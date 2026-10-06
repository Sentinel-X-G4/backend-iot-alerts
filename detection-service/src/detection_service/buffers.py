"""Tampons circulaires horodatés, un jeu par appareil."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .features import WindowInput
from .schemas import CameraEvent, SensorReading

_Deque = deque[tuple[float, float]]


def _prune(q: _Deque, before: float) -> None:
    while q and q[0][0] < before:
        q.popleft()


@dataclass
class DeviceBuffers:
    """Les horodatages sont ceux de **réception** par le service (l'ESP n'a pas d'horloge fiable)."""

    pir: _Deque = field(default_factory=deque)
    gas: _Deque = field(default_factory=deque)
    gas_do: _Deque = field(default_factory=deque)
    temp: _Deque = field(default_factory=deque)
    hum: _Deque = field(default_factory=deque)
    camera: _Deque = field(default_factory=deque)

    def add_sensor(self, r: SensorReading) -> None:
        t = r.received_at
        self.pir.append((t, 1.0 if r.pir else 0.0))
        self.gas.append((t, float(r.gas_raw)))
        if r.gas_do is not None:
            self.gas_do.append((t, 1.0 if r.gas_do else 0.0))
        if r.temp is not None:
            self.temp.append((t, r.temp))
        if r.hum is not None:
            self.hum.append((t, r.hum))

    def add_camera(self, e: CameraEvent) -> None:
        self.camera.append((e.received_at, 1.0 if e.person else 0.0))

    def prune(self, now: float, short_s: float, long_s: float, dht_max_age_s: float, camera_hold_s: float) -> None:
        """Supprime ce qui ne servira plus. On garde toujours la dernière lecture DHT/caméra récente."""
        for q in (self.pir, self.gas, self.gas_do):
            _prune(q, now - short_s)
        for q in (self.temp, self.hum):
            _prune(q, now - max(long_s, dht_max_age_s))
        _prune(self.camera, now - short_s - camera_hold_s)

    def window(self, now: float, baseline: float | None, *, short_s: float, long_s: float,
               dht_max_age_s: float, camera_hold_s: float) -> WindowInput:
        return WindowInput(
            now=now,
            pir=tuple(self.pir),
            gas=tuple(self.gas),
            gas_do=tuple(self.gas_do),
            temp=tuple(self.temp),
            hum=tuple(self.hum),
            camera=tuple(self.camera),
            baseline=baseline,
            short_window_s=short_s,
            long_window_s=long_s,
            dht_max_age_s=dht_max_age_s,
            camera_hold_s=camera_hold_s,
        )
