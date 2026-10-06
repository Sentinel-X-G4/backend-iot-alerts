"""Configuration via variables d'environnement / fichier .env (pydantic-settings)."""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ALERT_TYPES: tuple[str, ...] = ("feu", "fuite_gaz", "presence")
"""Types d'alerte, du plus prioritaire au moins prioritaire."""


class ModelMode(str, Enum):
    MULTILABEL = "multilabel"
    MULTICLASS = "multiclass"


class PredictorKind(str, Enum):
    RULES = "rules"
    ORANGE = "orange"
    SKLEARN = "sklearn"


class AlertParams(BaseModel):
    """Paramètres d'hystérésis d'un type d'alerte."""

    threshold_on: float = 0.6
    threshold_off: float = 0.4
    k_on: int = Field(3, ge=1)
    m_off: int = Field(6, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- MQTT -----------------------------------------------------------
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = False
    mqtt_tls_ca_certs: str | None = None
    mqtt_tls_insecure: bool = False
    mqtt_client_id: str = "detection-service"
    mqtt_sensor_topic: str = "sentinelx/{device_id}/telemetry"
    mqtt_camera_topic: str = "sentinelx/{device_id}/camera"
    mqtt_qos: int = Field(0, ge=0, le=2)
    mqtt_result_topic: str | None = "sentinelx/{device_id}/detection"
    """Topic des résultats ; vide = envoi HTTP au backend (BACKEND_URL) à la place."""
    mqtt_result_qos: int = Field(1, ge=0, le=2)
    mqtt_reconnect_min_s: float = 1.0
    mqtt_reconnect_max_s: float = 30.0

    # --- Fenêtres et features -------------------------------------------
    inference_interval_s: float = Field(0.5, gt=0)
    short_window_s: float = Field(2.0, gt=0)
    long_window_s: float = Field(60.0, gt=0)
    min_samples_short: int = Field(5, ge=1)
    stale_after_s: float = Field(10.0, gt=0)
    dht_max_age_s: float = Field(30.0, gt=0)
    camera_hold_s: float = Field(5.0, ge=0)

    # --- Préchauffage et baseline gaz ----------------------------------
    warmup_seconds: float = Field(120.0, ge=0)
    baseline_tau_s: float = Field(600.0, gt=0)
    baseline_init_ticks: int = Field(10, ge=1)

    # --- Modèle ----------------------------------------------------------
    predictor: PredictorKind = PredictorKind.RULES
    model_mode: ModelMode = ModelMode.MULTILABEL
    model_path: Path | None = None
    """Mode multi-classe : chemin du modèle unique (.pkcls ou .joblib)."""
    model_paths: Annotated[dict[str, Path], NoDecode] = Field(default_factory=dict)
    """Mode multi-label : JSON {"presence": "...", "fuite_gaz": "...", "feu": "..."}."""
    model_version: str = "rules-v1"
    model_negative_class: str = "aucune"
    rules_gas_delta: float = Field(150.0, gt=0)
    """RuleBasedPredictor : écart à la baseline (unités ADC) à partir duquel le gaz est suspect."""
    rules_temp_slope_c_per_min: float = Field(0.5, gt=0)
    """RuleBasedPredictor : hausse de température (°C/min) qui, avec du gaz, évoque un feu."""

    # --- Post-traitement ---------------------------------------------------
    smoothing_alpha: float = Field(0.5, gt=0, le=1)
    alert_threshold_on: float = Field(0.6, ge=0, le=1)
    alert_threshold_off: float = Field(0.4, ge=0, le=1)
    alert_k_on: int = Field(3, ge=1)
    alert_m_off: int = Field(6, ge=1)
    alert_overrides: Annotated[dict[str, dict[str, float]], NoDecode] = Field(default_factory=dict)
    """JSON, ex. {"feu": {"k_on": 2, "threshold_on": 0.5}}."""
    safety_gas_critical: float = Field(800.0, ge=0, le=1023)
    safety_gas_do_ticks: int = Field(4, ge=1)

    # --- Backend -----------------------------------------------------------
    backend_url: str | None = None
    backend_alert_route: str = "/api/alerts"
    backend_token: str | None = None
    backend_timeout_s: float = 5.0
    backend_max_retries: int = 5
    backend_backoff_base_s: float = 0.5
    backend_backoff_max_s: float = 10.0
    backend_queue_size: int = Field(500, ge=1)
    heartbeat_interval_s: float = Field(10.0, gt=0)

    # --- Base de données ------------------------------------------------
    database_url: str | None = None
    """postgresql+asyncpg://user:pass@host:5432/db ; vide = pas de stockage."""
    db_schema: str = "detection"
    db_batch_size: int = Field(200, ge=1)
    db_flush_interval_s: float = Field(1.0, gt=0)
    db_max_buffered_rows: int = Field(50_000, ge=1)
    store_all_feature_windows: bool = True

    # --- API -----------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_token: str | None = None

    # --- Logs ---------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator(
        "mqtt_username", "mqtt_password", "mqtt_tls_ca_certs", "mqtt_result_topic", "model_path", "backend_url", "backend_token",
        "database_url", "admin_token", mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("model_paths", "alert_overrides", mode="before")
    @classmethod
    def _json_dict(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return {}
        return json.loads(v) if isinstance(v, str) else v

    def alert_params(self, alert_type: str) -> AlertParams:
        values = {
            "threshold_on": self.alert_threshold_on,
            "threshold_off": self.alert_threshold_off,
            "k_on": self.alert_k_on,
            "m_off": self.alert_m_off,
        }
        values.update(self.alert_overrides.get(alert_type, {}))
        return AlertParams.model_validate(values)


@lru_cache
def get_settings() -> Settings:
    return Settings()
