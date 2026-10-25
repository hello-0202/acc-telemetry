"""装配层：FastAPI 生命周期 + 采集/记录/广播三个 Task。

启动顺序：记录 Task → 广播 Task → 采集 Task（下游先就绪）。
停止顺序：stop_event.set() → 采集退出 → 记录排空 → 广播退出。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from acc_telemetry.api import config as config_api
from acc_telemetry.api import sessions as sessions_api
from acc_telemetry.config import DB_PATH, VERSION, ensure_db_dir
from acc_telemetry.db import Database
from acc_telemetry.providers.acc_provider import AccSharedMemoryProvider
from acc_telemetry.providers.base import TelemetryProvider
from acc_telemetry.repositories.config_repo import ConfigRepository
from acc_telemetry.repositories.lap_repo import LapRepository
from acc_telemetry.repositories.sample_repo import SampleRepository
from acc_telemetry.repositories.session_repo import SessionRepository
from acc_telemetry.repositories.track_repo import TrackRepository
from acc_telemetry.services.geometry_service import GeometryService
from acc_telemetry.services.history_service import HistoryService
from acc_telemetry.trackmaps.resolver import TrackResolver

from .. import bus
from ..collector.reader import collect_loop
from ..storage.recorder import Recorder
from . import routes
from .broadcaster import broadcast_worker

logger = logging.getLogger(__name__)


def create_app(
    provider_factory: Callable[[], TelemetryProvider] = AccSharedMemoryProvider,
    db_path: str | Path | None = None,
    *,
    collector_hz: float = 100,
    broadcast_hz: float = 50,
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
        bus.reset()

        tracks = TrackRepository(db)
        sessions = SessionRepository(db)
        laps = LapRepository(db)
        samples = SampleRepository(db)
        config_repo = ConfigRepository(db)
        config_values = config_repo.get_all()
        bus.runtime["sample_hz"] = int(config_values.get("sampleHz", 100))
        bus.runtime["push_hz"] = int(config_values.get("pushHz", 50))
        history = HistoryService(tracks, sessions, laps, samples)
        resolver = TrackResolver()
        geometry = GeometryService(tracks)
        provider = provider_factory()
        recorder = Recorder(provider, history, resolver, tracks, geometry)

        app.state.db = db
        app.state.history = history
        app.state.config_repo = config_repo
        app.state.provider = provider
        app.state.provider_factory = provider_factory
        app.state.recorder = recorder
        # 兼容 acc_telemetry.api.sessions 的依赖（它读取 app.state.telemetry）
        app.state.telemetry = recorder

        def apply_runtime_config(values: dict) -> None:
            """配置保存后立即作用到采集/广播循环。"""
            if "sampleHz" in values:
                bus.runtime["sample_hz"] = int(values["sampleHz"])
            if "pushHz" in values:
                bus.runtime["push_hz"] = int(values["pushHz"])

        app.state.apply_runtime_config = apply_runtime_config

        stop_event = asyncio.Event()
        # 下游先就绪，上游才开跑
        recorder_task = asyncio.create_task(
            recorder.run_worker(stop_event), name="recorder"
        )
        broadcast_task = asyncio.create_task(
            broadcast_worker(stop_event, hz=broadcast_hz), name="broadcast"
        )
        collect_task = asyncio.create_task(
            collect_loop(provider, stop_event, hz=collector_hz), name="collector"
        )
        app.state.stop_event = stop_event
        app.state.tasks = [recorder_task, broadcast_task, collect_task]

        try:
            yield
        finally:
            stop_event.set()
            # 先停采集，确保不会再有新帧；再让记录层排空队列
            await asyncio.gather(collect_task, return_exceptions=True)
            await asyncio.gather(recorder_task, return_exceptions=True)
            await asyncio.gather(broadcast_task, return_exceptions=True)

    application = FastAPI(
        title="ACC Telemetry Async Backend", version=VERSION, lifespan=lifespan
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(routes.router)
    application.include_router(sessions_api.router)
    application.include_router(config_api.router)
    return application


# 供 server.py / PyInstaller 直接使用
app = create_app()
