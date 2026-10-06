"""Plan B sans orange3 : modèle scikit-learn exporté en .joblib + JSON de métadonnées.

Le JSON (même nom, extension .json) est produit par tools/export_orange_to_joblib.py :
    {"features": [...], "classes": [...], "impute": {"feature": valeur}, "source": "..."}
`impute` reproduit l'imputation des valeurs manquantes faite par Orange.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .base import ModelLoadError, ModelPredictor


def meta_path(model_path: str | Path) -> Path:
    return Path(model_path).with_suffix(".json")


class SklearnModel:
    def __init__(self, path: str, estimator: Any, meta: dict[str, Any]) -> None:
        self.path = path
        self.estimator = estimator
        try:
            self.feature_names = list(meta["features"])
            self.class_names = [str(c) for c in meta["classes"]]
        except KeyError as exc:
            raise ModelLoadError(f"{meta_path(path)} : clé {exc} manquante") from exc
        self.impute: dict[str, float] = {k: float(v) for k, v in meta.get("impute", {}).items()}
        n_classes = len(getattr(estimator, "classes_", self.class_names))
        if n_classes != len(self.class_names):
            raise ModelLoadError(f"{path} : {n_classes} classes dans le modèle, {len(self.class_names)} dans le JSON")

    def predict_proba(self, row: Sequence[float]) -> Sequence[float]:
        import numpy as np

        values = [
            self.impute.get(name, v) if math.isnan(v) else v
            for name, v in zip(self.feature_names, row, strict=True)
        ]
        return self.estimator.predict_proba(np.asarray([values], dtype=float))[0]


def load_sklearn_model(path: str) -> SklearnModel:
    meta_file = meta_path(path)
    if not Path(path).is_file():
        raise ModelLoadError(f"Modèle joblib introuvable : {path}")
    if not meta_file.is_file():
        raise ModelLoadError(f"Métadonnées introuvables : {meta_file} (produites par export_orange_to_joblib.py)")
    try:
        import joblib
    except ImportError as exc:
        raise ModelLoadError("scikit-learn/joblib non installés : `pip install .[sklearn]`") from exc
    return SklearnModel(path, joblib.load(path), json.loads(meta_file.read_text(encoding="utf-8")))


class SklearnPredictor(ModelPredictor):
    load_model = staticmethod(load_sklearn_model)
