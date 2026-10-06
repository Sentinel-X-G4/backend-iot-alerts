from __future__ import annotations

from ..config import PredictorKind, Settings
from .base import ModelLoadError, Predictor
from .rules import RuleBasedPredictor

__all__ = ["ModelLoadError", "Predictor", "RuleBasedPredictor", "create_predictor"]


def create_predictor(settings: Settings) -> Predictor:
    """Construit le predictor configuré. Lève ModelLoadError avec un message explicite."""
    if settings.predictor == PredictorKind.RULES:
        return RuleBasedPredictor(settings.rules_gas_delta, settings.rules_temp_slope_c_per_min,
                                  settings.model_version)
    if settings.predictor == PredictorKind.ORANGE:
        from .orange import OrangePredictor as cls
    else:
        from .sklearn import SklearnPredictor as cls  # type: ignore[assignment]
    return cls.from_paths(
        settings.model_mode.value,
        str(settings.model_path) if settings.model_path else None,
        {t: str(p) for t, p in settings.model_paths.items()},
        settings.model_version,
        settings.model_negative_class,
    )
