"""API HTTP du service : santé, dernier statut, enregistrement de sessions étiquetées, admin,
commandes vers les ESP (seul point d'envoi MQTT vers les appareils, appelé par backend-api)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Path

from .commands import BrokerUnavailable
from .predictors import ModelLoadError
from .schemas import (AlertCommand, AlertPayload, BuzzerCommand, LedCommand, RecordingStart, RecordingStop,
                      ScreenCommand, iso_ms, to_utc)

if TYPE_CHECKING:
    from .service import Service


def _iso(ts: float | None) -> str | None:
    return None if ts is None else iso_ms(to_utc(ts))


def _session(s: Any) -> dict[str, Any]:
    return {"session_id": str(s.id), "device_id": s.device_id, "label": s.label, "notes": s.notes,
            "started_at": _iso(s.started_at), "ended_at": _iso(s.ended_at)}


# Jamais de / + # : l'id entre dans le topic MQTT
DeviceId = Annotated[str, Path(pattern=r"^[\w.-]{1,64}$")]


def create_app(service: Service) -> FastAPI:
    app = FastAPI(title="Detection service", version="0.1.0")
    engine = service.engine

    def require_admin(authorization: str | None = Header(None)) -> None:
        token = service.settings.admin_token
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "jeton admin invalide")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        mqtt = service.dispatcher.stats
        db_ok: bool | None = None
        if service.db_enabled and service.db_ready:
            try:
                db_ok = await asyncio.wait_for(service.storage.ping(), timeout=1.0)
            except Exception:
                db_ok = False
        w, b = service.writer.stats, service.sender.stats
        now = time.time()
        degraded = not mqtt.connected or (service.db_enabled and not db_ok)
        return {
            "status": "degraded" if degraded else "ok",
            "uptime_s": round(now - service.started_at, 1),
            "last_tick_at": _iso(engine.last_tick_at),
            "mqtt": {"connected": mqtt.connected, "received": mqtt.received, "invalid": mqtt.invalid,
                     "invalid_by_reason": mqtt.invalid_by_reason, "last_message_at": _iso(mqtt.last_message_at),
                     "last_error": mqtt.last_error},
            "database": {"enabled": service.db_enabled, "ready": service.db_ready, "reachable": db_ok,
                         "writes_healthy": w.healthy, "buffered_rows": service.writer.buffered,
                         "written": w.written, "dropped": w.dropped, "last_error": w.last_error},
            "backend": {"enabled": service.sender.enabled, "url": service.sender.url,
                        "queue": len(service.sender.queue), "sent": b.sent, "failed": b.failed,
                        "dropped_heartbeats": b.dropped_heartbeats, "dropped_changes": b.dropped_changes,
                        "last_error": b.last_error, "last_success_at": _iso(b.last_success_at)},
            "model": {**engine.predictor.describe(), "loaded_at": _iso(service.model_loaded_at),
                      "predict_errors": engine.predict_errors, "last_reload_error": service.model_reload_error},
            "devices": {
                d.device_id: {
                    "last_sensor_at": _iso(d.last_sensor_at),
                    "last_camera_at": _iso(d.last_camera_at),
                    "seconds_since_last_sensor": None if d.last_sensor_at is None
                    else round(now - d.last_sensor_at, 1),
                    "device_state": d.last_result.device_state if d.last_result else None,
                    "status": d.last_result.status if d.last_result else None,
                    "gas_baseline": d.baseline.value,
                    "recording": _session(d.recording) if d.recording else None,
                }
                for d in engine.devices.values()
            },
        }

    @app.get("/status/{device_id}", response_model=AlertPayload)
    async def status(device_id: str) -> AlertPayload:
        dev = engine.devices.get(device_id)
        if dev is None or dev.last_result is None:
            raise HTTPException(404, f"aucun résultat pour {device_id}")
        return engine.build_payload(dev.last_result, "heartbeat")

    @app.get("/recording")
    async def recording_list() -> list[dict[str, Any]]:
        return [_session(d.recording) for d in engine.devices.values() if d.recording]

    @app.post("/recording/start", status_code=201)
    async def recording_start(body: RecordingStart) -> dict[str, Any]:
        session = engine.start_recording(body.device_id, body.label, body.notes)
        return _session(session)

    @app.post("/recording/stop")
    async def recording_stop(body: RecordingStop | None = None) -> dict[str, Any]:
        stopped = engine.stop_recording(body.device_id if body else None)
        if not stopped:
            raise HTTPException(404, "aucune session en cours")
        return {"stopped": [_session(s) for s in stopped]}

    # --- Commandes ESP : publiées sur sentinelx/{device_id}/cmd, réponse = acquittement de l'ESP ---
    # 200 {command, state: {alert, buzzer, led, screen}} ; 422 refusée par l'ESP ;
    # 503 broker injoignable ; 504 aucun acquittement (ESP hors ligne)
    async def command(device_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            ack = await service.commands.send(device_id, body)
        except BrokerUnavailable as exc:
            raise HTTPException(503, "broker MQTT injoignable") from exc
        if ack is None:
            raise HTTPException(504, f"pas d'acquittement de {device_id} (hors ligne ?)")
        if not ack.ok:
            raise HTTPException(422, f"commande refusée par l'appareil : {(ack.error or '')[:100]}")
        return {"command": ack.command, "state": ack.state}

    @app.post("/devices/{device_id}/alert", dependencies=[Depends(require_admin)])
    async def command_alert(device_id: DeviceId, body: AlertCommand) -> dict[str, Any]:
        return await command(device_id, {"command": "alert", **body.model_dump()})

    @app.post("/devices/{device_id}/buzzer", dependencies=[Depends(require_admin)])
    async def command_buzzer(device_id: DeviceId, body: BuzzerCommand) -> dict[str, Any]:
        return await command(device_id, {"command": "buzzer", **body.model_dump()})

    @app.post("/devices/{device_id}/led", dependencies=[Depends(require_admin)])
    async def command_led(device_id: DeviceId, body: LedCommand) -> dict[str, Any]:
        return await command(device_id, {"command": "led", **body.model_dump()})

    @app.post("/devices/{device_id}/screen", dependencies=[Depends(require_admin)])
    async def command_screen(device_id: DeviceId, body: ScreenCommand) -> dict[str, Any]:
        return await command(device_id, {"command": "screen", **body.model_dump(exclude_none=True)})

    @app.post("/devices/{device_id}/reset", dependencies=[Depends(require_admin)])
    async def command_reset(device_id: DeviceId) -> dict[str, Any]:
        return await command(device_id, {"command": "reset"})

    @app.post("/admin/reload-model", dependencies=[Depends(require_admin)])
    async def reload_model() -> dict[str, Any]:
        try:
            return {"reloaded": True, "model": service.reload_model()}
        except ModelLoadError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
