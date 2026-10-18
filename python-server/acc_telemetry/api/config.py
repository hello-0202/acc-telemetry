"""配置读写。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..config import VERSION
from ..db import Database
from .deps import get_config_repo, require_db

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_config(request: Request, _db: Database = Depends(require_db)) -> dict:
    values = get_config_repo(request).get_all()
    return {
        "pushHz": int(values.get("pushHz", 50)),
        "autoStart": bool(values.get("autoStart", True)),
        "dbPath": str(request.app.state.db.path),
        "version": VERSION,
    }


@router.post("/config")
def save_config(
    payload: dict,
    request: Request,
    _db: Database = Depends(require_db),
) -> dict:
    clean: dict = {}
    if "pushHz" in payload:
        try:
            clean["pushHz"] = max(5, min(60, int(payload["pushHz"])))
        except (TypeError, ValueError):
            pass
    if "autoStart" in payload:
        clean["autoStart"] = bool(payload["autoStart"])
    if clean:
        get_config_repo(request).set_many(clean)
    return get_config(request)
