"""Tables SQLAlchemy Core (sans schéma : il est appliqué via schema_translate_map).

Toute modification ici doit s'accompagner d'une migration Alembic (migrations/versions).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from ..features import FEATURE_NAMES

metadata = MetaData()

_BigId = BigInteger().with_variant(Integer, "sqlite")
_Json = JSON().with_variant(JSONB(), "postgresql")
_Ts = DateTime(timezone=True)

sensor_readings = Table(
    "sensor_readings",
    metadata,
    Column("id", _BigId, primary_key=True, autoincrement=True),
    Column("device_id", String(64), nullable=False),
    Column("received_at", _Ts, nullable=False),
    Column("device_ts", BigInteger),
    Column("temp", Float),
    Column("hum", Float),
    Column("pir", Boolean, nullable=False),
    Column("gas_raw", SmallInteger, nullable=False),
    Column("gas_do", Boolean),
    Column("warmup", Boolean, nullable=False),
    Index("ix_sensor_readings_device_received", "device_id", "received_at"),
    Index("ix_sensor_readings_received_at", "received_at"),
)

camera_events = Table(
    "camera_events",
    metadata,
    Column("id", _BigId, primary_key=True, autoincrement=True),
    Column("device_id", String(64), nullable=False),
    Column("received_at", _Ts, nullable=False),
    Column("device_ts", BigInteger),
    Column("person", Boolean, nullable=False),
    Index("ix_camera_events_device_received", "device_id", "received_at"),
)

feature_windows = Table(
    "feature_windows",
    metadata,
    Column("device_id", String(64), primary_key=True),
    Column("window_end", _Ts, primary_key=True),
    *(Column(name, Float) for name in FEATURE_NAMES),
    Column("session_id", Uuid),
    Column("label", String(64)),
    Index("ix_feature_windows_session", "session_id"),
)

predictions = Table(
    "predictions",
    metadata,
    Column("id", _BigId, primary_key=True, autoincrement=True),
    Column("device_id", String(64), nullable=False),
    Column("window_end", _Ts, nullable=False),
    Column("status", String(16), nullable=False),
    Column("device_state", String(16), nullable=False),
    Column("reason", String(16), nullable=False),
    Column("alerts", _Json, nullable=False),
    Column("metrics", _Json, nullable=False),
    Column("model_version", String(128), nullable=False),
    Index("ix_predictions_device_window", "device_id", "window_end"),
)

recording_sessions = Table(
    "recording_sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("device_id", String(64), nullable=False),
    Column("label", String(64), nullable=False),
    Column("started_at", _Ts, nullable=False),
    Column("ended_at", _Ts),
    Column("notes", Text),
)
