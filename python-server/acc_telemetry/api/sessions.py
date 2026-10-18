"""历史记录接口：每条 session（每次进图）是一条独立记录。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import Database
from .deps import get_history, get_telemetry, require_db

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions")
def list_sessions(request: Request, _db: Database = Depends(require_db)) -> list[dict]:
    history = get_history(request)
    telemetry = get_telemetry(request)
    return history.list_sessions(
        live_session_id=telemetry.current_session_id,
        streaming=telemetry.is_streaming,
    )


@router.get("/sessions/{session_id}/laps")
def list_laps(
    session_id: str,
    request: Request,
    _db: Database = Depends(require_db),
) -> list[dict]:
    laps = get_history(request).list_laps(session_id)
    if laps is None:
        raise HTTPException(status_code=404, detail="session not found")
    return laps


@router.get("/sessions/{session_id}/laps/{no}")
def lap_detail(
    session_id: str,
    no: int,
    request: Request,
    _db: Database = Depends(require_db),
) -> dict:
    detail = get_history(request).get_lap(session_id, no)
    if detail is None:
        raise HTTPException(status_code=404, detail="lap not found")
    return detail


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    request: Request,
    _db: Database = Depends(require_db),
) -> dict:
    telemetry = get_telemetry(request)
    if telemetry.is_streaming and telemetry.current_session_id == session_id:
        raise HTTPException(status_code=409, detail="session is currently streaming")
    deleted = get_history(request).delete_session(session_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "sessionId": session_id, "deleted": deleted}


@router.delete("/sessions")
def clear_sessions(request: Request, _db: Database = Depends(require_db)) -> dict:
    telemetry = get_telemetry(request)
    if telemetry.is_streaming:
        raise HTTPException(status_code=409, detail="stop monitoring before clearing history")
    deleted = get_history(request).clear_all()
    return {"ok": True, "deleted": deleted}
