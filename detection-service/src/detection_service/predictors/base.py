"""Interface commune des predictors.

Un predictor reçoit le dictionnaire de features d'une fenêtre et renvoie, pour chaque
type d'alerte (`feu`, `fuite_gaz`, `presence`), une probabilité entre 0 et 1.
Les deux modes de modèle (multi-classe / multi-label) produisent la même sortie.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..config import ALERT_TYPES
from ..features import FEATURE_NAMES

POSITIVE_NAMES = ("1", "true", "yes", "oui", "y", "o")
NEGATIVE_NAMES = ("0", "false", "no", "non", "n", "aucune", "aucun", "none", "normal", "rien")


class ModelLoadError(RuntimeError):
    """Modèle introuvable ou incompatible avec nos features. Le message doit dire quoi corriger."""


class Predictor(ABC):
    version: str = "unknown"

    @abstractmethod
    def predict(self, features: Mapping[str, float]) -> dict[str, float]:
        """Probabilité de chaque type d'alerte (clés = ALERT_TYPES)."""

    def describe(self) -> dict[str, object]:
        return {"kind": type(self).__name__, "version": self.version}


class ProbModel(Protocol):
    """Un modèle Orange chargé : features ordonnées, classes, probabilités."""

    path: str
    feature_names: Sequence[str]
    class_names: Sequence[str]

    def predict_proba(self, row: Sequence[float]) -> Sequence[float]: ...


def check_features(model: ProbModel) -> None:
    missing = [name for name in model.feature_names if name not in FEATURE_NAMES]
    if missing:
        raise ModelLoadError(
            f"Le modèle {model.path} attend des colonnes que le service ne calcule pas : {missing}. "
            f"Colonnes disponibles : {list(FEATURE_NAMES)}. Dans Orange, passez « label » en cible "
            "et « session_id » (et toute autre colonne) en méta avant d'entraîner."
        )


def build_row(model: ProbModel, features: Mapping[str, float]) -> list[float]:
    """Valeurs dans l'ordre exact attendu par le modèle (NaN = valeur manquante)."""
    row = []
    for name in model.feature_names:
        v = features.get(name)
        row.append(float("nan") if v is None else float(v))
    return row


def positive_index(model: ProbModel, alert_type: str, negative_class: str) -> int:
    """Pour un modèle binaire : index de la classe « alerte présente »."""
    names = [c.strip().lower() for c in model.class_names]
    for candidate in (alert_type.lower(), *POSITIVE_NAMES):
        if candidate in names:
            return names.index(candidate)
    negatives = {negative_class.lower(), *NEGATIVE_NAMES}
    if len(names) == 2:
        for i, name in enumerate(names):
            if name in negatives:
                return 1 - i
    raise ModelLoadError(
        f"Modèle binaire {model.path} pour « {alert_type} » : impossible d'identifier la classe positive "
        f"parmi {list(model.class_names)}. Utilisez par exemple les classes « {alert_type} » / "
        f"« {negative_class} » ou « 1 » / « 0 »."
    )


def clamp01(x: float) -> float:
    return 0.0 if math.isnan(x) else min(1.0, max(0.0, x))


class ModelPredictor(Predictor):
    """Socle des predictors à modèles. Les sous-classes fournissent `load_model(path)`."""

    def __init__(self, mode: str, models: Mapping[str, ProbModel], version: str, negative_class: str = "aucune"):
        self.mode = mode
        self.models = dict(models)
        self.version = version
        self.negative_class = negative_class
        for model in {id(m): m for m in self.models.values()}.values():
            check_features(model)
        if mode == "multiclass":
            model = self.models["*"]
            known = [t for t in ALERT_TYPES if t in model.class_names]
            if not known:
                raise ModelLoadError(
                    f"Modèle multi-classe {model.path} : aucune classe parmi {list(ALERT_TYPES)} "
                    f"(classes trouvées : {list(model.class_names)})."
                )
            self._indices = {t: list(model.class_names).index(t) for t in known}
        else:
            self._indices = {t: positive_index(m, t, negative_class) for t, m in self.models.items()}

    @classmethod
    def from_paths(cls, mode: str, model_path: str | None, model_paths: Mapping[str, str],
                   version: str, negative_class: str = "aucune") -> ModelPredictor:
        if mode == "multiclass":
            if not model_path:
                raise ModelLoadError("MODEL_MODE=multiclass nécessite MODEL_PATH")
            models = {"*": cls.load_model(str(model_path))}
        else:
            unknown = set(model_paths) - set(ALERT_TYPES)
            if unknown:
                raise ModelLoadError(f"MODEL_PATHS : types inconnus {sorted(unknown)} (attendus : {list(ALERT_TYPES)})")
            if not model_paths:
                raise ModelLoadError('MODEL_MODE=multilabel nécessite MODEL_PATHS, ex. {"presence": "/models/p.pkcls"}')
            models = {t: cls.load_model(str(p)) for t, p in model_paths.items()}
        return cls(mode, models, version, negative_class)

    @staticmethod
    def load_model(path: str) -> ProbModel:  # pragma: no cover - surchargé
        raise NotImplementedError

    def predict(self, features: Mapping[str, float]) -> dict[str, float]:
        out = dict.fromkeys(ALERT_TYPES, 0.0)
        if self.mode == "multiclass":
            model = self.models["*"]
            probs = model.predict_proba(build_row(model, features))
            for t, i in self._indices.items():
                out[t] = clamp01(float(probs[i]))
        else:
            for t, model in self.models.items():
                probs = model.predict_proba(build_row(model, features))
                out[t] = clamp01(float(probs[self._indices[t]]))
        return out

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "mode": self.mode,
            "models": {t: {"path": m.path, "features": list(m.feature_names), "classes": list(m.class_names)}
                       for t, m in self.models.items()},
            "missing_alert_types": [t for t in ALERT_TYPES
                                    if self.mode == "multilabel" and t not in self.models],
        }
