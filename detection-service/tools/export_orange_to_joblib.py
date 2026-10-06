#!/usr/bin/env python
"""Convertit un modèle Orange (.pkcls) en modèle scikit-learn (.joblib) + métadonnées (.json).

Plan B quand `orange3` est trop lourd pour l'image Docker : à lancer sur la machine où
Orange est installé, puis utiliser PREDICTOR=sklearn dans le service.

    python tools/export_orange_to_joblib.py models/feu.pkcls            # → models/feu.joblib + feu.json
    python tools/export_orange_to_joblib.py models/feu.pkcls -o out/feu.joblib

Le JSON contient la liste ordonnée des features, les classes dans l'ordre de
`predict_proba`, et les valeurs d'imputation qu'Orange applique aux valeurs manquantes.
Si le modèle utilise d'autres prétraitements (normalisation, continuisation...), la
conversion est refusée : utilisez alors OrangePredictor.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any


class ConversionError(RuntimeError):
    pass


def extract(model: Any) -> tuple[Any, dict[str, Any]]:
    """Retourne (estimateur sklearn, métadonnées). Lève ConversionError si non convertible."""
    skl = getattr(model, "skl_model", None)
    if skl is None or not hasattr(skl, "predict_proba"):
        raise ConversionError(f"{type(model).__name__} n'encapsule pas de classifieur scikit-learn (skl_model).")

    attrs = list(model.domain.attributes)
    original = getattr(model, "original_domain", None)
    if original is not None and [a.name for a in original.attributes] != [a.name for a in attrs]:
        raise ConversionError(
            "Le domaine du modèle diffère du domaine d'origine (features transformées par Orange : "
            f"{[a.name for a in attrs]}). Utilisez OrangePredictor."
        )

    impute: dict[str, float] = {}
    for a in attrs:
        cv = a.compute_value
        if cv is None:
            continue
        if type(cv).__name__ == "ReplaceUnknowns" and getattr(cv.variable, "name", None) == a.name:
            impute[a.name] = float(cv.value)
            continue
        raise ConversionError(
            f"Prétraitement Orange non supporté sur « {a.name} » ({type(cv).__name__}). Utilisez OrangePredictor."
        )

    values = list(model.domain.class_var.values)
    classes = [values[int(c)] for c in skl.classes_]
    meta = {
        "features": [a.name for a in attrs],
        "classes": classes,
        "impute": impute,
        "source": type(model).__name__,
        "estimator": type(skl).__name__,
    }
    return skl, meta


def convert(src: Path, dst: Path) -> dict[str, Any]:
    import joblib

    with src.open("rb") as fh:
        model = pickle.load(fh)  # noqa: S301 — fichier de confiance produit par Orange
    skl, meta = extract(model)
    meta["source_file"] = src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(skl, dst)
    dst.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model", type=Path, help="fichier .pkcls exporté par le widget Save Model")
    p.add_argument("-o", "--out", type=Path, help="fichier .joblib (défaut : même nom, extension .joblib)")
    args = p.parse_args()
    dst = args.out or args.model.with_suffix(".joblib")
    try:
        meta = convert(args.model, dst)
    except ConversionError as exc:
        print(f"Conversion impossible : {exc}", file=sys.stderr)
        return 1
    print(f"{dst} écrit ({meta['estimator']}), {len(meta['features'])} features, classes {meta['classes']}")
    print(f"{dst.with_suffix('.json')} écrit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
