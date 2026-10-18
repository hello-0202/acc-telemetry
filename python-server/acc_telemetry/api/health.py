"""健康检查。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import VERSION

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    db = request.app.state.db
    telemetry = request.app.state.telemetry
    try:
        game_running = bool(request.app.state.provider_factory().available())
    except Exception:
        game_running = False
    return {
        "ok": True,
        "gameRunning": game_running,
        "dbOk": bool(db.is_ok),
        "dbPath": str(db.path),
        "streaming": telemetry.is_streaming,
        "trackId": telemetry.current_track_id,
        "sessionId": telemetry.current_session_id,
        "version": VERSION,
    }
