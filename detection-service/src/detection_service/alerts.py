"""Alertes du dashboard, écrites dans la table `alerts` de l'infra (lue par backend-api).

Deux origines :
- détection : une alerte à l'**activation** de feu / fuite_gaz / presence (pas à chaque tick) ;
- ESP : message brut sur `sentinelx/{device_id}/alert`, ex. {"type": "pir", "value": true}.

Une insertion déclenche un NOTIFY (trigger de backend_db) : backend-api la diffuse en WebSocket.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .postprocess import AlertResult
from .schemas import EspAlertMessage, iso_ms, to_utc

SEVERITY = {"feu": "critical", "fuite_gaz": "critical", "presence": "high"}
TITLES = {"feu": "Incendie détecté", "fuite_gaz": "Fuite de gaz détectée", "presence": "Présence détectée"}


@dataclass(slots=True)
class AlertRow:
    time: float
    device_id: str
    source: str
    severity: str
    title: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def activated(previous: list[AlertResult], current: list[AlertResult]) -> list[AlertResult]:
    """Alertes actives maintenant qui ne l'étaient pas au tick précédent."""
    was_active = {a.type for a in previous if a.active}
    return [a for a in current if a.active and a.type not in was_active]


def detection_alert(device_id: str, now: float, alert: AlertResult, model_version: str,
                    metrics: dict[str, float | None]) -> AlertRow:
    origin = "règle de sécurité" if alert.source == "rule" else "modèle"
    since = iso_ms(to_utc(alert.since)) if alert.since is not None else None
    return AlertRow(
        time=now,
        device_id=device_id,
        source=f"detection-service/{device_id}",
        severity=SEVERITY.get(alert.type, "medium"),
        title=f"{TITLES.get(alert.type, alert.type)} ({device_id})",
        description=f"Confiance {round(alert.confidence * 100)} %, origine : {origin}",
        metadata={"device_id": device_id, "type": alert.type, "since": since,
                  "model_version": model_version, "metrics": metrics},
    )


def esp_alert(device_id: str, now: float, msg: EspAlertMessage) -> AlertRow | None:
    if msg.value is False:
        return None
    # Champs choisis un par un : le contenu du message ne doit pas écraser device_id
    value = msg.value if isinstance(msg.value, bool | int | float | str) or msg.value is None else None
    return AlertRow(
        time=now,
        device_id=device_id,
        source=f"esp/{device_id}",
        severity="medium",
        title=f"Alerte capteur {msg.type} ({device_id})",
        metadata={"device_id": device_id, "type": msg.type, "value": value},
    )
