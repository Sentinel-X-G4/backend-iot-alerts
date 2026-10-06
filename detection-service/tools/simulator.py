#!/usr/bin/env python
"""Publie de fausses mesures MQTT au format du contrat, selon une suite de scénarios.

Scénarios : normal, presence, fuite_gaz, feu, capteur_muet, dht_nan.

Exemples :
    python tools/simulator.py --sequence normal:150,fuite_gaz:60,normal:60
    python tools/simulator.py --device esp02 --sequence presence:30 --loop
    python tools/simulator.py --sequence normal:60,fuite_gaz:60 --record --api http://localhost:8000

Avec --record, chaque scénario est enregistré comme session étiquetée via l'API
du service (POST /recording/start|stop) : pratique pour tester l'export du dataset.
L'étiquette utilisée est le nom du scénario (normal → « aucune »).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiomqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from detection_service.simulation import RoomSimulator, parse_sequence  # noqa: E402

RECORD_LABELS = {"normal": "aucune", "presence": "presence", "fuite_gaz": "fuite_gaz", "feu": "feu"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--username")
    p.add_argument("--password")
    p.add_argument("--device", default="esp01")
    p.add_argument("--sensor-topic", default="maison/{device_id}/capteurs")
    p.add_argument("--camera-topic", default="maison/{device_id}/camera")
    p.add_argument("--rate", type=float, default=5.0, help="messages capteurs par seconde")
    p.add_argument("--camera-rate", type=float, default=1.0, help="messages caméra par seconde (0 = aucun)")
    p.add_argument("--sequence", default="normal:150,presence:30,fuite_gaz:45,normal:60,feu:120,capteur_muet:20")
    p.add_argument("--device-warmup", type=float, default=0.0, help="secondes initiales avec warmup=true")
    p.add_argument("--loop", action="store_true", help="rejouer la séquence indéfiniment")
    p.add_argument("--seed", type=int)
    p.add_argument("--record", action="store_true", help="enregistrer chaque scénario comme session étiquetée")
    p.add_argument("--api", default="http://localhost:8000", help="API du service (avec --record)")
    return p.parse_args()


async def record(api: str, action: str, body: dict) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(f"{api}/recording/{action}", json=body)
            print(f"  recording/{action} → {r.status_code}")
    except httpx.HTTPError as exc:
        print(f"  recording/{action} impossible : {exc}")


async def main() -> None:
    args = parse_args()
    sequence = parse_sequence(args.sequence)
    sim = RoomSimulator(seed=args.seed)
    sensor_topic = args.sensor_topic.format(device_id=args.device)
    camera_topic = args.camera_topic.format(device_id=args.device)
    period = 1.0 / args.rate
    cam_period = 1.0 / args.camera_rate if args.camera_rate > 0 else None

    async with aiomqtt.Client(args.host, args.port, username=args.username, password=args.password) as client:
        start = time.time()
        while True:
            for scenario, duration in sequence:
                print(f"[{time.strftime('%H:%M:%S')}] {args.device} : {scenario} pendant {duration:.0f} s")
                if args.record and scenario in RECORD_LABELS:
                    await record(args.api, "start", {"label": RECORD_LABELS[scenario], "device_id": args.device,
                                                     "notes": f"simulateur:{scenario}"})
                end = time.time() + duration
                next_cam = time.time()
                while (now := time.time()) < end:
                    msg = sim.sensor(now, scenario, warmup=now - start < args.device_warmup)
                    if msg is not None:
                        await client.publish(sensor_topic, json.dumps(msg))
                    if cam_period and now >= next_cam:
                        next_cam = now + cam_period
                        cam = sim.camera(now, scenario)
                        if cam is not None:
                            await client.publish(camera_topic, json.dumps(cam))
                    await asyncio.sleep(max(0.0, period - (time.time() - now)))
                if args.record and scenario in RECORD_LABELS:
                    await record(args.api, "stop", {"device_id": args.device})
            if not args.loop:
                break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
