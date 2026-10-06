#!/usr/bin/env python
"""Régénère les JSON Schema des contrats (docs/schemas/) à partir des modèles Pydantic.

    python tools/export_schemas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detection_service.schemas import AlertPayload, CameraMessage, SensorMessage  # noqa: E402

OUT = ROOT / "docs" / "schemas"
SCHEMAS = {
    "sensor_message.schema.json": (SensorMessage, "validation"),
    "camera_message.schema.json": (CameraMessage, "validation"),
    "alert_payload.schema.json": (AlertPayload, "serialization"),
}


def render() -> dict[str, str]:
    return {
        name: json.dumps(model.model_json_schema(mode=mode), indent=2, ensure_ascii=False) + "\n"
        for name, (model, mode) in SCHEMAS.items()
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in render().items():
        (OUT / name).write_text(content, encoding="utf-8")
        print(f"écrit : {OUT / name}")


if __name__ == "__main__":
    main()
