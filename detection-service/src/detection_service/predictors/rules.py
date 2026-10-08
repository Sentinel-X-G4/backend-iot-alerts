"""Predictor à règles : fonctionne avant tout entraînement, sert aussi de référence dans les tests."""

from __future__ import annotations

import math
from collections.abc import Mapping

from .base import Predictor


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, x))))


def _f(features: Mapping[str, float], name: str) -> float:
    v = features.get(name)
    return 0.0 if v is None or math.isnan(v) else float(v)


class RuleBasedPredictor(Predictor):
    """Pseudo-probabilités à partir de seuils simples.

    - presence  : max(pir_ratio, cam_ratio)
    - fuite_gaz : sigmoïde centrée sur `gas_delta` (écart à la baseline)
    - feu       : gaz/fumée **et** température qui monte (pente ou delta sur la fenêtre longue)
    - inondation : humidité proche de la saturation (sigmoïde centrée sur `flood_hum`)
    """

    def __init__(self, gas_delta: float = 150.0, temp_slope_c_per_min: float = 0.5,
                 flood_hum: float = 88.0, version: str = "rules-v1") -> None:
        self.gas_delta = gas_delta
        self.temp_slope = temp_slope_c_per_min
        self.flood_hum = flood_hum
        self.version = version

    def predict(self, features: Mapping[str, float]) -> dict[str, float]:
        presence = max(_f(features, "pir_ratio"), _f(features, "cam_ratio"))
        gas_score = _sigmoid((_f(features, "gas_delta_baseline") - self.gas_delta) / (self.gas_delta * 0.2))
        temp_score = max(
            _sigmoid((_f(features, "temp_slope_long") - self.temp_slope) / (self.temp_slope * 0.3)),
            _sigmoid((_f(features, "temp_delta_long") - 2.0) / 0.5),
        )
        hum = features.get("hum_last")
        flood = 0.0 if hum is None or math.isnan(hum) else _sigmoid((hum - self.flood_hum) / 1.5)
        return {
            "feu": min(gas_score, temp_score),
            "fuite_gaz": gas_score,
            "inondation": flood,
            "presence": min(1.0, presence),
        }

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "gas_delta": self.gas_delta, "temp_slope_c_per_min": self.temp_slope,
                "flood_hum": self.flood_hum}
