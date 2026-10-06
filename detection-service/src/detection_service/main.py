"""Point d'entrée : `python -m detection_service.main` ou `detection-service`."""

from __future__ import annotations

import asyncio
import logging
import sys

from .config import Settings
from .logging_setup import setup_logging
from .predictors import ModelLoadError


def run() -> None:
    settings = Settings()
    setup_logging(settings.log_level, settings.log_json)
    from .service import Service

    try:
        service = Service(settings)
    except ModelLoadError as exc:
        logging.getLogger("detection_service").critical("modèle invalide : %s", exc)
        sys.exit(2)
    asyncio.run(service.run())


if __name__ == "__main__":
    run()
