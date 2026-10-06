"""Calcul **pur** des features d'une fenêtre.

Cette fonction est la seule source de vérité des features : le temps réel l'appelle
à chaque tick et les fenêtres calculées sont stockées telles quelles dans
`feature_windows`, puis exportées pour Orange. Aucune logique de feature ne doit
exister ailleurs, sinon les features d'entraînement et de production divergent.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

FEATURE_NAMES: tuple[str, ...] = (
    "pir_ratio",
    "cam_ratio",
    "gas_mean",
    "gas_max",
    "gas_min",
    "gas_slope",
    "gas_delta_baseline",
    "gas_do_ratio",
    "temp_last",
    "hum_last",
    "temp_delta_long",
    "hum_delta_long",
    "temp_slope_long",
    "n_samples_short",
)

Series = Sequence[tuple[float, float]]
"""Suite de (horodatage en s, valeur), triée par temps croissant."""

NAN = float("nan")


@dataclass(frozen=True, slots=True)
class WindowInput:
    """Tout ce qu'il faut pour calculer les features à l'instant `now`."""

    now: float
    pir: Series
    gas: Series
    gas_do: Series
    """Uniquement les mesures où DO est présent ; 1.0 = alerte."""
    temp: Series
    """Uniquement les lectures valides (pas de None/NaN)."""
    hum: Series
    camera: Series
    baseline: float | None
    short_window_s: float = 2.0
    long_window_s: float = 60.0
    dht_max_age_s: float = 30.0
    camera_hold_s: float = 5.0


def _since(series: Series, start: float, end: float) -> list[tuple[float, float]]:
    return [(t, v) for t, v in series if start < t <= end]


def _ratio(values: Sequence[float]) -> float:
    return sum(1.0 for v in values if v) / len(values) if values else 0.0


def linear_slope(points: Sequence[tuple[float, float]]) -> float:
    """Pente par moindres carrés (unité de valeur / s). 0 si moins de 2 points ou temps constants."""
    n = len(points)
    if n < 2:
        return 0.0
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    if var == 0:
        return 0.0
    return sum((t - mt) * (v - mv) for t, v in points) / var


def _dht_features(series: Series, now: float, long_s: float, max_age: float) -> tuple[float, float, float]:
    """(dernière valeur, delta sur fenêtre longue, pente /s). NaN si pas de valeur assez récente."""
    recent = [(t, v) for t, v in series if t <= now]
    if not recent or now - recent[-1][0] > max_age:
        return NAN, NAN, NAN
    last = recent[-1][1]
    window = _since(recent, now - long_s, now)
    if len(window) < 2:
        return last, 0.0, 0.0
    return last, window[-1][1] - window[0][1], linear_slope(window)


def _camera_ratio(camera: Series, now: float, short_s: float, hold_s: float) -> float:
    in_window = [v for _, v in _since(camera, now - short_s, now)]
    if in_window:
        return _ratio(in_window)
    previous = [(t, v) for t, v in camera if t <= now - short_s]
    if previous and now - previous[-1][0] <= hold_s:
        return 1.0 if previous[-1][1] else 0.0
    return 0.0


def compute_features(w: WindowInput) -> dict[str, float]:
    start = w.now - w.short_window_s
    pir = [v for _, v in _since(w.pir, start, w.now)]
    gas_pts = _since(w.gas, start, w.now)
    gas = [v for _, v in gas_pts]
    gas_do = [v for _, v in _since(w.gas_do, start, w.now)]

    gas_mean = sum(gas) / len(gas) if gas else NAN
    temp_last, temp_delta, temp_slope = _dht_features(w.temp, w.now, w.long_window_s, w.dht_max_age_s)
    hum_last, hum_delta, _ = _dht_features(w.hum, w.now, w.long_window_s, w.dht_max_age_s)

    return {
        "pir_ratio": _ratio(pir),
        "cam_ratio": _camera_ratio(w.camera, w.now, w.short_window_s, w.camera_hold_s),
        "gas_mean": gas_mean,
        "gas_max": max(gas) if gas else NAN,
        "gas_min": min(gas) if gas else NAN,
        "gas_slope": linear_slope(gas_pts),
        "gas_delta_baseline": gas_mean - w.baseline if gas and w.baseline is not None else NAN,
        "gas_do_ratio": _ratio(gas_do),
        "temp_last": temp_last,
        "hum_last": hum_last,
        "temp_delta_long": temp_delta,
        "hum_delta_long": hum_delta,
        "temp_slope_long": temp_slope * 60.0 if not math.isnan(temp_slope) else NAN,  # °C/min
        "n_samples_short": float(len(gas_pts)),
    }


def nan_to_none(features: dict[str, float]) -> dict[str, float | None]:
    """Pour JSON / BDD : NaN n'est pas sérialisable proprement."""
    return {k: None if isinstance(v, float) and math.isnan(v) else v for k, v in features.items()}
