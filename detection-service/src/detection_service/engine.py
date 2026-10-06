"""Orchestration par appareil : tampons → features → predictor → post-traitement → sorties.

Le moteur est synchrone et sans I/O (les sorties passent par des files non bloquantes),
ce qui le rend testable avec une horloge simulée.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .baseline import GasBaseline
from .buffers import DeviceBuffers
from .config import Settings
from .features import compute_features, nan_to_none
from .postprocess import AlertResult, PostProcessor, global_status
from .predictors import Predictor
from .schemas import AlertOut, AlertPayload, CameraEvent, SensorReading, to_utc
from .storage import BatchWriter, FeatureWindowRow, PredictionRow, RecordingSession

log = logging.getLogger(__name__)


@dataclass
class TickResult:
    device_id: str
    window_end: float
    device_state: str
    status: str
    alerts: list[AlertResult]
    features: dict[str, float]

    def key(self) -> tuple:
        """Ce qui définit un « changement d'état » à notifier."""
        return self.device_state, self.status, tuple((a.type, a.active, a.source) for a in self.alerts)


@dataclass
class DevicePipeline:
    device_id: str
    settings: Settings
    buffers: DeviceBuffers = field(default_factory=DeviceBuffers)
    first_sensor_at: float | None = None
    last_sensor_at: float | None = None
    last_camera_at: float | None = None
    device_warmup: bool = False
    last_result: TickResult | None = None
    last_payload: AlertPayload | None = None
    last_sent_at: float | None = None
    recording: RecordingSession | None = None

    def __post_init__(self) -> None:
        s = self.settings
        self.baseline = GasBaseline(s.baseline_tau_s, s.baseline_init_ticks)
        self.post = PostProcessor(s)

    def add_sensor(self, r: SensorReading) -> None:
        if self.first_sensor_at is None:
            self.first_sensor_at = r.received_at
        self.last_sensor_at = r.received_at
        self.device_warmup = r.warmup
        self.buffers.add_sensor(r)

    def add_camera(self, e: CameraEvent) -> None:
        self.last_camera_at = e.received_at
        self.buffers.add_camera(e)

    def _state(self, now: float, features: dict[str, float]) -> str:
        s = self.settings
        if self.last_sensor_at is None:
            return "no_data"
        if now - self.last_sensor_at > s.stale_after_s:
            return "stale"
        if self.device_warmup or now - (self.first_sensor_at or now) < s.warmup_seconds:
            return "warming_up"
        if features["n_samples_short"] < s.min_samples_short:
            return "no_data"
        return "ok"

    def tick(self, now: float, predictor: Predictor, on_predict_error: Callable[[Exception], None]) -> TickResult:
        s = self.settings
        self.buffers.prune(now, s.short_window_s, s.long_window_s, s.dht_max_age_s, s.camera_hold_s)
        window = self.buffers.window(
            now, self.baseline.value, short_s=s.short_window_s, long_s=s.long_window_s,
            dht_max_age_s=s.dht_max_age_s, camera_hold_s=s.camera_hold_s,
        )
        features = compute_features(window)
        state = self._state(now, features)

        if state == "ok":
            # Baseline mise à jour avant la prédiction suivante, gelée si alerte gaz/feu.
            self.baseline.update(now, features["gas_mean"], allowed=not self.post.gas_alert_active)
            if not self.baseline.ready:
                state = "warming_up"  # initialisation de la baseline (BASELINE_INIT_TICKS ticks)

        if state == "ok":
            try:
                probs = predictor.predict(features)
            except Exception as exc:  # le filet de sécurité gaz doit fonctionner même si le modèle plante
                on_predict_error(exc)
                probs = {}
            alerts = self.post.update(probs, features, now)
        elif state == "warming_up":
            alerts = [AlertResult(a.type, False, 0.0, None, "model") for a in self.post.results]
        else:
            alerts = self.post.results  # no_data / stale : dernier état connu, figé

        result = TickResult(self.device_id, now, state, global_status(alerts), alerts, features)
        self.last_result = result
        return result


class DetectionEngine:
    def __init__(
        self,
        settings: Settings,
        predictor: Predictor,
        writer: BatchWriter | None = None,
        enqueue_payload: Callable[[AlertPayload], object] | None = None,
    ) -> None:
        self.settings = settings
        self.predictor = predictor
        self.writer = writer
        self.enqueue_payload = enqueue_payload or (lambda _p: None)
        self.devices: dict[str, DevicePipeline] = {}
        self.predict_errors = 0
        self.last_tick_at: float | None = None

    # ------------------------------------------------------------ entrées
    def device(self, device_id: str) -> DevicePipeline:
        dev = self.devices.get(device_id)
        if dev is None:
            dev = self.devices[device_id] = DevicePipeline(device_id, self.settings)
            log.info("nouvel appareil", extra={"device_id": device_id})
        return dev

    def on_sensor(self, r: SensorReading) -> None:
        self.device(r.device_id).add_sensor(r)
        if self.writer:
            self.writer.put("sensor_readings", r)

    def on_camera(self, e: CameraEvent) -> None:
        self.device(e.device_id).add_camera(e)
        if self.writer:
            self.writer.put("camera_events", e)

    def set_predictor(self, predictor: Predictor) -> None:
        self.predictor = predictor

    # ------------------------------------------------------- enregistrement
    def start_recording(self, device_id: str, label: str, notes: str | None, now: float | None = None) -> RecordingSession:
        dev = self.device(device_id)
        now = time.time() if now is None else now
        if dev.recording is not None:
            self.stop_recording(device_id, now)
        dev.recording = RecordingSession(label=label, device_id=device_id, started_at=now, notes=notes)
        if self.writer:
            self.writer.put("sessions", dev.recording)
        log.info("enregistrement démarré", extra={"device_id": device_id, "label": label,
                                                    "session_id": str(dev.recording.id)})
        return dev.recording

    def stop_recording(self, device_id: str | None = None, now: float | None = None) -> list[RecordingSession]:
        now = time.time() if now is None else now
        stopped = []
        for dev in self.devices.values():
            if dev.recording is not None and (device_id is None or dev.device_id == device_id):
                dev.recording.ended_at = now
                if self.writer:
                    self.writer.put("sessions", dev.recording)
                stopped.append(dev.recording)
                log.info("enregistrement arrêté", extra={"device_id": dev.device_id,
                                                          "session_id": str(dev.recording.id)})
                dev.recording = None
        return stopped

    # ---------------------------------------------------------------- tick
    def _on_predict_error(self, exc: Exception) -> None:
        self.predict_errors += 1
        if self.predict_errors == 1 or self.predict_errors % 100 == 0:
            log.error("erreur du predictor", extra={"error": repr(exc), "count": self.predict_errors})

    def tick(self, now: float | None = None) -> list[AlertPayload]:
        """Une inférence pour chaque appareil. Retourne les payloads émis (pour les tests)."""
        now = time.time() if now is None else now
        self.last_tick_at = now
        emitted = []
        for dev in list(self.devices.values()):
            previous = dev.last_result
            result = dev.tick(now, self.predictor, self._on_predict_error)
            self._store_window(dev, result)
            changed = previous is None or previous.key() != result.key()
            heartbeat_due = dev.last_sent_at is None or now - dev.last_sent_at >= self.settings.heartbeat_interval_s
            if changed or heartbeat_due:
                payload = self.build_payload(result, "state_change" if changed else "heartbeat")
                dev.last_payload = payload
                dev.last_sent_at = now
                self.enqueue_payload(payload)
                if self.writer:
                    self.writer.put("predictions", PredictionRow(
                        result.device_id, now, result.status, result.device_state, payload.reason,
                        [a.model_dump(mode="json") for a in payload.alerts], payload.metrics,
                        payload.model_version,
                    ))
                if changed:
                    log.info("changement d'état", extra={"device_id": dev.device_id, "status": result.status,
                                                         "device_state": result.device_state})
                emitted.append(payload)
        return emitted

    def _store_window(self, dev: DevicePipeline, result: TickResult) -> None:
        if not self.writer or result.device_state != "ok":
            return
        if dev.recording is None and not self.settings.store_all_feature_windows:
            return
        rec = dev.recording
        self.writer.put("feature_windows", FeatureWindowRow(
            dev.device_id, result.window_end, nan_to_none(result.features),
            rec.id if rec else None, rec.label if rec else None,
        ))

    def build_payload(self, result: TickResult, reason: str) -> AlertPayload:
        return AlertPayload(
            device_id=result.device_id,
            timestamp=to_utc(result.window_end),
            status=result.status,
            device_state=result.device_state,
            reason=reason,
            alerts=[
                AlertOut(type=a.type, active=a.active, confidence=min(1.0, max(0.0, a.confidence)),
                         since=to_utc(a.since) if a.since is not None else None, source=a.source)
                for a in result.alerts
            ],
            metrics={k: (None if math.isnan(v) else round(v, 4)) for k, v in result.features.items()},
            model_version=self.predictor.version,
        )
