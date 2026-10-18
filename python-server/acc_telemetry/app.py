"""FastAPI 应用工厂与生命周期。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import config as config_api
from .api import health as health_api
from .api import sessions as sessions_api
from .api import ws as ws_api
from .config import DB_PATH, VERSION, ensure_db_dir
from .db import Database
from .providers.acc_provider import AccSharedMemoryProvider
from .providers.base import TelemetryProvider
from .repositories.config_repo import ConfigRepository
from .repositories.lap_repo import LapRepository
from .repositories.sample_repo import SampleRepository
from .repositories.session_repo import SessionRepository
from .repositories.track_repo import TrackRepository
from .services.history_service import HistoryService
from .services.telemetry_service import TelemetryService
from .trackmaps.resolver import TrackResolver


def create_app(
    provider_factory: Callable[[], TelemetryProvider] = AccSharedMemoryProvider,
    db_path: str | Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = Path(db_path) if db_path is not None else DB_PATH
        if db_path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            ensure_db_dir()

        db = Database(path)
        db.initialize()

        tracks = TrackRepository(db)
        sessions = SessionRepository(db)
        laps = LapRepository(db)
        samples = SampleRepository(db)
        config_repo = ConfigRepository(db)
        history = HistoryService(tracks, sessions, laps, samples)
        resolver = TrackResolver()
        telemetry = TelemetryService(
            provider_factory=provider_factory,
            history=history,
            resolver=resolver,
            tracks=tracks,
            sessions=sessions,
            samples=samples,
            config_repo=config_repo,
        )

        app.state.db = db
        app.state.history = history
        app.state.telemetry = telemetry
        app.state.config_repo = config_repo
        app.state.provider_factory = provider_factory
        try:
            yield
        finally:
            await telemetry.shutdown()

    application = FastAPI(title="ACC Telemetry", version=VERSION, lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_api.router)
    application.include_router(sessions_api.router)
    application.include_router(config_api.router)
    application.include_router(ws_api.router)
    return application


app = create_app()
