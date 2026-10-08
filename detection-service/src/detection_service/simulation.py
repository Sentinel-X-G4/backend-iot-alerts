"""Générateur de fausses mesures, au format exact du contrat MQTT.

Utilisé par tools/simulator.py (publication MQTT), par tools/train_models.py (données
d'entraînement) et par le test d'intégration (sans broker).
L'état physique (gaz, température, humidité) évolue de façon continue d'un scénario à l'autre.
Un scénario peut combiner plusieurs situations avec « + » (ex. « presence+fuite_gaz »).
"""

from __future__ import annotations

import math
import random
from typing import Any

SCENARIOS = ("normal", "presence", "fuite_gaz", "feu", "inondation", "capteur_muet", "dht_nan")


def scenario_parts(scenario: str) -> set[str]:
    return {p.strip() for p in scenario.split("+") if p.strip()}


def parse_sequence(spec: str) -> list[tuple[str, float]]:
    """« normal:150,fuite_gaz:60 » → [("normal", 150.0), ("fuite_gaz", 60.0)]."""
    out = []
    for part in spec.split(","):
        name, _, duration = part.strip().partition(":")
        unknown = scenario_parts(name) - set(SCENARIOS)
        if unknown or not name:
            raise ValueError(f"scénario inconnu {name!r} (disponibles : {', '.join(SCENARIOS)})")
        out.append((name, float(duration or 60)))
    return out


def _approach(current: float, target: float, dt: float, tau: float) -> float:
    return target + (current - target) * math.exp(-dt / tau)


class RoomSimulator:
    """Pièce simulée. Valeurs normales : gaz ≈ 100 (ADC), 21 °C, 50 %RH.

    Intensités (modifiables entre deux scénarios) : `leak_gas` (excès de gaz d'une fuite),
    `fire_gas` / `fire_rate_c_per_min` (fumée et montée en température d'un feu),
    `flood_hum` (humidité relative atteinte pendant une inondation).
    """

    def __init__(self, seed: int | None = None, base_gas: float = 100.0, base_temp: float = 21.0,
                 base_hum: float = 50.0, pir_hold_s: float = 5.0, gas_do_threshold: int = 600) -> None:
        self.rng = random.Random(seed)
        self.base_gas = base_gas
        self.base_temp = base_temp
        self.base_hum = base_hum
        self.pir_hold_s = pir_hold_s
        self.gas_do_threshold = gas_do_threshold
        self.leak_gas = 450.0
        self.fire_gas = 350.0
        self.fire_rate_c_per_min = 3.0
        self.flood_hum = 95.0
        self.gas_excess = 0.0
        self.temp_excess = 0.0
        self.hum_excess = 0.0
        self.pir_until = -1.0
        self._last_t: float | None = None
        self._last_dht: float = -math.inf

    def _evolve(self, t: float, scenario: str) -> None:
        parts = scenario_parts(scenario)
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        if "feu" in parts:
            self.gas_excess = _approach(self.gas_excess, max(self.fire_gas, self.leak_gas if "fuite_gaz" in parts else 0),
                                        dt, 8.0)
            self.temp_excess = min(15.0, self.temp_excess + dt * self.fire_rate_c_per_min / 60.0)
        elif "fuite_gaz" in parts:
            self.gas_excess = _approach(self.gas_excess, self.leak_gas, dt, 6.0)
        else:
            self.gas_excess = _approach(self.gas_excess, 0.0, dt, 20.0)
        if "feu" not in parts:
            self.temp_excess = _approach(self.temp_excess, 0.0, dt, 180.0)
        if "inondation" in parts:
            self.hum_excess = _approach(self.hum_excess, max(0.0, self.flood_hum - self.base_hum), dt, 25.0)
        else:
            self.hum_excess = _approach(self.hum_excess, 0.0, dt, 300.0)  # la pièce sèche lentement
        if "presence" in parts and self.rng.random() < 0.3 * max(dt, 0.2):
            self.pir_until = t + self.pir_hold_s  # temporisation du HW-416

    def sensor(self, t: float, scenario: str, warmup: bool = False) -> dict[str, Any] | None:
        """Message du topic capteurs à l'instant t (s), ou None si le capteur est muet."""
        parts = scenario_parts(scenario)
        self._evolve(t, scenario)
        if "capteur_muet" in parts:
            return None
        drift = 10.0 * math.sin(t / 600.0)  # dérive lente du MQ-2
        gas = round(self.base_gas + drift + self.gas_excess + self.rng.gauss(0, 4))
        gas = max(0, min(1023, gas))
        temp = hum = None
        if t - self._last_dht >= 2.0:  # DHT22 : au plus une lecture toutes les 2 s
            self._last_dht = t
            if "dht_nan" not in parts:
                temp = round(self.base_temp + self.temp_excess + self.rng.gauss(0, 0.05), 1)
                hum = self.base_hum + self.hum_excess - self.temp_excess * 0.8 + self.rng.gauss(0, 0.3)
                hum = round(min(100.0, max(0.0, hum)), 1)
        return {
            "ts": int(t * 1000),
            "temp": temp,
            "hum": hum,
            "pir": 1 if t < self.pir_until else 0,
            "gas_raw": gas,
            "gas_do": 0 if gas >= self.gas_do_threshold else 1,  # actif bas
            "warmup": warmup,
        }

    def camera(self, t: float, scenario: str) -> dict[str, Any] | None:
        parts = scenario_parts(scenario)
        if "capteur_muet" in parts:
            return None
        person = "presence" in parts and self.rng.random() < 0.8
        return {"ts": int(t * 1000), "person": person}
