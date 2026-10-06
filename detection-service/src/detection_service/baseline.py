"""Baseline du MQ-2 : référence lente pour compenser la dérive du capteur.

- Initialisation : médiane des `init_ticks` premières moyennes de fenêtre après le préchauffage.
- Ensuite : moyenne mobile exponentielle de constante de temps `tau_s`
  (alpha = 1 - exp(-dt / tau), donc indépendante de la fréquence des ticks).
- Gelée tant qu'une alerte gaz/feu est active : une fuite ne doit pas devenir « la normale ».
"""

from __future__ import annotations

import math
import statistics


class GasBaseline:
    def __init__(self, tau_s: float = 600.0, init_ticks: int = 10) -> None:
        self.tau_s = tau_s
        self.init_ticks = init_ticks
        self._init_values: list[float] = []
        self._value: float | None = None
        self._last_t: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, now: float, gas_mean: float, allowed: bool = True) -> None:
        """À appeler à chaque tick, hors préchauffage. `allowed=False` gèle la baseline."""
        if math.isnan(gas_mean):
            return
        if self._value is None:
            self._init_values.append(gas_mean)
            if len(self._init_values) >= self.init_ticks:
                self._value = statistics.median(self._init_values)
                self._init_values.clear()
                self._last_t = now
            return
        if allowed and self._last_t is not None:
            dt = max(0.0, now - self._last_t)
            alpha = 1.0 - math.exp(-dt / self.tau_s)
            self._value += alpha * (gas_mean - self._value)
        # Le temps avance même gelée : à la reprise, pas de rattrapage brutal.
        self._last_t = now
