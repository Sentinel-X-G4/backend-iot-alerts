"""Assemblage des composants et cycle de vie du service (démarrage, tâches, arrêt propre)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

from .backend_client import BackendSender
from .config import Settings
from .engine import DetectionEngine
from .mqtt_client import MessageDispatcher, run_mqtt
from .mqtt_publisher import MqttResultPublisher
from .predictors import ModelLoadError, Predictor, create_predictor
from .storage import BatchWriter, MemoryStorage, create_storage

log = logging.getLogger(__name__)


class Service:
    def __init__(self, settings: Settings, predictor: Predictor | None = None) -> None:
        self.settings = settings
        self.started_at = time.time()
        self.storage = create_storage(settings)
        self.db_enabled = not isinstance(self.storage, MemoryStorage)
        self.db_ready = not self.db_enabled
        self.writer = BatchWriter(
            self.storage, settings.db_batch_size, settings.db_flush_interval_s, settings.db_max_buffered_rows
        )
        self.sender = MqttResultPublisher(settings) if settings.mqtt_result_topic else BackendSender(settings)
        # Échoue au démarrage avec un message clair si le modèle est incompatible.
        self.engine = DetectionEngine(settings, predictor or create_predictor(settings), self.writer,
                                      self.sender.enqueue)
        self.dispatcher = MessageDispatcher(settings, self.engine.on_sensor, self.engine.on_camera)
        self.model_loaded_at = time.time()
        self.model_reload_error: str | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------- modèle
    def reload_model(self) -> dict[str, object]:
        """Recharge le modèle depuis la config courante (env relue). Garde l'ancien en cas d'échec."""
        try:
            predictor = create_predictor(Settings())
        except ModelLoadError as exc:
            self.model_reload_error = str(exc)
            log.error("rechargement du modèle refusé, l'ancien est conservé", extra={"error": str(exc)})
            raise
        self.engine.set_predictor(predictor)
        self.model_loaded_at = time.time()
        self.model_reload_error = None
        log.info("modèle rechargé", extra=predictor.describe())
        return predictor.describe()

    # -------------------------------------------------------------- tâches
    async def _ticker(self) -> None:
        loop = asyncio.get_running_loop()
        interval = self.settings.inference_interval_s
        next_at = loop.time()
        while True:
            next_at += interval
            try:
                self.engine.tick()
            except Exception:
                log.exception("erreur pendant l'inférence")
            delay = next_at - loop.time()
            if delay < 0:  # retard : on ne rattrape pas les ticks manqués
                next_at = loop.time()
                delay = 0
            await asyncio.sleep(delay)

    async def _db_bootstrap(self) -> None:
        """Connexion avec reprises : une base absente ne bloque pas l'inférence.

        Le schéma n'est pas créé ici : il est défini dans sentinel-x-g4/infra/postgres/init.
        """
        delay = 1.0
        while True:
            try:
                await self.storage.connect()
                await self.storage.ping()
                self.db_ready = True
                log.info("base de données prête", extra={"schema": self.settings.db_schema})
                return
            except Exception as exc:
                log.warning("base indisponible, nouvel essai", extra={"error": str(exc)[:300], "retry_in_s": delay})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _api(self) -> None:
        import uvicorn

        from .api import create_app

        config = uvicorn.Config(create_app(self), host=self.settings.api_host, port=self.settings.api_port,
                                log_config=None, access_log=False)
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        await server.serve()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.request_stop)
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(signal.SIGHUP, self._reload_from_signal)

        log.info("démarrage", extra={"predictor": self.engine.predictor.describe(),
                                     "backend": self.sender.url, "database": self.db_enabled})
        if self.db_enabled:
            db_task = asyncio.create_task(self._db_bootstrap(), name="db-bootstrap")
        else:
            db_task = None
            log.warning("DATABASE_URL non défini : rien n'est persisté")

        on_client = self.sender.bind if isinstance(self.sender, MqttResultPublisher) else None
        mqtt_task = asyncio.create_task(run_mqtt(self.settings, self.dispatcher, on_client), name="mqtt")
        tasks = [
            asyncio.create_task(self._ticker(), name="ticker"),
            asyncio.create_task(self._api(), name="api"),
        ]
        writer_task = asyncio.create_task(self._writer_when_ready(), name="db-writer")
        sender_task = asyncio.create_task(self.sender.run(), name="backend-sender")

        await self._stop.wait()
        log.info("arrêt demandé : vidage des files")
        for task in [*tasks, *( [db_task] if db_task else [])]:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sender_task.cancel()
        writer_task.cancel()
        await asyncio.gather(sender_task, writer_task, return_exceptions=True)
        await self.sender.drain()  # avant de couper MQTT : les résultats peuvent y partir
        mqtt_task.cancel()
        await asyncio.gather(mqtt_task, return_exceptions=True)
        if self.db_ready:
            await self.writer.drain()
        await self.storage.close()
        log.info("arrêt terminé")

    async def _writer_when_ready(self) -> None:
        while not self.db_ready:
            await asyncio.sleep(0.5)
        await self.writer.run()

    def _reload_from_signal(self) -> None:
        with contextlib.suppress(ModelLoadError):
            self.reload_model()

