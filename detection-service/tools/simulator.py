#!/usr/bin/env python
"""Publie de fausses mesures MQTT au format du contrat, selon une suite de scénarios.

Scénarios : normal, presence, fuite_gaz, feu, capteur_muet, dht_nan.

Exemples :
    python tools/simulator.py --sequence normal:150,fuite_gaz:60,normal:60
    python tools/simulator.py --device esp02 --sequence presence:30 --loop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from pathlib import Path

import aiomqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from detection_service.simulation import RoomSimulator, parse_sequence  # noqa: E402

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--username")
    p.add_argument("--password")
    p.add_argument("--tls-ca", help="certificat de l'autorité : active TLS (MQTTS, port 8883)")
    p.add_argument("--device", default="esp01")
    p.add_argument("--sensor-topic", default="sentinelx/{device_id}/telemetry")
    p.add_argument("--camera-topic", default="sentinelx/{device_id}/camera")
    p.add_argument("--rate", type=float, default=5.0, help="messages capteurs par seconde")
    p.add_argument("--camera-rate", type=float, default=1.0, help="messages caméra par seconde (0 = aucun)")
    p.add_argument("--sequence", default="normal:150,presence:30,fuite_gaz:45,normal:60,feu:120,capteur_muet:20")
    p.add_argument("--device-warmup", type=float, default=0.0, help="secondes initiales avec warmup=true")
    p.add_argument("--loop", action="store_true", help="rejouer la séquence indéfiniment")
    p.add_argument("--seed", type=int)
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    sequence = parse_sequence(args.sequence)
    sim = RoomSimulator(seed=args.seed)
    sensor_topic = args.sensor_topic.format(device_id=args.device)
    camera_topic = args.camera_topic.format(device_id=args.device)
    period = 1.0 / args.rate
    cam_period = 1.0 / args.camera_rate if args.camera_rate > 0 else None

    tls = aiomqtt.TLSParameters(ca_certs=args.tls_ca, cert_reqs=ssl.CERT_REQUIRED) if args.tls_ca else None
    async with aiomqtt.Client(args.host, args.port, username=args.username, password=args.password or None,
                              tls_params=tls) as client:
        start = time.time()
        while True:
            for scenario, duration in sequence:
                print(f"[{time.strftime('%H:%M:%S')}] {args.device} : {scenario} pendant {duration:.0f} s")
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
            if not args.loop:
                break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
