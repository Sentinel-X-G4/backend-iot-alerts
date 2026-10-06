#!/usr/bin/env python
"""Génère un jeu d'entraînement **synthétique** pour Orange, sans broker ni base.

Chaque session rejoue un scénario du simulateur dans le vrai DevicePipeline avec une
horloge simulée : les features sont donc calculées exactement comme en production.
Une phase « normal » non étiquetée précède chaque session (préchauffage, baseline gaz).
Les conditions de la pièce (gaz, température, humidité de base) varient d'une session à l'autre.

Sert à tester la chaîne Orange → modèle → service. Les données réelles (sessions
enregistrées avec les capteurs, puis tools/export_dataset.py) restent indispensables.

Exemples :
    python tools/generate_synthetic_dataset.py -o exports/synthetic
    python tools/generate_synthetic_dataset.py -o exports/synthetic --sessions 10 --duration 180 --seed 1
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from detection_service.config import ALERT_TYPES, Settings  # noqa: E402
from detection_service.dataset import write_csv  # noqa: E402
from detection_service.engine import DevicePipeline  # noqa: E402
from detection_service.features import nan_to_none  # noqa: E402
from detection_service.predictors import RuleBasedPredictor  # noqa: E402
from detection_service.schemas import CameraEvent, SensorReading  # noqa: E402
from detection_service.simulation import RoomSimulator  # noqa: E402

LABELS = {"normal": "aucune", "presence": "presence", "fuite_gaz": "fuite_gaz", "feu": "feu"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--out-dir", type=Path, default=Path("exports/synthetic"))
    p.add_argument("--sessions", type=int, default=8, help="sessions par classe")
    p.add_argument("--duration", type=float, default=120.0, help="durée d'une session (s)")
    p.add_argument("--lead-in", type=float, default=90.0, help="phase « normal » non étiquetée avant chaque session (s)")
    p.add_argument("--rate", type=float, default=5.0, help="messages capteurs par seconde")
    p.add_argument("--camera-rate", type=float, default=1.0, help="messages caméra par seconde")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def run_session(scenario: str, args: argparse.Namespace, settings: Settings, rng: random.Random) -> list[dict[str, Any]]:
    sim = RoomSimulator(seed=rng.randrange(2**32), base_gas=rng.uniform(100, 220),
                        base_temp=rng.uniform(17, 26), base_hum=rng.uniform(30, 65))
    dev = DevicePipeline("sim", settings)
    predictor = RuleBasedPredictor(settings.rules_gas_delta, settings.rules_temp_slope_c_per_min)
    session_id = uuid.uuid4()
    label = LABELS[scenario]
    t0 = rng.uniform(0, 3600)  # décale la dérive lente du MQ-2
    end = t0 + args.lead_in + args.duration
    period, cam_period, tick_period = 1.0 / args.rate, 1.0 / args.camera_rate, settings.inference_interval_s
    t, next_cam, next_tick = t0, t0, t0 + tick_period
    rows = []
    while t < end:
        current = "normal" if t < t0 + args.lead_in else scenario
        msg = sim.sensor(t, current)
        if msg is not None:
            dev.add_sensor(SensorReading("sim", t, msg["ts"], msg["temp"], msg["hum"], bool(msg["pir"]),
                                         msg["gas_raw"], msg["gas_do"] == 0, False))
        if t >= next_cam:
            next_cam += cam_period
            cam = sim.camera(t, current)
            if cam is not None:
                dev.add_camera(CameraEvent("sim", t, cam["ts"], cam["person"]))
        t += period
        while next_tick <= t:
            result = dev.tick(next_tick, predictor, lambda exc: None)
            if result.device_state == "ok" and current == scenario and next_tick >= t0 + args.lead_in:
                rows.append({**nan_to_none(result.features), "label": label, "session_id": session_id})
            next_tick += tick_period
    return rows


async def as_async(rows: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for row in rows:
        yield row


async def main() -> int:
    args = parse_args()
    if args.lead_in < 60:
        print("--lead-in < 60 s : la fenêtre longue (température) ne sera pas remplie", file=sys.stderr)
    # Préchauffage court : la phase lead-in suffit, pas besoin des 120 s de production.
    settings = Settings(_env_file=None, warmup_seconds=10.0)
    rng = random.Random(args.seed)
    rows = [row for _ in range(args.sessions) for scenario in LABELS
            for row in run_session(scenario, args, settings, rng)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for target in (None, *ALERT_TYPES):
        path = args.out_dir / f"{target or 'dataset'}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            counts = await write_csv(as_async(rows), fh, target, orange_flags=True)
        by_label: dict[str, int] = {}
        for (_, label), n in counts.items():
            by_label[label] = by_label.get(label, 0) + n
        print(f"{path} : {sum(counts.values())} fenêtres, {len({s for s, _ in counts})} sessions  {by_label}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
