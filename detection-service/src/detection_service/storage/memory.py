"""Stockage en mémoire : tests, et mode « sans base » (DATABASE_URL vide)."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..alerts import AlertRow
from ..schemas import CameraEvent, SensorReading
from .base import FeatureWindowRow, PredictionRow, RecordingSession, Storage


class MemoryStorage(Storage):
    """Garde au plus `max_rows` lignes par table (les plus anciennes sont jetées)."""

    def __init__(self, max_rows: int = 10_000) -> None:
        self.sensor_readings: deque[SensorReading] = deque(maxlen=max_rows)
        self.camera_events: deque[CameraEvent] = deque(maxlen=max_rows)
        self.camera_state: dict[str, CameraEvent] = {}
        self.feature_windows: deque[FeatureWindowRow] = deque(maxlen=max_rows)
        self.predictions: deque[PredictionRow] = deque(maxlen=max_rows)
        self.alerts: deque[AlertRow] = deque(maxlen=max_rows)
        self.sessions: dict[uuid.UUID, RecordingSession] = {}
        self.fail = False
        """Pour les tests : simule une panne de base."""

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("base indisponible (simulé)")

    async def ping(self) -> bool:
        self._check()
        return True

    async def insert_sensor_readings(self, rows: Sequence[SensorReading]) -> None:
        self._check()
        self.sensor_readings.extend(rows)

    async def insert_camera_events(self, rows: Sequence[CameraEvent]) -> None:
        self._check()
        self.camera_events.extend(rows)

    async def upsert_camera_state(self, rows: Sequence[CameraEvent]) -> None:
        self._check()
        self.camera_state.update((r.device_id, r) for r in rows)

    async def insert_feature_windows(self, rows: Sequence[FeatureWindowRow]) -> None:
        self._check()
        self.feature_windows.extend(rows)

    async def insert_predictions(self, rows: Sequence[PredictionRow]) -> None:
        self._check()
        self.predictions.extend(rows)

    async def insert_alerts(self, rows: Sequence[AlertRow]) -> None:
        self._check()
        self.alerts.extend(rows)

    async def upsert_sessions(self, rows: Sequence[RecordingSession]) -> None:
        self._check()
        for r in rows:
            self.sessions[r.id] = r

    async def iter_labeled_windows(
        self, session_ids: Sequence[uuid.UUID] | None = None, device_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        rows = [
            r for r in self.feature_windows
            if r.label is not None
            and (not session_ids or r.session_id in session_ids)
            and (device_id is None or r.device_id == device_id)
        ]
        rows.sort(key=lambda r: (str(r.session_id), r.window_end))
        for r in rows:
            yield {"device_id": r.device_id, "window_end": r.window_end, **r.features,
                   "session_id": r.session_id, "label": r.label}
