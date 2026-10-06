"""Modèle Orange (.pkcls produit par le widget « Save Model »). Nécessite le paquet `orange3`.

La table d'entrée est construite dans le **domaine d'origine** du modèle
(`model.original_domain`, à défaut `model.domain`) : les noms et l'ordre des colonnes sont
ceux vus par Orange à l'entraînement, et Orange applique lui-même ses prétraitements
(imputation des valeurs manquantes, normalisation...) en passant vers `model.domain`.
"""

from __future__ import annotations

import pickle
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .base import ModelLoadError, ModelPredictor


class OrangeModel:
    def __init__(self, path: str, model: Any) -> None:
        from Orange.data import Domain  # import tardif : orange3 est optionnel

        self.path = path
        self.model = model
        original = getattr(model, "original_domain", None)
        input_domain = original if original is not None else model.domain
        attrs = list(input_domain.attributes)
        not_continuous = [a.name for a in attrs if not a.is_continuous]
        if not_continuous:
            raise ModelLoadError(
                f"{path} : colonnes non numériques {not_continuous}. Toutes les features doivent être "
                "« numeric » dans Orange (widget File / Edit Domain)."
            )
        if model.domain.class_var is None or not model.domain.class_var.is_discrete:
            raise ModelLoadError(f"{path} : le modèle doit être un classifieur (cible catégorielle).")
        self._domain = Domain(attrs)
        self.feature_names = [a.name for a in attrs]
        self.class_names = list(model.domain.class_var.values)

    def predict_proba(self, row: Sequence[float]) -> Sequence[float]:
        import numpy as np
        from Orange.data import Table

        table = Table.from_numpy(self._domain, np.asarray([row], dtype=float))
        return self.model(table, self.model.Probs)[0]


def load_orange_model(path: str) -> OrangeModel:
    if not Path(path).is_file():
        raise ModelLoadError(f"Modèle Orange introuvable : {path}")
    try:
        import Orange  # noqa: F401
    except ImportError as exc:
        raise ModelLoadError(
            "Le paquet orange3 n'est pas installé : `pip install .[orange]`, ou convertissez le modèle "
            "avec tools/export_orange_to_joblib.py et utilisez PREDICTOR=sklearn."
        ) from exc
    with open(path, "rb") as fh:
        model = pickle.load(fh)  # noqa: S301 — fichier de confiance produit par Orange
    return OrangeModel(path, model)


class OrangePredictor(ModelPredictor):
    load_model = staticmethod(load_orange_model)
