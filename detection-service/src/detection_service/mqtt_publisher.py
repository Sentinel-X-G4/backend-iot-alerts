"""Publication des résultats sur MQTT (défaut : `sentinelx/{device_id}/detection`).

Le corps publié est exactement le payload décrit dans docs/BACKEND_CONTRACT.md. La file
et ses priorités sont celles de BackendSender (heartbeats remplacés ou jetés en premier) ;
seul le transport change : les messages partent sur la connexion MQTT du service
(voir `run_mqtt(on_client=...)`). Hors connexion, l'envoi attend la reconnexion.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiomqtt

from .backend_client import BackendSender
from .config import Settings
from .mqtt_client import TopicPattern
from .schemas import AlertPayload

log = logging.getLogger(__name__)


class MqttResultPublisher(BackendSender):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        assert settings.mqtt_result_topic is not None
        self.topic = TopicPattern(settings.mqtt_result_topic)
        self.url = f"mqtt://{settings.mqtt_host}:{settings.mqtt_port}/{self.topic.pattern}"
        self._mqtt: aiomqtt.Client | None = None
        self._online = asyncio.Event()

    def bind(self, client: aiomqtt.Client | None) -> None:
        """Appelé par run_mqtt à chaque connexion (client) et déconnexion (None)."""
        self._mqtt = client
        if client is None:
            self._online.clear()
        else:
            self._online.set()

    async def send(self, payload: AlertPayload) -> bool:
        """Publie un résultat ; attend la connexion si besoin. Ne renonce pas."""
        topic = self.topic.topic(payload.device_id)
        body = payload.model_dump_json()
        while True:
            await self._online.wait()
            assert self._mqtt is not None
            try:
                await self._mqtt.publish(topic, body, qos=self.settings.mqtt_result_qos)
            except aiomqtt.MqttError as exc:
                self.stats.last_error = f"MqttError: {exc}"
                await self._sleep(self.settings.mqtt_reconnect_min_s)
                continue
            self.stats.sent += 1
            self.stats.last_success_at = time.time()
            return True

    async def run(self) -> None:
        while True:
            while not self.queue:
                self._wake.clear()
                await self._wake.wait()
            await self.send(self.queue.popleft())

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Arrêt propre : à appeler avant de couper la connexion MQTT."""
        try:
            async with asyncio.timeout(timeout_s):
                while self.queue and self._online.is_set():
                    await self.send(self.queue.popleft())
        except TimeoutError:
            pass
        if self.queue:
            log.warning("résultats non publiés à l'arrêt", extra={"count": len(self.queue)})
