"""Commandes vers les ESP (`sentinelx/{device_id}/cmd`) et leurs acquittements (`.../ack`).

Le service est le seul client MQTT côté serveur : backend-api relaie les commandes du dashboard
vers POST /devices/{device_id}/... (api.py), qui publie ici et attend l'acquittement de même id.
Les messages partent sur la connexion MQTT du service (voir `run_mqtt(on_client=...)`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import aiomqtt

from .config import Settings
from .mqtt_client import TopicPattern
from .schemas import CommandAck

log = logging.getLogger(__name__)


class BrokerUnavailable(Exception):
    """Pas de connexion MQTT : la commande n'est pas publiée."""


class CommandSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.topic = TopicPattern(settings.mqtt_command_topic)
        self._mqtt: aiomqtt.Client | None = None
        self._pending: dict[str, tuple[str, asyncio.Future[CommandAck]]] = {}

    def bind(self, client: aiomqtt.Client | None) -> None:
        """Appelé par run_mqtt à chaque connexion (client) et déconnexion (None)."""
        self._mqtt = client

    def on_ack(self, device_id: str, ack: CommandAck) -> None:
        waiting = self._pending.get(ack.id)
        # Un appareil ne peut acquitter que ses propres commandes
        if waiting is None or waiting[0] != device_id or waiting[1].done():
            log.debug("acquittement sans commande en attente", extra={"device_id": device_id, "id": ack.id})
            return
        waiting[1].set_result(ack)

    async def send(self, device_id: str, command: dict[str, Any]) -> CommandAck | None:
        """Publie la commande et attend l'acquittement ; None si aucun dans COMMAND_ACK_TIMEOUT_S."""
        if self._mqtt is None:
            raise BrokerUnavailable
        command_id = str(uuid.uuid4())
        future: asyncio.Future[CommandAck] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = (device_id, future)
        try:
            try:
                await self._mqtt.publish(self.topic.topic(device_id), json.dumps({"id": command_id, **command}), qos=1)
            except aiomqtt.MqttError as exc:
                raise BrokerUnavailable from exc
            async with asyncio.timeout(self.settings.command_ack_timeout_s):
                ack = await future
        except TimeoutError:
            ack = None
        finally:
            self._pending.pop(command_id, None)
        log.info("commande ESP", extra={"device_id": device_id, "command": command.get("command"),
                                        "state": command.get("state"),
                                        "result": "timeout" if ack is None else ("ok" if ack.ok else ack.error)})
        return ack


ALARM_TEXT = {"feu": "INCENDIE DETECTE", "fuite_gaz": "FUITE DE GAZ DETECTEE",
              "inondation": "INONDATION DETECTEE", "presence": "PRESENCE DETECTEE"}
"""Texte de l'écran de l'ESP pendant l'alarme (ASCII, 100 caractères au plus)."""


class EspAlarm:
    """Répercute les alertes détectées sur l'alarme de l'ESP (buzzer, LED rouge, écran).

    `set()` est appelé par le moteur (synchrone) ; un worker envoie les commandes dans l'ordre et
    réessaie toutes les ESP_ALARM_RETRY_S tant que l'ESP n'a pas acquitté l'état voulu.
    """

    def __init__(self, settings: Settings, commands: CommandSender) -> None:
        self.settings = settings
        self.commands = commands
        self.wanted: dict[str, str] = {}
        self.applied: dict[str, str] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def set(self, device_id: str, alert: str) -> None:
        self.wanted[device_id] = alert
        self._queue.put_nowait(device_id)

    def _commands_for(self, alert: str) -> list[dict[str, Any]]:
        if alert == "aucune":
            return [{"command": "alert", "state": "off"}, {"command": "screen", "state": "auto"}]
        return [{"command": "alert", "state": "on"},
                {"command": "screen", "state": "message", "text": ALARM_TEXT.get(alert, alert.upper())[:100]}]

    async def _apply(self, device_id: str, alert: str) -> bool:
        for command in self._commands_for(alert):
            try:
                ack = await self.commands.send(device_id, command)
            except BrokerUnavailable:
                return False
            if ack is None or not ack.ok:
                return False
        return True

    async def _retry_later(self, device_id: str) -> None:
        await asyncio.sleep(self.settings.esp_alarm_retry_s)
        self._queue.put_nowait(device_id)

    async def run(self) -> None:
        retries: set[asyncio.Task[None]] = set()
        while True:
            device_id = await self._queue.get()
            alert = self.wanted.get(device_id)
            if alert is None or self.applied.get(device_id) == alert:
                continue
            if await self._apply(device_id, alert):
                self.applied[device_id] = alert
                log.info("alarme ESP", extra={"device_id": device_id, "alert": alert})
            elif self.wanted.get(device_id) == alert:
                task = asyncio.create_task(self._retry_later(device_id))
                retries.add(task)
                task.add_done_callback(retries.discard)
