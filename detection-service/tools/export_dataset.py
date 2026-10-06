#!/usr/bin/env python
"""Exporte les fenêtres de features étiquetées (table feature_windows) en CSV pour Orange.

Exemples :
    python tools/export_dataset.py -o exports/dataset.csv
    python tools/export_dataset.py -o exports/feu.csv --target feu --orange-flags
    python tools/export_dataset.py --list-sessions

Dans Orange, découper l'entraînement et le test **par session** (colonne session_id),
jamais par mélange aléatoire de fenêtres : des fenêtres consécutives se ressemblent trop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from detection_service.config import ALERT_TYPES, Settings  # noqa: E402
from detection_service.dataset import write_csv  # noqa: E402
from detection_service.storage.sql import SqlStorage  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--out", type=Path, help="fichier CSV de sortie (défaut : stdout)")
    p.add_argument("--database-url", help="défaut : DATABASE_URL")
    p.add_argument("--schema", help="défaut : DB_SCHEMA")
    p.add_argument("--session", action="append", type=uuid.UUID, help="limiter à ces sessions (répétable)")
    p.add_argument("--device", help="limiter à un appareil")
    p.add_argument("--target", choices=ALERT_TYPES, help="cible binaire <type>/aucune (mode multi-label)")
    p.add_argument("--orange-flags", action="store_true",
                   help="en-têtes cD#label / mS#session_id pour qu'Orange règle cible et méta")
    p.add_argument("--list-sessions", action="store_true", help="lister les sessions et quitter")
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    settings = Settings()
    url = args.database_url or settings.database_url
    if not url:
        print("DATABASE_URL non défini (ou --database-url)", file=sys.stderr)
        return 1
    storage = SqlStorage(url, schema=args.schema if args.schema is not None else settings.db_schema)
    await storage.connect()
    try:
        if args.list_sessions:
            for s in await storage.list_sessions():
                print(f"{s['id']}  {s['device_id']:<12} {s['label']:<20} {s['started_at']} → {s['ended_at']}  "
                      f"{s['notes'] or ''}")
            return 0
        rows = storage.iter_labeled_windows(args.session, args.device)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", newline="", encoding="utf-8") as fh:
                counts = await write_csv(rows, fh, args.target, args.orange_flags)
        else:
            counts = await write_csv(rows, sys.stdout, args.target, args.orange_flags)
    finally:
        await storage.close()

    by_label: Counter[str] = Counter()
    for (_, label), n in counts.items():
        by_label[label] += n
    print(f"{sum(counts.values())} fenêtres, {len({s for s, _ in counts})} sessions", file=sys.stderr)
    for label, n in by_label.most_common():
        print(f"  {label:<24} {n}", file=sys.stderr)
    if len({s for s, _ in counts}) < 4:
        print("Attention : peu de sessions, l'évaluation par session sera peu fiable.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
