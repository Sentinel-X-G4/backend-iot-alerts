"""Abonnement MQTT, validation des messages et dispatch.

Reconnexion automatique avec backoff exponentiel ; les abonnements sont refaits
à chaque connexion. Un message invalide est journalisé puis ignoré.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import aiomqtt
from pydantic import ValidationError

from .config import Settings
from .schemas import CameraEvent, CameraMessage, SensorMessage, SensorReading

log = logging.getLogger(__name__)

SensorHandler = Callable[[SensorReading], None]
CameraHandler = Callable[[CameraEvent], None]


class TopicPattern:
    """Motif du type `maison/{device_id}/capteurs` → filtre MQTT + extraction du device_id."""

    def __init__(self, pattern: str) -> None:
        if pattern.count("{device_id}") != 1:
            raise ValueError(f"Le motif de topic doit contenir exactement un {{device_id}} : {pattern!r}")
        self.pattern = pattern
        self.filter = pattern.replace("{device_id}", "+")
        prefix, suffix = pattern.split("{device_id}")
        self._regex = re.compile("^" + re.escape(prefix) + r"([^/]+)" + re.escape(suffix) + "$")

    def device_id(self, topic: str) -> str | None:
        m = self._regex.match(topic)
        return m.group(1) if m else None


@dataclass
class MqttStats:
    connected: bool = False
    received: int = 0
    invalid: int = 0
    last_message_at: float | None = None
    connections: int = 0
    last_error: str | None = None
    invalid_by_reason: dict[str, int] = field(default_factory=dict)


class MessageDispatcher:
    """Décode et valide un message ; indépendant du client MQTT (testable)."""

    def __init__(self, settings: Settings, on_sensor: SensorHandler, on_camera: CameraHandler) -> None:
        self.sensor_topic = TopicPattern(settings.mqtt_sensor_topic)
        self.camera_topic = TopicPattern(settings.mqtt_camera_topic)
        self.on_sensor = on_sensor
        self.on_camera = on_camera
        self.stats = MqttStats()

    def _invalid(self, topic: str, reason: str, detail: object) -> None:
        self.stats.invalid += 1
        self.stats.invalid_by_reason[reason] = self.stats.invalid_by_reason.get(reason, 0) + 1
        log.warning("message MQTT ignoré", extra={"topic": topic, "reason": reason, "detail": str(detail)[:300]})

    def dispatch(self, topic: str, payload: bytes, received_at: float | None = None) -> bool:
        """Retourne True si le message a été accepté. Ne lève jamais d'exception."""
        received_at = time.time() if received_at is None else received_at
        self.stats.received += 1
        self.stats.last_message_at = received_at
        try:
            if (device_id := self.sensor_topic.device_id(topic)) is not None:
                sensor = SensorMessage.model_validate(json.loads(payload))
                self.on_sensor(SensorReading.from_message(device_id, received_at, sensor))
                return True
            if (device_id := self.camera_topic.device_id(topic)) is not None:
                cam = CameraMessage.model_validate(json.loads(payload))
                self.on_camera(CameraEvent(device_id, received_at, cam.ts, cam.person))
                return True
            self._invalid(topic, "unknown_topic", "")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._invalid(topic, "bad_json", exc)
        except ValidationError as exc:
            self._invalid(topic, "validation", exc.errors(include_url=False))
        except Exception as exc:  # un handler aval ne doit jamais tuer la boucle MQTT
            log.exception("erreur pendant le traitement d'un message", extra={"topic": topic})
            self._invalid(topic, "handler_error", exc)
        return False


def _tls_params(settings: Settings) -> aiomqtt.TLSParameters | None:
    if not settings.mqtt_tls:
        return None
    return aiomqtt.TLSParameters(
        ca_certs=settings.mqtt_tls_ca_certs,
        cert_reqs=ssl.CERT_NONE if settings.mqtt_tls_insecure else ssl.CERT_REQUIRED,
    )


async def run_mqtt(settings: Settings, dispatcher: MessageDispatcher) -> None:
    """Boucle infinie : connexion, abonnement, réception ; reconnexion sur erreur."""
    delay = settings.mqtt_reconnect_min_s
    stats = dispatcher.stats
    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_username,
                password=settings.mqtt_password,
                identifier=settings.mqtt_client_id,
                tls_params=_tls_params(settings),
                tls_insecure=settings.mqtt_tls_insecure if settings.mqtt_tls else None,
                keepalive=30,
            ) as client:
                for topic in (dispatcher.sensor_topic.filter, dispatcher.camera_topic.filter):
                    await client.subscribe(topic, qos=settings.mqtt_qos)
                stats.connected = True
                stats.connections += 1
                stats.last_error = None
                delay = settings.mqtt_reconnect_min_s
                log.info(
                    "MQTT connecté",
                    extra={"host": settings.mqtt_host, "port": settings.mqtt_port,
                           "topics": [dispatcher.sensor_topic.filter, dispatcher.camera_topic.filter]},
                )
                async for message in client.messages:
                    payload = message.payload if isinstance(message.payload, bytes) else str(message.payload).encode()
                    dispatcher.dispatch(str(message.topic), payload)
        except aiomqtt.MqttError as exc:
            stats.connected = False
            stats.last_error = str(exc)
            log.warning("MQTT déconnecté, nouvelle tentative", extra={"error": str(exc), "retry_in_s": delay})
            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.mqtt_reconnect_max_s)
        except asyncio.CancelledError:
            stats.connected = False
            raise
