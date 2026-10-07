"""Interface de stockage, indépendante de la base utilisée."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..alerts import AlertRow
from ..schemas import CameraEvent, SensorReading


@dataclass(slots=True)
class FeatureWindowRow:
    device_id: str
    window_end: float
    features: dict[str, float | None]
    session_id: uuid.UUID | None = None
    label: str | None = None


@dataclass(slots=True)
class PredictionRow:
    device_id: str
    window_end: float
    status: str
    device_state: str
    reason: str
    alerts: list[dict[str, Any]]
    metrics: dict[str, float | None]
    model_version: str


@dataclass(slots=True)
class RecordingSession:
    label: str
    device_id: str
    started_at: float
    notes: str | None = None
    ended_at: float | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


class Storage(ABC):
    """Toutes les méthodes peuvent lever une exception : l'appelant (BatchWriter) gère les reprises."""

    async def connect(self) -> None:  # noqa: B027 — optionnel
        pass

    async def close(self) -> None:  # noqa: B027
        pass

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def insert_sensor_readings(self, rows: Sequence[SensorReading]) -> None: ...

    @abstractmethod
    async def insert_camera_events(self, rows: Sequence[CameraEvent]) -> None: ...

    @abstractmethod
    async def upsert_camera_state(self, rows: Sequence[CameraEvent]) -> None:
        """Dernier état par caméra : seule la ligne la plus récente de chaque device_id compte."""

    @abstractmethod
    async def insert_feature_windows(self, rows: Sequence[FeatureWindowRow]) -> None: ...

    @abstractmethod
    async def insert_predictions(self, rows: Sequence[PredictionRow]) -> None: ...

    @abstractmethod
    async def insert_alerts(self, rows: Sequence[AlertRow]) -> None: ...

    @abstractmethod
    async def upsert_sessions(self, rows: Sequence[RecordingSession]) -> None: ...

    @abstractmethod
    def iter_labeled_windows(
        self, session_ids: Sequence[uuid.UUID] | None = None, device_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Fenêtres étiquetées, triées par session puis par temps (export du jeu d'entraînement)."""
