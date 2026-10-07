"""Modèles Pydantic : messages MQTT entrants, payload backend, corps de l'API.

Les messages MQTT sont le **contrat d'entrée** du service (voir docs/MQTT_CONTRACT.md).
Le payload backend est le **contrat de sortie** (voir docs/BACKEND_CONTRACT.md).
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

AlertType = Literal["feu", "fuite_gaz", "presence"]
AlertStatus = Literal["feu", "fuite_gaz", "presence", "aucune"]
DeviceState = Literal["ok", "warming_up", "no_data", "stale"]
AlertSource = Literal["model", "rule"]


def _nan_to_none(v: object) -> object:
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.strip().lower() in ("nan", "null", ""):
        return None
    return v


# --------------------------------------------------------------------------- #
# Messages MQTT entrants
# --------------------------------------------------------------------------- #
class SensorMessage(BaseModel):
    """Topic `sentinelx/{device_id}/telemetry` (~5 msg/s)."""

    model_config = ConfigDict(extra="ignore")

    ts: int | None = Field(None, description="Horodatage appareil en ms (informatif, non fiable)")
    temp: float | None = Field(None, description="Température °C (DHT22), null si non lue")
    hum: float | None = Field(None, description="Humidité %HR (DHT22), null si non lue")
    pir: bool = Field(..., description="Mouvement PIR (0/1)")
    gas_raw: int = Field(..., ge=0, le=1023, validation_alias=AliasChoices("gas_raw", "gas"),
                         description="MQ-2 sortie AO, ADC 10 bits (`gas` accepté)")
    gas_do: int | None = Field(
        None, description="MQ-2 sortie DO brute : 0 = seuil dépassé (actif bas), 1 = normal. Optionnel."
    )
    warmup: bool = Field(False, description="true tant que l'appareil chauffe ses capteurs")

    @field_validator("temp", "hum", mode="before")
    @classmethod
    def _nan(cls, v: object) -> object:
        return _nan_to_none(v)

    @field_validator("temp")
    @classmethod
    def _temp_range(cls, v: float | None) -> float | None:
        # Lecture hors plage DHT22 = lecture ratée : on ignore la valeur, pas le message.
        return v if v is None or -40.0 <= v <= 80.0 else None

    @field_validator("hum")
    @classmethod
    def _hum_range(cls, v: float | None) -> float | None:
        return v if v is None or 0.0 <= v <= 100.0 else None

    @field_validator("gas_do")
    @classmethod
    def _do_binary(cls, v: int | None) -> int | None:
        if v is not None and v not in (0, 1):
            raise ValueError("gas_do doit valoir 0 ou 1")
        return v

    @property
    def gas_alert(self) -> bool | None:
        """Sortie DO normalisée : True = seuil dépassé."""
        return None if self.gas_do is None else self.gas_do == 0


class CameraFace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(None, max_length=64, description="Personne autorisée reconnue, null si inconnu")


class CameraMessage(BaseModel):
    """Topic `sentinelx/{device_id}/camera`.

    Seul `person` entre dans le modèle ; `identity`, `names` et `faces` (human-detection-ia) sont
    enregistrés comme dernier état de la caméra (detection.camera_state), lu par backend-api.
    """

    model_config = ConfigDict(extra="ignore")

    ts: int | None = Field(None, description="Horodatage appareil en ms (informatif)")
    person: bool = Field(..., description="Personne détectée")
    identity: str | None = Field(None, max_length=16, description="none | authorized | unknown")
    names: list[str] = Field(default_factory=list, max_length=20, description="Personnes autorisées reconnues")
    faces: list[CameraFace] = Field(default_factory=list, max_length=20, description="Visages vus")


class EspAlertMessage(BaseModel):
    """Topic `sentinelx/{device_id}/alert` : alerte brute de l'ESP, ex. {"type": "pir", "value": true}."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., min_length=1, max_length=64)
    value: Any = None


# --------------------------------------------------------------------------- #
# Mesures internes (horodatées à la réception)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SensorReading:
    device_id: str
    received_at: float
    device_ts: int | None
    temp: float | None
    hum: float | None
    pir: bool
    gas_raw: int
    gas_do: bool | None
    """Normalisé : True = alerte DO."""
    warmup: bool

    @classmethod
    def from_message(cls, device_id: str, received_at: float, msg: SensorMessage) -> SensorReading:
        return cls(
            device_id=device_id,
            received_at=received_at,
            device_ts=msg.ts,
            temp=msg.temp,
            hum=msg.hum,
            pir=msg.pir,
            gas_raw=msg.gas_raw,
            gas_do=msg.gas_alert,
            warmup=msg.warmup,
        )


@dataclass(frozen=True, slots=True)
class CameraEvent:
    device_id: str
    received_at: float
    device_ts: int | None
    person: bool
    identity: str | None = None
    names: tuple[str, ...] = ()
    faces: tuple[str | None, ...] = ()
    """Noms des visages vus (None = inconnu)."""

    def state_key(self) -> tuple:
        """Ce qui définit un changement d'état de la caméra (detection.camera_state)."""
        return self.person, self.identity, self.names, self.faces


# --------------------------------------------------------------------------- #
# Payload backend
# --------------------------------------------------------------------------- #
def to_utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def iso_ms(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AlertOut(BaseModel):
    type: AlertType
    active: bool
    confidence: float = Field(..., ge=0, le=1, description="Probabilité lissée")
    since: datetime | None = Field(None, description="Début de l'alerte active, sinon null")
    source: AlertSource = "model"

    @field_serializer("since")
    def _ser_since(self, v: datetime | None) -> str | None:
        return None if v is None else iso_ms(v)


class AlertPayload(BaseModel):
    """Corps du POST envoyé au backend."""

    device_id: str
    timestamp: datetime
    status: AlertStatus = Field(..., description="Alerte active la plus prioritaire (feu > fuite_gaz > presence > aucune)")
    device_state: DeviceState = Field(
        ..., description="ok, ou raison de l'absence de prédiction (warming_up, no_data, stale)"
    )
    reason: Literal["state_change", "heartbeat"]
    alerts: list[AlertOut]
    metrics: dict[str, float | None]
    model_version: str

    @field_serializer("timestamp")
    def _ser_ts(self, v: datetime) -> str:
        return iso_ms(v)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Commandes vers l'ESP (POST /devices/{device_id}/..., publiées sur sentinelx/{device_id}/cmd)
# Contrat côté firmware : software/src/main.cpp (handleCommand)
# --------------------------------------------------------------------------- #
SCREEN_TEXT_MAX = 100


class AlertCommand(BaseModel):
    """Alarme de l'ESP (buzzer + LED rouge + « ALERT » à l'écran) : seule façon de la déclencher."""

    state: Literal["on", "off"]


class BuzzerCommand(BaseModel):
    state: Literal["on", "off", "auto"]


class LedCommand(BaseModel):
    state: Literal["red", "green", "both", "off", "auto"]


class ScreenCommand(BaseModel):
    """`text` obligatoire avec `message` : l'OLED n'affiche que l'ASCII (accents retirés)."""

    state: Literal["auto", "off", "message"]
    text: str | None = None

    @field_validator("text")
    @classmethod
    def _ascii(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = "".join(c for c in unicodedata.normalize("NFD", v) if not unicodedata.combining(c))
        v = "".join(c if " " <= c <= "~" else " " for c in v).strip()
        if not 1 <= len(v) <= SCREEN_TEXT_MAX:
            raise ValueError(f"1 à {SCREEN_TEXT_MAX} caractères")
        return v

    @model_validator(mode="after")
    def _text_with_message(self) -> ScreenCommand:
        if (self.state == "message") != (self.text is not None):
            raise ValueError("text obligatoire avec state=message, et seulement avec lui")
        return self


class CommandAck(BaseModel):
    """Topic `sentinelx/{device_id}/ack` : réponse de l'ESP à une commande (même `id`)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=64)
    command: str = ""
    ok: bool
    error: str | None = None
    state: dict[str, str] = Field(default_factory=dict, description="alert, buzzer, led, screen")


class RecordingStart(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    device_id: str = Field(..., min_length=1, max_length=64)
    notes: str | None = None


class RecordingStop(BaseModel):
    device_id: str | None = Field(None, description="Appareil à arrêter ; tous si absent")
