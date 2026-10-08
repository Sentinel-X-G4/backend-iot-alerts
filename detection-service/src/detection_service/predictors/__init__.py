from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from ..config import ALERT_TYPES, ModelMode, Settings
from .base import ModelLoadError, Predictor

__all__ = ["ModelLoadError", "Predictor", "create_predictor", "find_models"]

MODEL_SUFFIX = ".pkcls"
MULTICLASS_FILE = "model.pkcls"


def find_models(settings: Settings) -> tuple[Path | None, dict[str, Path]]:
    """(modèle multi-classe, modèles multi-label) : chemins explicites, sinon fichiers de MODELS_DIR.

    Multi-label : `<type>.pkcls` pour chaque type présent (feu, fuite_gaz, inondation, presence).
    Multi-classe : `model.pkcls`.
    """
    if settings.model_mode == ModelMode.MULTICLASS:
        if settings.model_path:
            return settings.model_path, {}
        path = settings.models_dir / MULTICLASS_FILE
        return (path if path.is_file() else None), {}
    if settings.model_paths:
        return None, dict(settings.model_paths)
    found = {t: settings.models_dir / f"{t}{MODEL_SUFFIX}" for t in ALERT_TYPES}
    return None, {t: p for t, p in found.items() if p.is_file()}


def fingerprint(paths: Iterable[Path]) -> str:
    """Version dérivée du contenu des fichiers : change dès qu'un modèle est remplacé."""
    h = hashlib.sha256()
    for path in sorted(paths, key=str):
        h.update(path.read_bytes())
    return f"orange-{h.hexdigest()[:8]}"


def create_predictor(settings: Settings) -> Predictor:
    """Charge les modèles Orange configurés. Lève ModelLoadError avec un message explicite."""
    model_path, model_paths = find_models(settings)
    if not (model_path or model_paths):
        expected = MULTICLASS_FILE if settings.model_mode == ModelMode.MULTICLASS else \
            ", ".join(f"{t}{MODEL_SUFFIX}" for t in ALERT_TYPES)
        raise ModelLoadError(f"aucun modèle dans {settings.models_dir} (attendu : {expected})")
    from .orange import OrangePredictor

    predictor = OrangePredictor.from_paths(
        settings.model_mode.value,
        str(model_path) if model_path else None,
        {t: str(p) for t, p in model_paths.items()},
        "",
        settings.model_negative_class,
    )
    predictor.version = settings.model_version or fingerprint([model_path] if model_path else model_paths.values())
    return predictor
