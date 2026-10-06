"""Écritures par lots, non bloquantes, tolérantes aux pannes de la base.

- `put_*` ne bloque jamais et ne lève jamais : on empile dans une file en mémoire.
- Une tâche vide les files toutes les `flush_interval_s` ou dès qu'une file atteint `batch_size`.
- En cas d'échec : le lot est remis en tête de file, reprise avec backoff exponentiel.
- La mémoire est bornée par `max_buffered_rows` : au-delà, on jette les lignes les plus
  anciennes des tables les moins importantes (mesures brutes d'abord, sessions jamais).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .base import Storage

log = logging.getLogger(__name__)

# Ordre d'écriture (les sessions avant les fenêtres qui les référencent)
# et ordre inverse de sacrifice quand la mémoire est pleine.
KINDS: tuple[str, ...] = ("sessions", "predictions", "feature_windows", "camera_events", "sensor_readings")
_DROP_ORDER: tuple[str, ...] = ("sensor_readings", "camera_events", "feature_windows", "predictions")


@dataclass
class WriterStats:
    written: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    dropped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    failures: int = 0
    last_error: str | None = None
    last_success_at: float | None = None
    healthy: bool = True


class BatchWriter:
    def __init__(
        self,
        storage: Storage,
        batch_size: int = 200,
        flush_interval_s: float = 1.0,
        max_buffered_rows: int = 50_000,
        backoff_max_s: float = 30.0,
    ) -> None:
        self.storage = storage
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.max_buffered_rows = max_buffered_rows
        self.backoff_max_s = backoff_max_s
        self.queues: dict[str, deque[Any]] = {k: deque() for k in KINDS}
        self.stats = WriterStats()
        self._wake = asyncio.Event()
        self._sinks: dict[str, Callable[[Sequence[Any]], Awaitable[None]]] = {
            "sessions": storage.upsert_sessions,
            "predictions": storage.insert_predictions,
            "feature_windows": storage.insert_feature_windows,
            "camera_events": storage.insert_camera_events,
            "sensor_readings": storage.insert_sensor_readings,
        }

    # ------------------------------------------------------------------ put
    @property
    def buffered(self) -> int:
        return sum(len(q) for q in self.queues.values())

    def put(self, kind: str, row: Any) -> None:
        self.queues[kind].append(row)
        if self.buffered > self.max_buffered_rows:
            self._shed()
        if len(self.queues[kind]) >= self.batch_size:
            self._wake.set()

    def _shed(self) -> None:
        for kind in _DROP_ORDER:
            q = self.queues[kind]
            if q:
                q.popleft()
                self.stats.dropped[kind] += 1
                if self.stats.dropped[kind] % 1000 == 1:
                    log.warning("file BDD pleine : lignes jetées", extra={"kind": kind,
                                                                         "dropped": self.stats.dropped[kind]})
                return

    # ---------------------------------------------------------------- flush
    async def flush_once(self) -> bool:
        """Écrit tout ce qui est en file, lot par lot. Retourne False au premier échec."""
        for kind in KINDS:
            q = self.queues[kind]
            while q:
                batch = [q.popleft() for _ in range(min(self.batch_size, len(q)))]
                try:
                    await self._sinks[kind](batch)
                except Exception as exc:
                    q.extendleft(reversed(batch))
                    self.stats.failures += 1
                    self.stats.last_error = f"{type(exc).__name__}: {exc}"
                    if self.stats.healthy:
                        log.error("écriture BDD en échec, reprise ultérieure",
                                  extra={"kind": kind, "error": self.stats.last_error})
                    self.stats.healthy = False
                    return False
                self.stats.written[kind] += len(batch)
        if not self.stats.healthy:
            log.info("écriture BDD rétablie", extra={"buffered": self.buffered})
        self.stats.healthy = True
        self.stats.last_success_at = time.time()
        return True

    async def run(self) -> None:
        delay = self.flush_interval_s
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._wake.clear()
            ok = await self.flush_once()
            delay = self.flush_interval_s if ok else min(max(delay * 2, 1.0), self.backoff_max_s)

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Arrêt propre : dernière tentative de vidage, bornée dans le temps."""
        try:
            await asyncio.wait_for(self.flush_once(), timeout=timeout_s)
        except TimeoutError:
            log.error("vidage BDD incomplet à l'arrêt", extra={"buffered": self.buffered})
        if self.buffered:
            log.warning("lignes non écrites à l'arrêt", extra={"buffered": self.buffered})
