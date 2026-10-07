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
