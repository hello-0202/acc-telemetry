"""实时遥测采集服务：读取 ACC 共享内存 → WS 帧 + 落库。

同一时刻只允许一个采集任务；所有数据库调用放到线程池，避免阻塞 WS 事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from ..config import FRAME_HZ, SAMPLE_INTERVAL, VERSION
from ..providers.base import GameNotRunning, Snapshot, StaticInfo, TelemetryProvider
from ..repositories.config_repo import ConfigRepository
from ..repositories.sample_repo import SampleRepository
from ..repositories.session_repo import SessionRepository
from ..repositories.track_repo import TrackRepository
from ..trackmaps.cars import resolve_car
from ..trackmaps.from_track import build_spline_from_samples, spline_is_degenerate
from ..trackmaps.resolver import ResolvedTrack, TrackResolver
from .history_service import HistoryService
from .lap_processor import LapProcessor, LapResult

logger = logging.getLogger(__name__)

Send = Callable[[dict[str, Any]], Awaitable[None]]


def gear_text(raw_gear: int) -> str:
    """ACC 原始档位 → 显示文本：0=R, 1=N, 2+=前进档。"""
    if raw_gear == 0:
        return "R"
    if raw_gear == 1:
        return "N"
    return str(raw_gear - 1)


class TelemetryService:
    def __init__(
        self,
        provider_factory: Callable[[], TelemetryProvider],
        history: HistoryService,
        resolver: TrackResolver,
        tracks: TrackRepository,
        sessions: SessionRepository,
        samples: SampleRepository,
        config_repo: ConfigRepository,
    ) -> None:
        self.provider_factory = provider_factory
        self.history = history
        self.resolver = resolver
        self.tracks = tracks
        self.sessions = sessions
        self.samples = samples
        self.config_repo = config_repo

        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._state = "IDLE"
        self._track_id: str | None = None
        self._session_id: str | None = None
        self._session_type: str | None = None
        # 当前 session 已记录的圈数；出场圈不计入编号
        self._lap_seq = 0

    # ------------------------------------------------------------------ 状态
    @property
    def is_streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_track_id(self) -> str | None:
        return self._track_id

    @property
    def current_session_id(self) -> str | None:
        return self._session_id

    @property
    def state(self) -> str:
        return self._state

    def health(self) -> dict:
        return {
            "streaming": self.is_streaming,
            "trackId": self._track_id,
            "sessionId": self._session_id,
            "state": self._state,
            "version": VERSION,
        }

    # ------------------------------------------------------------------ 控制
    async def start(self, send: Send, stop_event: asyncio.Event) -> None:
        if self.is_streaming:
            await send(self._error("ALREADY_STREAMING", "已有监测会话正在运行"))
            return
        self._stop_event = stop_event
        provider = self.provider_factory()
        self._task = asyncio.create_task(self._run(provider, send, stop_event))

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=6.0)
            except asyncio.TimeoutError:
                task.cancel()
            except Exception as exc:  # noqa: BLE001
                logger.warning("stream stop error: %s", exc)
        self._task = None
        self._state = "IDLE"
        self._track_id = None
        self._session_id = None
        self._session_type = None

    async def shutdown(self) -> None:
        await self.stop()

    # ------------------------------------------------------------------ 主任务
    async def _run(self, provider: TelemetryProvider, send: Send, stop_event: asyncio.Event) -> None:
        self._state = "OPENING"
        try:
            if not provider.available():
                await send(self._error("GAME_NOT_FOUND", "未找到 ACC 游戏进程，请先进入比赛"))
                return
            provider.open()
            while not stop_event.is_set():
                static = await self._read_static(provider)
                if static is None:
                    await send(self._error("GAME_NOT_FOUND", "无法读取 ACC 静止数据，请确认已进入比赛"))
                    break
                self._state = "STREAMING"
                session_id, track_payload = await self._open_session(static, send)
                changed = await self._stream(provider, send, stop_event, session_id, track_payload, static)
                if stop_event.is_set():
                    break
                if not changed:
                    break
                # 赛道/车辆变化或圈号重置：保持 mmap，开启新 session
                self._state = "OPENING"
        except GameNotRunning:
            await self._safe_error(send, "GAME_EXITED", "ACC 已退出")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 任何异常都不能让 sidecar 崩溃
            logger.exception("telemetry stream failed")
            await self._safe_error(send, "INTERNAL", f"遥测采集失败：{exc}")
        finally:
            provider.close()
            self._state = "IDLE"
            self._track_id = None
            self._session_id = None

    async def _read_static(self, provider: TelemetryProvider) -> StaticInfo | None:
        last: StaticInfo | None = None
        for _ in range(20):
            try:
                static = await asyncio.to_thread(provider.read_static)
            except Exception:
                static = None
            if static is not None:
                last = static
                if static.track_name and static.car_model:
                    return static
            await asyncio.sleep(0.1)
        return last if last is not None and last.track_name else None

    async def _open_session(self, static: StaticInfo, send: Send) -> tuple[str, dict]:
        track = self.resolver.resolve(static.track_name, static.track_configuration)
        track_payload = track.to_payload()
        car_id, car_name, gear_count = resolve_car(static.car_model)
        session_id = uuid.uuid4().hex
        pb_ms: int | None = None
        pb_ref: dict | None = None
        db_ok = True

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        session_type = getattr(static, "session_type", "unknown") or "unknown"

        def persist() -> None:
            self.tracks.upsert(
                id=track.id,
                name=track.name,
                length_m=track.length_m,
                spline=track_payload["spline"],
                sectors=track_payload["sectors"],
                date=now,
            )
            self.sessions.create(
                id=session_id,
                track_id=track.id,
                car_name=car_name,
                date=now,
                session_type=session_type,
            )

        try:
            await asyncio.to_thread(persist)
            pb_ms = await asyncio.to_thread(self.history.pb_ms, track.id)
            pb_ref = await asyncio.to_thread(self.history.pb_reference, track.id)
        except Exception as exc:  # noqa: BLE001
            db_ok = False
            logger.warning("session persistence failed: %s", exc)
            await self._safe_warning(send, "数据库不可用，本次监测不会保存历史")

        # 若该赛道已有实跑生成的真实形状，优先使用它而不是内置近似形状
        try:
            stored = await asyncio.to_thread(self.tracks.get, track.id)
        except Exception:
            stored = None
        if (
            stored
            and stored.get("map_source") == "recorded"
            and not spline_is_degenerate(stored.get("spline") or [])
        ):
            track_payload["spline"] = stored["spline"]
            track_payload["geometryKnown"] = True

        self._track_id = track.id
        self._session_id = session_id
        self._session_type = session_type
        self._lap_seq = 0
        max_rpm = static.max_rpm or 0
        payload = {
            "type": "session",
            "track": track_payload,
            "car": {"id": car_id, "name": car_name},
            "sessionType": session_type,
            "carSpec": {
                "maxRpm": max_rpm,
                "gearCount": gear_count,
                "maxFuel": round(static.max_fuel, 2),
            },
            "pbMs": pb_ms,
            "pbRef": pb_ref,
            "sessionId": session_id,
            "dbOk": db_ok,
        }
        await send(payload)
        return session_id, track_payload

    async def _stream(
        self,
        provider: TelemetryProvider,
        send: Send,
        stop_event: asyncio.Event,
        session_id: str,
        track_payload: dict,
        static: StaticInfo,
    ) -> bool:
        """返回 True 表示需要开启新 session（赛道/车辆变化或圈号重置）。"""
        processor = LapProcessor()
        try:
            snap = provider.read()
        except Exception as exc:  # noqa: BLE001
            await self._safe_error(send, "GAME_EXITED", f"ACC 数据读取失败：{exc}")
            return False
        processor.begin(snap)

        frame_interval = await self._frame_interval()
        next_frame = 0.0
        last_liveness = time.monotonic()
        last_static_check = time.monotonic()

        while not stop_event.is_set():
            tick_start = time.monotonic()
            try:
                snap = provider.read()
            except Exception as exc:  # noqa: BLE001
                await self._safe_error(send, "GAME_EXITED", f"ACC 已退出：{exc}")
                return False

            # 每秒确认共享内存段仍然存在
            if tick_start - last_liveness >= 1.0:
                last_liveness = tick_start
                if not provider.available():
                    await self._safe_error(send, "GAME_EXITED", "ACC 已退出")
                    return False

            # 每 2 秒检查赛道/车辆是否变化
            if tick_start - last_static_check >= 2.0:
                last_static_check = tick_start
                try:
                    current = await asyncio.to_thread(provider.read_static)
                except Exception:
                    current = None
                if current is not None and (
                    current.track_name != static.track_name
                    or current.car_model != static.car_model
                    or current.track_configuration != static.track_configuration
                ):
                    return True

            # 暂停 / 回放：不采样、不推送，等待回到 live
            if snap.status != 2:
                await asyncio.sleep(SAMPLE_INTERVAL)
                continue

            # 会话类型可能晚于「开会话」才写进共享内存（ACC 菜单 / 回放里是 -1），
            # 一旦读到真实类型就把会话行和此前标成 unknown 的圈补写上。
            if self._session_type in (None, "unknown"):
                snap_type = getattr(snap, "session_type", "unknown") or "unknown"
                if snap_type != "unknown":
                    self._session_type = snap_type
                    await self._promote_session_type(session_id, snap_type)

            results = processor.consume(snap)
            reset_detected = processor.consume_reset()
            for result in results:
                # 不保存出场圈；飞驰圈从 L1 开始连续编号
                if result.kind == "out":
                    continue
                self._lap_seq += 1
                result.no = self._lap_seq
                result.session_type = getattr(snap, "session_type", "unknown") or "unknown"
                if result.session_type != "unknown":
                    self._session_type = result.session_type
                payload = await self._persist_lap(session_id, result, send)
                await send(payload)
                await self._maybe_record_track_map(result, send)
            if reset_detected:
                snapshot_type = getattr(snap, "session_type", "unknown") or "unknown"
                type_changed = (
                    snapshot_type != "unknown"
                    and self._session_type not in (None, "unknown")
                    and snapshot_type != self._session_type
                )
                if type_changed:
                    # 排位 → 正赛：圈数归零但仍是同一条记录，只更新当前类型
                    self._session_type = snapshot_type
                else:
                    return True

            if processor.active and tick_start >= next_frame:
                await send(self._frame_payload(snap, processor, self._lap_seq + 1))
                next_frame = tick_start + frame_interval

            elapsed = time.monotonic() - tick_start
            delay = SAMPLE_INTERVAL - elapsed
            if delay > 0:
                await asyncio.sleep(delay)

        return False

    async def _frame_interval(self) -> float:
        try:
            config = await asyncio.to_thread(self.config_repo.get_all)
            hz = int(config.get("pushHz", FRAME_HZ))
        except Exception:
            hz = FRAME_HZ
        hz = max(5, min(60, hz))
        return 1.0 / hz

    async def _promote_session_type(self, session_id: str, session_type: str) -> None:
        """把「未标注」的会话补写成真实类型（只补 unknown，不覆盖已有值）。"""
        try:
            changed = await asyncio.to_thread(self.sessions.set_type, session_id, session_type)
            if changed:
                logger.info("session %s type promoted to %s", session_id, session_type)
        except Exception as exc:  # noqa: BLE001 - 补写失败不影响采集
            logger.warning("session type promote failed: %s", exc)

    async def _persist_lap(self, session_id: str, result: LapResult, send: Send) -> dict:
        persisted = True
        try:
            await asyncio.to_thread(self.history.save_lap, session_id, result)
        except Exception as exc:  # noqa: BLE001
            persisted = False
            logger.warning("lap persistence failed: %s", exc)
            await self._safe_warning(send, f"本圈保存失败：{exc}")

        pb_ms: int | None = None
        try:
            pb_ms = await asyncio.to_thread(self.history.pb_ms, self._track_id) if self._track_id else None
        except Exception:
            pb_ms = None

        return {
            "type": "lap",
            "no": result.no,
            "ms": result.ms,
            "kind": result.kind,
            "valid": result.valid,
            "s1": result.s1,
            "s2": result.s2,
            "s3": result.s3,
            "sessionType": result.session_type,
            "pb": bool(result.kind == "ok" and pb_ms is not None and pb_ms == result.ms),
            "pbMs": pb_ms,
            "sampleCount": len(result.samples),
            "persisted": persisted,
        }

    async def _maybe_record_track_map(self, result: LapResult, send: Send) -> None:
        """用第一个有效圈的世界坐标生成真实赛道形状，之后所有 session 复用。"""
        if result.kind != "ok" or not self._track_id:
            return
        try:
            stored = await asyncio.to_thread(self.tracks.get, self._track_id)
            if stored is None:
                return
            if stored.get("map_source") == "recorded" and not spline_is_degenerate(
                stored.get("spline") or []
            ):
                return
            spline = await asyncio.to_thread(build_spline_from_samples, result.samples)
            if not spline:
                return
            await asyncio.to_thread(
                self.tracks.upsert,
                id=stored["id"],
                name=stored["name"],
                length_m=stored["length_m"],
                spline=spline,
                sectors=stored["sectors"],
                date=stored.get("date") or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                source="recorded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("track map generation failed: %s", exc)
            return

        try:
            await send(
                {
                    "type": "trackmap",
                    "trackId": self._track_id,
                    "spline": spline,
                    "geometryKnown": True,
                }
            )
        except Exception:
            pass

    @staticmethod
    def _frame_payload(snap: Snapshot, processor: LapProcessor, lap_no: int) -> dict:
        pressure = snap.wheel_pressure
        temp = snap.tyre_core_temp
        brake_temp = snap.brake_temp
        corners = ("FL", "FR", "RL", "RR")
        return {
            "type": "frame",
            "t": int(time.time() * 1000),
            "lap": lap_no,
            "lapTime": snap.lap_time_ms,
            "valid": bool(snap.valid_lap and not processor.invalid_latched),
            "speed": round(snap.speed_kmh, 1),
            "rpm": snap.rpm,
            "gear": snap.gear,
            "gearText": gear_text(snap.gear),
            "currentMaxRpm": snap.current_max_rpm,
            "throttle": round(snap.throttle, 3),
            "brake": round(snap.brake, 3),
            "steer": round(snap.steer, 3),
            "progress": round(snap.progress, 5),
            "airTemp": round(float(getattr(snap, "air_temp", 0.0)), 1),
            "roadTemp": round(float(getattr(snap, "road_temp", 0.0)), 1),
            "rainIntensity": int(getattr(snap, "rain_intensity", 0)),
            "trackGrip": int(getattr(snap, "track_grip", 0)),
            "wheels": [
                {
                    "corner": corners[i],
                    "pressure": round(pressure[i], 2),
                    "temp": round(temp[i], 1),
                    "brakeTemp": round(brake_temp[i], 1),
                }
                for i in range(4)
            ],
        }

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _error(code: str, message: str) -> dict:
        # `error` 键保留给旧版页面；`type/code/message` 是契约格式
        return {"type": "error", "code": code, "message": message, "error": message}

    async def _safe_error(self, send: Send, code: str, message: str) -> None:
        try:
            await send(self._error(code, message))
        except Exception:
            pass

    async def _safe_warning(self, send: Send, message: str) -> None:
        try:
            await send({"type": "warning", "message": message})
        except Exception:
            pass
