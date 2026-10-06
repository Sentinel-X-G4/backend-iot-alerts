"""Envoi des résultats au backend : file bornée + worker HTTP asynchrone (httpx).

=============================================================================
TODO BACKEND : la route de réception n'existe pas encore côté backend.
Elle doit accepter :

    POST {BACKEND_URL}{BACKEND_ALERT_ROUTE}        (défaut : /api/alerts)
    Authorization: Bearer {BACKEND_TOKEN}          (si BACKEND_TOKEN est défini)
    Content-Type: application/json
    Corps : schemas.AlertPayload (contrat complet : docs/BACKEND_CONTRACT.md,
            JSON Schema : docs/schemas/alert_payload.schema.json)

et répondre 2xx (200/201/202/204) rapidement, sans traitement long dans la requête.
Codes interprétés par ce client :
    2xx            → envoyé
    408, 429, 5xx  → réessayé avec backoff exponentiel
    autre 4xx      → abandonné (erreur de contrat, journalisée)
Non utilisé dans la pile Sentinel-X : backend-api lit les résultats en base.
=============================================================================

Ordre et priorité :
- Un seul worker : les messages d'un appareil partent dans l'ordre.
- `reason="state_change"` est prioritaire sur `reason="heartbeat"` : file pleine →
  on jette d'abord des heartbeats ; un heartbeat déjà en file pour le même appareil est
  remplacé par le plus récent.
- L'envoi ne bloque jamais la réception MQTT ni l'inférence (`enqueue` est synchrone et O(n) borné).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass

import httpx

from .config import Settings
from .schemas import AlertPayload

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429}


@dataclass
class SenderStats:
    sent: int = 0
    failed: int = 0
    dropped_heartbeats: int = 0
    dropped_changes: int = 0
    replaced_heartbeats: int = 0
    last_error: str | None = None
    last_success_at: float | None = None


class BackendSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.url = (settings.backend_url.rstrip("/") + settings.backend_alert_route) if settings.backend_url else None
        self.queue: deque[AlertPayload] = deque()
        self.stats = SenderStats()
        self._client = client
        self._wake = asyncio.Event()
        self._sleep = asyncio.sleep  # remplaçable dans les tests

    @property
    def enabled(self) -> bool:
        return self.url is not None

    # --------------------------------------------------------------- file
    def enqueue(self, payload: AlertPayload) -> bool:
        """Ne bloque jamais. Retourne False si le message a été jeté."""
        q = self.queue
        if payload.reason == "heartbeat":
            for i, queued in enumerate(q):
                if queued.reason == "heartbeat" and queued.device_id == payload.device_id:
                    q[i] = payload
                    self.stats.replaced_heartbeats += 1
                    self._wake.set()
                    return True
            if len(q) >= self.settings.backend_queue_size:
                self.stats.dropped_heartbeats += 1
                return False
        elif len(q) >= self.settings.backend_queue_size:
            victim = next((x for x in q if x.reason == "heartbeat"), None)
            if victim is not None:
                q.remove(victim)
                self.stats.dropped_heartbeats += 1
            else:
                q.popleft()
                self.stats.dropped_changes += 1
                log.error("file backend pleine : changement d'état le plus ancien jeté",
                          extra={"dropped_changes": self.stats.dropped_changes})
        q.append(payload)
        self._wake.set()
        return True

    # -------------------------------------------------------------- envoi
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.backend_token:
            headers["Authorization"] = f"Bearer {self.settings.backend_token}"
        return headers

    def _backoff(self, attempt: int) -> float:
        s = self.settings
        delay = min(s.backend_backoff_max_s, s.backend_backoff_base_s * (2 ** attempt))
        return delay * (0.5 + random.random() / 2)  # jitter

    async def send(self, payload: AlertPayload) -> bool:
        """Un envoi avec reprises. Retourne True si le backend a accepté."""
        assert self._client is not None and self.url is not None
        body = payload.model_dump_json()
        for attempt in range(self.settings.backend_max_retries + 1):
            try:
                resp = await self._client.post(self.url, content=body, headers=self._headers())
                if resp.is_success:
                    self.stats.sent += 1
                    self.stats.last_success_at = time.time()
                    return True
                self.stats.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code < 500 and resp.status_code not in RETRYABLE_STATUS:
                    log.error("backend a refusé le payload (pas de nouvel essai)",
                              extra={"status": resp.status_code, "body": resp.text[:300]})
                    break
            except httpx.HTTPError as exc:
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.settings.backend_max_retries:
                await self._sleep(self._backoff(attempt))
        self.stats.failed += 1
        log.warning("envoi backend abandonné", extra={"device_id": payload.device_id, "reason": payload.reason,
                                                       "error": self.stats.last_error})
        return False

    async def run(self) -> None:
        if not self.enabled:
            log.warning("BACKEND_URL non défini : les résultats ne sont pas envoyés (journalisés en DEBUG)")
        owns_client = self._client is None
        if owns_client and self.enabled:
            self._client = httpx.AsyncClient(timeout=self.settings.backend_timeout_s)
        try:
            while True:
                while not self.queue:
                    self._wake.clear()
                    await self._wake.wait()
                payload = self.queue.popleft()
                if self.enabled:
                    await self.send(payload)
                else:
                    log.debug("payload (non envoyé)", extra={"payload": payload.model_dump_json()})
        finally:
            if owns_client and self._client is not None:
                await self._client.aclose()

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Arrêt propre : tente d'envoyer ce qui reste, une seule tentative par message."""
        if not self.enabled or not self.queue:
            return
        async with httpx.AsyncClient(timeout=self.settings.backend_timeout_s) as client:
            deadline = time.monotonic() + timeout_s
            while self.queue and time.monotonic() < deadline:
                payload = self.queue.popleft()
                try:
                    await client.post(self.url, content=payload.model_dump_json(), headers=self._headers())  # type: ignore[arg-type]
                except httpx.HTTPError as exc:
                    log.warning("envoi final en échec", extra={"error": str(exc)})
        if self.queue:
            log.warning("messages backend non envoyés à l'arrêt", extra={"count": len(self.queue)})
