"""路由依赖：从 app.state 取单例。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..db import Database
from ..repositories.config_repo import ConfigRepository
from ..services.history_service import HistoryService
from ..services.telemetry_service import TelemetryService


def get_db(request: Request) -> Database:
    return request.app.state.db


def require_db(request: Request) -> Database:
    db: Database = request.app.state.db
    if not db.is_ok:
        raise HTTPException(status_code=503, detail="database unavailable")
    return db


def get_history(request: Request) -> HistoryService:
    return request.app.state.history


def get_telemetry(request: Request) -> TelemetryService:
    return request.app.state.telemetry


def get_config_repo(request: Request) -> ConfigRepository:
    return request.app.state.config_repo
