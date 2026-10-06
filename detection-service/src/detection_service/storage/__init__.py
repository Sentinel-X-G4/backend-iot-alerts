from __future__ import annotations

from ..config import Settings
from .base import FeatureWindowRow, PredictionRow, RecordingSession, Storage
from .memory import MemoryStorage
from .writer import BatchWriter

__all__ = [
    "BatchWriter",
    "FeatureWindowRow",
    "MemoryStorage",
    "PredictionRow",
    "RecordingSession",
    "Storage",
    "create_storage",
]


def create_storage(settings: Settings) -> Storage:
    if not settings.database_url:
        return MemoryStorage(max_rows=1000)
    from .sql import SqlStorage

    return SqlStorage(settings.database_url, schema=settings.db_schema)
