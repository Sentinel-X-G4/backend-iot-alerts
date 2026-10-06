"""Stockage SQL via SQLAlchemy 2 async (PostgreSQL/asyncpg en production, SQLite en test)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..features import FEATURE_NAMES
from ..schemas import CameraEvent, SensorReading, to_utc
from . import tables as t
from .base import FeatureWindowRow, PredictionRow, RecordingSession, Storage


class SqlStorage(Storage):
    def __init__(self, url: str, schema: str | None = None, engine: AsyncEngine | None = None) -> None:
        self.url = url
        self.schema = schema or None
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("SqlStorage.connect() n'a pas été appelé")
        return self._engine

    async def connect(self) -> None:
        if self._engine is None:
            kwargs: dict[str, Any] = {"pool_pre_ping": True}
            if self.url.startswith("postgresql"):
                kwargs.update(pool_size=5, max_overflow=5)
            engine = create_async_engine(self.url, **kwargs)
            if self.schema:
                engine = engine.execution_options(schema_translate_map={None: self.schema})
            self._engine = engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def create_all(self) -> None:
        """Création directe des tables (tests / SQLite uniquement). PostgreSQL : schéma de l'infra."""
        async with self.engine.begin() as conn:
            await conn.run_sync(t.metadata.create_all)

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def _insert(self, table: Any, rows: list[dict[str, Any]]) -> None:
        if rows:
            async with self.engine.begin() as conn:
                await conn.execute(table.insert(), rows)

    async def insert_sensor_readings(self, rows: Sequence[SensorReading]) -> None:
        await self._insert(
            t.sensor_readings,
            [
                {
                    "device_id": r.device_id, "received_at": to_utc(r.received_at), "device_ts": r.device_ts,
                    "temp": r.temp, "hum": r.hum, "pir": r.pir, "gas_raw": r.gas_raw,
                    "gas_do": r.gas_do, "warmup": r.warmup,
                }
                for r in rows
            ],
        )

    async def insert_camera_events(self, rows: Sequence[CameraEvent]) -> None:
        await self._insert(
            t.camera_events,
            [
                {"device_id": r.device_id, "received_at": to_utc(r.received_at),
                 "device_ts": r.device_ts, "person": r.person}
                for r in rows
            ],
        )

    async def insert_feature_windows(self, rows: Sequence[FeatureWindowRow]) -> None:
        await self._insert(
            t.feature_windows,
            [
                {
                    "device_id": r.device_id, "window_end": to_utc(r.window_end),
                    **{name: r.features.get(name) for name in FEATURE_NAMES},
                    "session_id": r.session_id, "label": r.label,
                }
                for r in rows
            ],
        )

    async def insert_predictions(self, rows: Sequence[PredictionRow]) -> None:
        await self._insert(
            t.predictions,
            [{**asdict(r), "window_end": to_utc(r.window_end)} for r in rows],
        )

    async def upsert_sessions(self, rows: Sequence[RecordingSession]) -> None:
        if not rows:
            return
        if self.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        async with self.engine.begin() as conn:
            for r in rows:
                values = {
                    "id": r.id, "device_id": r.device_id, "label": r.label, "notes": r.notes,
                    "started_at": to_utc(r.started_at),
                    "ended_at": to_utc(r.ended_at) if r.ended_at is not None else None,
                }
                stmt = insert(t.recording_sessions).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"], set_={"ended_at": stmt.excluded.ended_at, "notes": stmt.excluded.notes}
                )
                await conn.execute(stmt)

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            result = await conn.execute(select(t.recording_sessions).order_by(t.recording_sessions.c.started_at))
            return [dict(row._mapping) for row in result]

    async def iter_labeled_windows(
        self, session_ids: Sequence[uuid.UUID] | None = None, device_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        fw = t.feature_windows
        query = select(fw).where(fw.c.label.is_not(None)).order_by(fw.c.session_id, fw.c.window_end)
        if session_ids:
            query = query.where(fw.c.session_id.in_(list(session_ids)))
        if device_id:
            query = query.where(fw.c.device_id == device_id)
        async with self.engine.connect() as conn:
            result = await conn.stream(query.execution_options(yield_per=2000))
            async for row in result:
                yield dict(row._mapping)
