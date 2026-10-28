"""记录 Task：消费 FramePacket，识别圈次、落库、产生一次性事件。

它替代了原来 TelemetryService 里的“采集 + 落库 + 推送”串行逻辑：
- 采集由 collector/reader.py 负责；
- 帧推送由 api/broadcaster.py 负责；
- 这里只做圈次状态机 + SQLite 写入（单写者）。

事件通过 bus.event_queue 交给广播 Task，避免在存储 Task 里 await 网络。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from acc_telemetry.services.history_service import HistoryService
from acc_telemetry.services.lap_processor import LapProcessor, LapResult
from acc_telemetry.repositories.track_repo import TrackRepository
from acc_telemetry.services.geometry_service import GeometryService
from acc_telemetry.trackmaps.cars import resolve_car
from acc_telemetry.trackmaps.from_track import (
    build_spline_from_geometry,
    build_spline_from_samples,
    spline_is_degenerate,
)
from acc_telemetry.trackmaps.resolver import TrackResolver

from .. import bus

logger = logging.getLogger(__name__)


# 滚动缓冲上限（帧数，约 60 秒 @ 60fps）。监测未开启时也持续保留最近的若干帧，
# 点「开始监测」时回放，使第一圈从真正的过线点（t≈0）起算，而不是从开启记录那一刻起算。
PREBUF_MAX_FRAMES = 3600


def _trim_lead_in(buf: list) -> list:
    """从滚动缓冲里只保留「最近一次 completedLaps 变化之后」的帧。

    点「开始监测」那一刻往往已经在某圈中途，缓冲里混着上一圈的尾段（出场圈之前 /
    上一圈）。把这些属于上一圈的尾段裁掉，让回放的第一圈从最近一次过线点
    （completedLaps 变化）开始、t≈0 起算；若缓冲里没有过线点则原样返回。
    """
    if len(buf) < 2:
        return buf
    target = buf[-1].snapshot.completed_laps
    cut = 0
    for i in range(len(buf) - 1):
        if (
            buf[i].snapshot.completed_laps != buf[i + 1].snapshot.completed_laps
            and buf[i + 1].snapshot.completed_laps == target
        ):
            cut = i + 1
    return buf[cut:]


class Recorder:
    def __init__(
        self,
        provider: Any,
        history: HistoryService,
        resolver: TrackResolver,
        tracks: TrackRepository,
        geometry: GeometryService,
    ) -> None:
        self.provider = provider
        self.history = history
        self.resolver = resolver
        self.tracks = tracks
        self.geometry = geometry

        self._recording = False
        self._processor: LapProcessor | None = None
        self._session_id: str | None = None
        self._track_id: str | None = None
        self._static: Any | None = None
        self._session_type: str | None = None
        self._lap_seq = 0
        self._frame_count = 0
        # 连续失败计数（只用于降低日志噪声，见 _safe_process）
        self._process_errors = 0
        # 延迟落库：session 行在收到第一帧真实遥测时才写入，避免空等也留空 session
        self._session_persisted = False
        self._session_meta: tuple | None = None
        # 滚动缓冲：监测未开启时也持续保留最近的若干帧。点「开始监测」时把其中
        # 「最近一次过线之后」的帧回放给圈次状态机，使第一圈从真正的过线点（t≈0）
        # 起算，而不是从「开启记录这一刻」起算——否则第一圈开头一段轨迹会缺失。
        self._pre_buffer: list = []
        # 本次会话解析到的 fastlane 几何：开记录时 tracks 行可能还不存在
        # （session 延迟落库），先留在内存，建行后由 _persist_session 补写。
        self._geometry: dict | None = None
        # 发布给主题状态快照（bus.runtime["state_meta"]）的会话元信息：
        # 赛道 / 车辆 / PB 参考。state.py 只读这些，不去碰数据库。
        self._track_payload: dict | None = None
        self._car_name: str = ""
        self._pb_ms: int | None = None
        self._pb_ref: dict | None = None

    # ------------------------------------------------------------------ 状态
    @property
    def is_streaming(self) -> bool:
        return self._recording

    @property
    def current_session_id(self) -> str | None:
        return self._session_id

    @property
    def current_track_id(self) -> str | None:
        return self._track_id

    # ------------------------------------------------------------------ 控制
    async def start(self) -> None:
        if self._recording:
            return

        bus.runtime["state_error"] = None
        static = await self._wait_for_static()
        if static is None:
            self._emit(
                self._error("GAME_NOT_FOUND", "未找到 ACC 游戏进程，请先进入比赛")
            )
            return

        # bus.latest_static 是打开共享内存时的快照，可能停在旧会话类型上；
        # 开始记录前重新读一次，保证首条记录的 session_type 正确。
        try:
            fresh = await asyncio.to_thread(self.provider.read_static)
        except Exception:  # noqa: BLE001
            fresh = None
        if fresh is not None and getattr(fresh, "track_name", ""):
            static = fresh

        await self._open_session(static)
        self._processor = LapProcessor()
        self._lap_seq = 0

        # 开启记录；run_worker 会先把滚动缓冲里「最近一次过线之后」的帧回放给圈次状态机，
        # 第一圈由此从真正的过线点（t≈0）起算，而不是从开启记录这一刻起算。
        self._recording = True
        bus.runtime["recording"] = True
        bus.runtime["lap"] = 1

    async def stop(self) -> None:
        if self._processor is not None:
            self._processor.stop()
        self._processor = None
        self._recording = False
        self._session_id = None
        self._track_id = None
        self._static = None
        self._session_type = None
        self._lap_seq = 0
        self._session_persisted = False
        self._session_meta = None
        self._geometry = None
        self._pre_buffer = []
        self._track_payload = None
        self._car_name = ""
        self._pb_ms = None
        self._pb_ref = None
        bus.runtime["state_meta"] = None
        bus.runtime["recording"] = False
        bus.runtime["lap"] = 1
        self._emit({"type": "stopped"})

    async def _wait_for_static(self) -> Any | None:
        """等待采集层读到赛道/车辆信息；游戏没开时返回 None。"""
        for _ in range(20):
            static = bus.latest_static
            if static is not None and getattr(static, "track_name", ""):
                return static
            if not self.provider.available():
                return None
            try:
                static = await asyncio.to_thread(self.provider.read_static)
            except Exception:  # noqa: BLE001
                static = None
            if static is not None and getattr(static, "track_name", ""):
                return static
            await asyncio.sleep(0.1)
        return bus.latest_static

    async def _open_session(self, static: Any, session_type: str | None = None) -> None:
        track = self.resolver.resolve(
            getattr(static, "track_name", ""),
            getattr(static, "track_configuration", ""),
        )
        track_payload = track.to_payload()
        car_id, car_name, gear_count = resolve_car(getattr(static, "car_model", ""))
        session_id = uuid.uuid4().hex
        date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        session_type = (
            session_type
            or getattr(static, "session_type", "unknown")
            or "unknown"
        )
        pb_ms: int | None = None
        pb_ref: dict | None = None
        db_ok = True
        # 会话行延迟到「收到第一帧真实遥测」时才落库（见 _persist_session），
        # 避免点了「开始监测」却停在车库 / 主菜单 / 回放（status≠LIVE）时
        # 没有产生任何数据也写入一条空 session。
        self._session_meta = (
            session_id,
            date,
            session_type,
            track,
            track_payload,
            car_id,
            car_name,
        )
        self._session_persisted = False

        # 此时 track 行可能尚未落库（落库被推迟），读不到 PB 属正常；
        # 第一圈完成后会在 _persist_lap 里重新计算。
        try:
            if session_type and session_type != "unknown":
                pb_ms = await asyncio.to_thread(
                    self.history.pb_ms_for_type, track.id, session_type
                )
                pb_ref = await asyncio.to_thread(
                    self.history.pb_reference_for_type, track.id, session_type
                )
            else:
                pb_ms = await asyncio.to_thread(self.history.pb_ms, track.id)
                pb_ref = await asyncio.to_thread(self.history.pb_reference, track.id)
        except Exception:  # noqa: BLE001
            pb_ms = None
            pb_ref = None

        # 真实赛道几何：数据库有就直接用，没有就解析本地 fastlane.ai 并缓存。
        # get_or_parse 里的 set_geometry 是 UPDATE，而「首次进入某条赛道」时
        # tracks 行还没建（session 落库是延迟的），UPDATE 会静默影响 0 行。
        # 所以几何先留在内存，等 _persist_session 建好行之后再补写。
        geometry = None
        try:
            geometry = await asyncio.to_thread(
                self.geometry.get_or_parse,
                track.id,
                getattr(static, "track_name", ""),
            )
        except Exception:  # noqa: BLE001
            logger.exception("赛道几何解析失败")
        self._geometry = geometry
        track_payload["geometryAvailable"] = bool(geometry)

        # 优先使用真实几何 / 实跑生成的真实赛道形状
        try:
            stored = await asyncio.to_thread(self.tracks.get, track.id)
        except Exception:  # noqa: BLE001
            stored = None
        stored_spline = (stored or {}).get("spline") or []
        if (
            stored
            and stored.get("map_source") in ("recorded", "fastlane")
            and not spline_is_degenerate(stored_spline)
        ):
            track_payload["spline"] = stored_spline
            track_payload["geometryKnown"] = True
        elif geometry:
            # 库里没有可用的赛道形状（新赛道第一次进 / 只有示意形状）：
            # 直接用 fastlane 中心线，别再退回 resolver 的示意折线，
            # 否则前端会先显示一张「默认地图」，直到跑出有效圈才换成实跑走线。
            spline = await asyncio.to_thread(build_spline_from_geometry, geometry)
            if spline and not spline_is_degenerate(spline):
                track_payload["spline"] = spline
                track_payload["geometryKnown"] = True

        self._session_id = session_id
        self._track_id = track.id
        self._static = static
        self._session_type = session_type
        self._track_payload = track_payload
        self._car_name = car_name
        self._pb_ms = pb_ms
        self._pb_ref = pb_ref
        self._publish_meta()

        self._emit(
            {
                "type": "session",
                "track": track_payload,
                "car": {"id": car_id, "name": car_name},
                "sessionType": session_type,
                "carSpec": {
                    "maxRpm": int(getattr(static, "max_rpm", 0) or 0),
                    "gearCount": gear_count,
                    "maxFuel": round(float(getattr(static, "max_fuel", 0.0)), 2),
                },
                "pbMs": pb_ms,
                "pbRef": pb_ref,
                "sessionId": session_id,
                "dbOk": db_ok,
            }
        )

    def _publish_meta(self) -> None:
        """把会话元信息挂到总线，供 `backend/api/state.py` 组装主题状态。

        state 组装必须留在广播侧（只读、无锁、每帧都能跑），所以记录层只负责
        「把静态信息 + PB 参考放上去」，不参与任何推送节奏。
        """
        static = self._static
        if static is None or self._track_id is None:
            bus.runtime["state_meta"] = None
            return
        payload = self._track_payload or {}
        bus.runtime["state_meta"] = {
            "sessionId": self._session_id,
            "sessionType": self._session_type or "unknown",
            "track": {
                # name 用 ACC 内部 id（monza），title 是给主题显示的美化名（Monza）
                "name": self._track_id,
                "title": payload.get("name") or self._track_id,
                "configuration": getattr(static, "track_configuration", "") or "",
                "lengthM": payload.get("lengthM"),
                "sectorCount": int(getattr(static, "sector_count", 0) or 0),
            },
            "car": {
                "name": self._car_name,
                "maxRpm": int(getattr(static, "max_rpm", 0) or 0),
                "maxFuel": float(getattr(static, "max_fuel", 0.0) or 0.0),
                # ACC 的 static 块不填这两项（官方文档标注 Not shown in ACC）
                "maxTorque": 0.0,
                "maxPower": 0.0,
            },
            "pbMs": self._pb_ms,
            # pbRef 只在内存里给 delta 用，绝不进 state 消息（上千点会撑爆 30Hz 推送）
            "pbRef": self._pb_ref,
        }

    async def _persist_session(self) -> None:
        """收到第一帧真实遥测时把 session + track 写入数据库。

        延迟落库可避免「开始监测」后一直没有实际数据却留下空 session 行
        （例如 ACC 停在车库 / 主菜单 / 回放，状态不是 LIVE）。
        """
        if self._session_persisted or self._session_meta is None:
            return
        (
            session_id,
            date,
            session_type,
            track,
            track_payload,
            _car_id,
            car_name,
        ) = self._session_meta

        def _write() -> None:
            self.tracks.upsert(
                id=track.id,
                name=track.name,
                length_m=track.length_m,
                spline=track_payload["spline"],
                sectors=track_payload["sectors"],
                date=date,
            )
            self.history.sessions.create(
                id=session_id,
                track_id=track.id,
                car_name=car_name,
                date=date,
                session_type=session_type,
            )
            # 行建好之后再把 fastlane 几何写进去（开记录时这一行可能还不存在）。
            # 否则 /api/tracks/{id}/geometry 会一直 404，前端只能画示意形状。
            if self._geometry:
                try:
                    self.tracks.set_geometry(track.id, self._geometry)
                    current = self.tracks.get(track.id) or {}
                    if current.get("map_source") != "recorded":
                        spline = build_spline_from_geometry(self._geometry)
                        if spline and not spline_is_degenerate(spline):
                            self.tracks.set_spline(
                                track.id, spline, source="fastlane"
                            )
                except Exception:  # noqa: BLE001
                    logger.exception("缓存赛道几何失败（本次仍用内存数据）")

        try:
            await asyncio.to_thread(_write)
            self._session_persisted = True
            logger.info("session %s 已落库（track=%s）", session_id, track.id)
            # 落库之后再通知前端：历史页收到这条才去拉列表，
            # 保证「新的赛道记录」出现时数据库里已经有这一行
            # （session 事件在落库之前就发出去了，单靠它拉列表可能还看不到新记录）。
            self._emit(
                {"type": "history", "reason": "session", "sessionId": session_id}
            )
        except Exception:  # noqa: BLE001
            logger.exception("创建 session 失败，本次监测不落库")
            self._emit(
                {"type": "warning", "message": "数据库不可用，本次监测不会保存历史"}
            )

    async def _restart_session(self) -> None:
        # 圈号重置后开启新 session；优先重新读 static，避免沿用旧副本
        try:
            static = await asyncio.to_thread(self.provider.read_static)
        except Exception:  # noqa: BLE001
            static = None
        if static is None:
            static = bus.latest_static
        if static is None:
            await self.stop()
            return
        await self._open_session(static)
        self._processor = LapProcessor()
        self._lap_seq = 0
        bus.runtime["lap"] = 1

    # ------------------------------------------------------------------ 消费循环
    async def _safe_process(self, packet: Any) -> None:
        """单帧处理失败绝不能拖死整个记录 Task。

        历史事故（2026-10-26）：`run_worker` 里一个 `AttributeError`
        （`self._trim_lead_in` 写成方法调用，实际是模块级函数）就让记录 Task
        静默退出——前端仍显示「监测中」、实时帧照推，但从此一圈都不落库，
        现象就是「进赛道不记录了」。这里兜住异常并计数，避免再次静默失联。
        """
        try:
            await self._process(packet)
            self._process_errors = 0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._process_errors += 1
            if self._process_errors == 1 or self._process_errors % 500 == 0:
                logger.exception(
                    "记录帧失败（连续/累计 %d 次），已跳过该帧", self._process_errors
                )

    async def run_worker(self, stop_event: asyncio.Event) -> None:
        try:
            while not stop_event.is_set():
                try:
                    packet = await asyncio.wait_for(
                        bus.frame_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    if self._recording and not self.provider.available():
                        self._emit(self._error("GAME_EXITED", "ACC 已退出"))
                        await self.stop()
                    continue

                bus.frame_queue.task_done()
                try:
                    if not self._recording:
                        # 监测未开启时也持续保留最近若干帧（滚动缓冲）。点「开始监测」后
                        # 这些帧会被回放，使第一圈补齐开头一段轨迹。
                        self._pre_buffer.append(packet)
                        if len(self._pre_buffer) > PREBUF_MAX_FRAMES:
                            self._pre_buffer.pop(0)
                        continue
                    # recording 开启后：先把滚动缓冲里「最近一次过线之后」的帧回放给圈次
                    # 状态机（丢弃更早、属于上一圈的尾段），再处理当前帧；第一圈由此从
                    # 真正的过线点（t≈0）起算，而不是从开启记录这一刻起算。
                    if self._pre_buffer:
                        buf, self._pre_buffer = self._pre_buffer, []
                        try:
                            buf = _trim_lead_in(buf)
                        except Exception:  # noqa: BLE001
                            logger.exception("裁剪回放缓冲失败，按原样回放")
                        for p in buf:
                            await self._safe_process(p)
                    await self._safe_process(packet)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    # 兜底：循环体任何意外都不许让记录 Task 退出
                    logger.exception("记录循环异常，继续运行")
                    await asyncio.sleep(0.05)
        finally:
            # 应用关闭时，先把队列里剩余的帧处理完（完成圈不能丢），
            # 再结束当前记录会话。
            while True:
                try:
                    packet = bus.frame_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                bus.frame_queue.task_done()
                if self._recording:
                    await self._safe_process(packet)
            if self._recording:
                await self.stop()

    async def _process(self, packet: Any) -> None:
        snapshot = packet.snapshot
        if self._processor is None:
            self._processor = LapProcessor()

        # 同一游戏运行内可能切换赛道 / 进入服务器：定期重读静态信息，
        # track_name 变了就重开 session，保证前端赛道名与 live 点跟随更新。
        self._frame_count += 1
        # 第一帧真实遥测到达：此时才把 session 落库（避免空 session）
        await self._persist_session()
        if self._frame_count % 10 == 0 and self._static is not None:
            try:
                fresh = await asyncio.to_thread(self.provider.read_static)
            except Exception:  # noqa: BLE001
                fresh = None
            if (
                fresh
                and getattr(fresh, "track_name", "")
                and getattr(fresh, "track_name", "")
                != getattr(self._static, "track_name", "")
            ):
                logger.info(
                    "赛道变化 %s → %s，重开 session",
                    getattr(self._static, "track_name", ""),
                    getattr(fresh, "track_name", ""),
                )
                await self._restart_session()
                return

        # 开会话时 ACC 可能还没写出会话类型（菜单 / 回放里是 -1），一旦从共享内存
        # 读到真实类型，就把会话行和此前标成 unknown 的圈补写回去。
        if self._session_type in (None, "unknown"):
            snap_type = getattr(snapshot, "session_type", "unknown") or "unknown"
            if snap_type != "unknown":
                self._session_type = snap_type
                await self._promote_session_type(snap_type)
                await self._emit_pbref()

        results = self._processor.consume(snapshot)
        reset_detected = self._processor.consume_reset()

        for result in results:
            # 出场圈不保存；圈号按实际保存顺序从 1 连续编号
            if result.kind == "out":
                continue
            self._lap_seq += 1
            result.no = self._lap_seq
            # 同一记录可能横跨排位与正赛，逐圈记下类型供前端二次分类
            result.session_type = (
                getattr(snapshot, "session_type", "unknown") or "unknown"
            )
            if result.session_type != "unknown":
                self._session_type = result.session_type
            self._emit(await self._persist_lap(result))
            await self._maybe_record_track_map(result)
            bus.runtime["lap"] = self._lap_seq + 1

        if reset_detected:
            snapshot_type = getattr(snapshot, "session_type", "unknown") or "unknown"
            # 排位 → 正赛：ACC 会把圈数归零，但用户希望仍是同一条记录，
            # 只是圈速列表里多一个「正赛」下拉框。这里只重置圈处理状态。
            type_changed = (
                snapshot_type != "unknown"
                and self._session_type not in (None, "unknown")
                and snapshot_type != self._session_type
            )
            if type_changed:
                self._session_type = snapshot_type
                bus.runtime["lap"] = self._lap_seq + 1
                await self._emit_pbref()
            else:
                await self._restart_session()

    async def _promote_session_type(self, session_type: str) -> None:
        """把「未标注」的会话补写成真实类型（只补 unknown，不覆盖已有值）。"""
        session_id = self._session_id
        if not session_id or session_type in (None, "unknown"):
            return
        try:
            changed = await asyncio.to_thread(
                self.history.sessions.set_type, session_id, session_type
            )
            if changed:
                logger.info("session %s type promoted to %s", session_id, session_type)
                # 会话类型补写成功：历史列表里的「未标注」需要跟着变
                self._emit(
                    {"type": "history", "reason": "session-type", "sessionId": session_id}
                )
        except Exception:  # noqa: BLE001 - 补写失败不影响采集
            logger.exception("补写会话类型失败")

    async def _persist_lap(self, result: LapResult) -> dict:
        persisted = True
        try:
            await asyncio.to_thread(self.history.save_lap, self._session_id, result)
            bus.stats["laps"] += 1
        except Exception:  # noqa: BLE001
            persisted = False
            logger.exception("本圈保存失败")
            self._emit({"type": "warning", "message": "本圈保存失败，采集继续"})

        pb_ms: int | None = None
        pb_ref: dict | None = None
        stype = result.session_type
        try:
            if self._track_id and stype and stype != "unknown":
                pb_ms = await asyncio.to_thread(
                    self.history.pb_ms_for_type, self._track_id, stype
                )
                pb_ref = await asyncio.to_thread(
                    self.history.pb_reference_for_type, self._track_id, stype
                )
            elif self._track_id:
                pb_ms = await asyncio.to_thread(self.history.pb_ms, self._track_id)
                pb_ref = await asyncio.to_thread(self.history.pb_reference, self._track_id)
        except Exception:  # noqa: BLE001
            pb_ms = None
            pb_ref = None

        # 每完成一圈，把当前类型的 PB 参考推给前端，使 DELTA·对 PB 按类型更新
        if pb_ref is not None or pb_ms is not None:
            self._pb_ms = pb_ms
            self._pb_ref = pb_ref
            self._publish_meta()
            self._emit({"type": "pbref", "pbMs": pb_ms, "pbRef": pb_ref})

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
            "pb": bool(
                result.kind == "ok" and pb_ms is not None and pb_ms == result.ms
            ),
            "pbMs": pb_ms,
            "sampleCount": len(result.samples),
            "persisted": persisted,
        }

    async def _emit_pbref(self) -> None:
        """把当前会话类型对应的 PB 参考圈推给前端（按类型各自算，而非整条赛道）。"""
        if not self._track_id or self._session_type in (None, "unknown"):
            return
        try:
            pb_ms = await asyncio.to_thread(
                self.history.pb_ms_for_type, self._track_id, self._session_type
            )
            pb_ref = await asyncio.to_thread(
                self.history.pb_reference_for_type, self._track_id, self._session_type
            )
        except Exception:  # noqa: BLE001
            return
        self._pb_ms = pb_ms
        self._pb_ref = pb_ref
        self._publish_meta()
        self._emit({"type": "pbref", "pbMs": pb_ms, "pbRef": pb_ref})

    async def _maybe_record_track_map(self, result: LapResult) -> None:
        """第一个有效圈完成后，用世界坐标生成真实赛道形状。"""
        if result.kind != "ok" or not self._track_id:
            return
        try:
            stored = await asyncio.to_thread(self.tracks.get, self._track_id)
            if stored is None:
                return
            if stored.get("map_source") in ("recorded", "fastlane") and not spline_is_degenerate(
                stored.get("spline") or []
            ):
                return
            spline = await asyncio.to_thread(
                build_spline_from_samples, result.samples
            )
            if not spline:
                return
            await asyncio.to_thread(
                self.tracks.upsert,
                id=stored["id"],
                name=stored["name"],
                length_m=stored["length_m"],
                spline=spline,
                sectors=stored["sectors"],
                date=stored.get("date")
                or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                source="recorded",
            )
        except Exception:  # noqa: BLE001
            logger.exception("自动生成赛道地图失败")
            return

        self._emit(
            {
                "type": "trackmap",
                "trackId": self._track_id,
                "spline": spline,
                "geometryKnown": True,
            }
        )

    # ------------------------------------------------------------------ 工具
    def _emit(self, event: dict[str, Any]) -> None:
        """把一次性事件交给广播 Task；永不阻塞存储/记录逻辑。"""
        try:
            bus.event_queue.put_nowait(event)
        except Exception:  # noqa: BLE001
            logger.exception("事件入队失败")

        # 主题状态快照的 status 需要知道「是不是真出错」：
        # GAME_NOT_FOUND 只是「还没进赛道」（前端同样按等待态处理），不算故障；
        # 其余错误（GAME_EXITED / 落库失败等）保留到下次成功开始监测。
        if event.get("type") == "error":
            if event.get("code") != "GAME_NOT_FOUND":
                bus.runtime["state_error"] = event.get("message") or "error"

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"type": "error", "code": code, "message": message, "error": message}
