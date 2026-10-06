"""Export des fenêtres étiquetées en CSV pour Orange.

Colonnes : exactement FEATURE_NAMES, puis `label` et `session_id`.
- `label` peut combiner plusieurs alertes avec « + » (ex. « presence+fuite_gaz ») pour les
  sessions où elles coexistent.
- `target=<type>` produit une cible binaire (`<type>` / `aucune`) pour entraîner un modèle
  du mode multi-label.
- `orange_flags=True` préfixe les en-têtes (`cD#label`, `mS#session_id`) : Orange règle alors
  tout seul la cible et la méta, et `session_id` ne peut pas devenir une feature par erreur.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any, TextIO

from .config import ALERT_TYPES
from .features import FEATURE_NAMES

NEGATIVE = "aucune"


def label_parts(label: str) -> set[str]:
    return {p.strip() for p in label.split("+") if p.strip()}


def target_label(label: str, target: str | None) -> str:
    if target is None:
        return label
    return target if target in label_parts(label) else NEGATIVE


def _cell(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""  # valeur manquante pour Orange
    return v


async def write_csv(rows: AsyncIterator[dict[str, Any]], out: TextIO, target: str | None = None,
                    orange_flags: bool = False) -> Counter[tuple[str, str]]:
    """Écrit le CSV, retourne le nombre de fenêtres par (session_id, label)."""
    if target is not None and target not in ALERT_TYPES:
        raise ValueError(f"cible inconnue {target!r}, attendue parmi {list(ALERT_TYPES)}")
    header = [*FEATURE_NAMES, "cD#label" if orange_flags else "label",
              "mS#session_id" if orange_flags else "session_id"]
    writer = csv.writer(out)
    writer.writerow(header)
    counts: Counter[tuple[str, str]] = Counter()
    async for row in rows:
        label = target_label(str(row["label"]), target)
        session = str(row["session_id"]) if row.get("session_id") is not None else ""
        writer.writerow([*(_cell(row.get(name)) for name in FEATURE_NAMES), label, session])
        counts[(session, label)] += 1
    return counts
