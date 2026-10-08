"""Post-traitement des probabilités : lissage, hystérésis, filet de sécurité, priorité."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .config import ALERT_TYPES, AlertParams, Settings

PRIORITY: tuple[str, ...] = ALERT_TYPES  # feu > fuite_gaz > inondation > presence


@dataclass(frozen=True, slots=True)
class AlertResult:
    type: str
    active: bool
    confidence: float
    since: float | None
    source: str  # "model" | "rule"


class AlertTracker:
    """Hystérésis : ON après K probabilités lissées ≥ seuil_on consécutives,
    OFF après M probabilités lissées < seuil_off consécutives."""

    def __init__(self, params: AlertParams, alpha: float = 0.5) -> None:
        self.params = params
        self.alpha = alpha
        self.smoothed: float | None = None
        self.active = False
        self.since: float | None = None
        self._above = 0
        self._below = 0

    def update(self, prob: float, now: float) -> None:
        prob = 0.0 if math.isnan(prob) else prob
        self.smoothed = prob if self.smoothed is None else self.smoothed + self.alpha * (prob - self.smoothed)
        p = self.params
        self._above = self._above + 1 if self.smoothed >= p.threshold_on else 0
        self._below = self._below + 1 if self.smoothed < p.threshold_off else 0
        if not self.active and self._above >= p.k_on:
            self.active, self.since = True, now
        elif self.active and self._below >= p.m_off:
            self.active, self.since = False, None


class GasSafetyRule:
    """Filet indépendant du modèle : force `fuite_gaz` si le gaz dépasse un seuil critique
    ou si la sortie DO du MQ-2 est en alerte sur toute la fenêtre pendant N ticks."""

    def __init__(self, critical: float, do_ticks: int) -> None:
        self.critical = critical
        self.do_ticks = do_ticks
        self._do_streak = 0
        self.since: float | None = None

    def update(self, features: Mapping[str, float], now: float) -> bool:
        gas_max = features.get("gas_max", math.nan)
        do_ratio = features.get("gas_do_ratio", 0.0)
        self._do_streak = self._do_streak + 1 if do_ratio is not None and do_ratio >= 1.0 else 0
        triggered = (gas_max is not None and not math.isnan(gas_max) and gas_max >= self.critical) or (
            self._do_streak >= self.do_ticks
        )
        if triggered and self.since is None:
            self.since = now
        elif not triggered:
            self.since = None
        return triggered


class PostProcessor:
    """État de post-traitement d'**un** appareil."""

    def __init__(self, settings: Settings) -> None:
        self.trackers = {t: AlertTracker(settings.alert_params(t), settings.smoothing_alpha) for t in ALERT_TYPES}
        self.safety = GasSafetyRule(settings.safety_gas_critical, settings.safety_gas_do_ticks)
        self.results: list[AlertResult] = [AlertResult(t, False, 0.0, None, "model") for t in ALERT_TYPES]

    def update(self, probs: Mapping[str, float], features: Mapping[str, float], now: float) -> list[AlertResult]:
        for t, tracker in self.trackers.items():
            tracker.update(probs.get(t, 0.0), now)
        forced = self.safety.update(features, now)

        results = []
        for t, tracker in self.trackers.items():
            confidence = tracker.smoothed or 0.0
            if t == "fuite_gaz" and forced and not tracker.active:
                results.append(AlertResult(t, True, 1.0, self.safety.since, "rule"))
            else:
                results.append(AlertResult(t, tracker.active, round(confidence, 4), tracker.since, "model"))
        self.results = results
        return results

    @property
    def gas_alert_active(self) -> bool:
        """Utilisé pour geler la baseline gaz."""
        return any(r.active for r in self.results if r.type in ("fuite_gaz", "feu"))


def global_status(results: list[AlertResult]) -> str:
    active = {r.type for r in results if r.active}
    return next((t for t in PRIORITY if t in active), "aucune")
