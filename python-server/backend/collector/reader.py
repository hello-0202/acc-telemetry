"""采集 Task（Producer）。

铁律：这里只做「读内存 + 写环形缓冲 + put_nowait 队列」，
绝不 await 网络发送，也绝不 await 磁盘写入。

为什么用 put_nowait：
- 存储队列有界，队列满说明下游来不及消费；
- put_nowait 会立刻抛 QueueFull，我们丢帧并计数，
  采集节拍不会被下游拖慢。

provider 约定：
- 由调用方传入，需实现 available() / open() / close() / read() / read_static()；
- read() 是同步函数，返回 Snapshot（或可被 frame_builder 转成 dict 的对象）；
- 采集 Task 负责 open/close provider。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .. import bus

logger = logging.getLogger(__name__)

FrameBuilder = Callable[[Any], dict[str, Any]]

# ACC graphics.status（pyACCSharedMemory/acc_enum.py::ACCStatus）
ACC_OFF, ACC_REPLAY, ACC_LIVE, ACC_PAUSE = 0, 1, 2, 3

# 明确读到「不在赛道」后持续这么久才收尾，躲开加载 / 切屏 / 回放切换的瞬时抖动
LEAVE_TRACK_GRACE_S = 2.0
# graphics.packetId 冻结这么久就认为游戏已不再写这页（LIVE 时它约 100Hz 递增）。
# 只在 status 不是 PAUSE 时生效，避免长时间暂停被误判成退出赛道。
PACKETS_FROZEN_GRACE_S = 8.0


class TrackPresence:
    """判定「本次上赛道是否已经结束」，并请求 Recorder 收尾当前 session。

    ACC 的 status 枚举本身能区分「游戏内暂停(3)」和「主菜单/无赛事(0)」，但底层
    MMapControl 是 copy 模式，packetId 不刷新时 status 会冻在旧值，所以这里改读
    provider 的实时页，并额外用 packetId 新鲜度兜底。

    判定规则（宽松到严格）：
      1. status == LIVE 且 packetId 在动           → 在赛道
      2. status == PAUSE                          → **永不结束**，用户随时会继续
      3. status 为 OFF / REPLAY 持续 LEAVE_TRACK_GRACE_S → 回主菜单，结束
      4. packetId 冻结 PACKETS_FROZEN_GRACE_S 且非 PAUSE → 数据源已死，兜底结束
      5. 共享内存段消失                            → 立即结束

    只做**边沿触发**：从「在赛道」变「离开」时请求一次，恢复 LIVE 才重新武装。
    否则前端监督循环每 3s 重发一次 start，会被同一个请求反复 stop。
    """

    def __init__(self) -> None:
        self._off_since: float | None = None
        self._last_packet_id: int | None = None
        self._last_packet_change = time.monotonic()
        self._left_track = False
        self._last_logged_status: int | None = None

    def reset_on_open(self) -> None:
        """重新打开共享内存后重置新鲜度基准，避免一 open 就被判成冻结。"""
        self._last_packet_id = None
        self._last_packet_change = time.monotonic()

    def update(self, *, status: int | None, packet_id: int | None, now: float) -> None:
        if status != self._last_logged_status:
            # 真机排查「暂停 vs 主菜单能否区分」时看这一行就够了
            logger.info("ACC graphics.status → %s", status)
            self._last_logged_status = status
        bus.runtime["acc_status"] = status

        if packet_id is not None and packet_id != self._last_packet_id:
            self._last_packet_id = packet_id
            self._last_packet_change = now

        if status == ACC_LIVE:
            if now - self._last_packet_change >= PACKETS_FROZEN_GRACE_S:
                # status 冻在 2（copy 页没刷新）但游戏其实早就不写了
                self._request_end("packets-frozen")
                return
            if self._left_track:
                logger.info("重新进入赛道（status=2），复位离开判定")
            self._left_track = False
            self._off_since = None
            bus.runtime["session_end_requested"] = False
            bus.runtime["session_end_reason"] = None
            return

        if self._left_track:
            return

        # 游戏内暂停：硬性不结束。这一场还没完，恢复后要接着记。
        if status == ACC_PAUSE:
            self._off_since = None
            return

        reason = None
        if status in (ACC_OFF, ACC_REPLAY):
            if self._off_since is None:
                self._off_since = now
            if now - self._off_since >= LEAVE_TRACK_GRACE_S:
                reason = "acc-off"
        elif now - self._last_packet_change >= PACKETS_FROZEN_GRACE_S:
            # 含 status 读不到（实时页异常）的情况
            reason = "packets-frozen"

        if reason:
            self._request_end(reason)

    def note_game_gone(self) -> None:
        """共享内存段消失 / provider 关闭：立即请求收尾，同样只触发一次。"""
        if self._left_track:
            return
        self._off_since = None
        self._last_packet_change = time.monotonic()
        self._request_end("game-exited")

    def _request_end(self, reason: str) -> None:
        self._left_track = True
        bus.runtime["session_end_requested"] = True
        bus.runtime["session_end_reason"] = reason
        # 面板 / 悬浮窗立刻失去实时数据源：别让最后一帧冻在屏幕上
        bus.latest_snapshot = None
        bus.latest_buffer.clear()
        logger.info("判定已离开赛道（%s），请求结束本次记录", reason)


def gear_text(raw_gear: int) -> str:
    if raw_gear == 0:
        return "R"
    if raw_gear == 1:
        return "N"
    return str(raw_gear - 1)


def snapshot_to_frame(snapshot: Any) -> dict[str, Any]:
    """把 acc_telemetry 的 Snapshot 转成前端需要的 frame。"""
    pressure = getattr(snapshot, "wheel_pressure", (0.0, 0.0, 0.0, 0.0))
    temp = getattr(snapshot, "tyre_core_temp", (0.0, 0.0, 0.0, 0.0))
    brake_temp = getattr(snapshot, "brake_temp", (0.0, 0.0, 0.0, 0.0))
    corners = ("FL", "FR", "RL", "RR")
    raw_gear = int(getattr(snapshot, "gear", 1))
    speed = float(getattr(snapshot, "speed_kmh", 0.0))
    world_x = getattr(snapshot, "world_x", None)
    world_z = getattr(snapshot, "world_y", None)  # ACC 的 y 是高度，平面用 x-z
    heading = float(getattr(snapshot, "heading", 0.0))
    return {
        "type": "frame",
        "t": int(time.time() * 1000),
        "lap": int(bus.runtime.get("lap", 1)),
        "lapTime": int(getattr(snapshot, "lap_time_ms", 0)),
        "valid": bool(getattr(snapshot, "valid_lap", False)),
        "speed": round(speed, 1),
        "rpm": int(getattr(snapshot, "rpm", 0)),
        "gear": raw_gear,
        "gearText": gear_text(raw_gear),
        "currentMaxRpm": int(getattr(snapshot, "current_max_rpm", 0)),
        "throttle": round(float(getattr(snapshot, "throttle", 0.0)), 3),
        "brake": round(float(getattr(snapshot, "brake", 0.0)), 3),
        "steer": round(float(getattr(snapshot, "steer", 0.0)), 3),
        "progress": round(float(getattr(snapshot, "progress", 0.0)), 5),
        "sessionType": getattr(snapshot, "session_type", "unknown") or "unknown",
        # 场景信息（环境温度 / 路面温度 / 降雨 / 抓地力）
        "airTemp": round(float(getattr(snapshot, "air_temp", 0.0)), 1),
        "roadTemp": round(float(getattr(snapshot, "road_temp", 0.0)), 1),
        "rainIntensity": int(getattr(snapshot, "rain_intensity", 0)),
        "trackGrip": int(getattr(snapshot, "track_grip", 0)),
        "x": None if world_x is None else round(float(world_x), 3),
        "z": None if world_z is None else round(float(world_z), 3),
        "heading": round(heading, 4),
        "wheels": [
            {
                "corner": corners[i],
                "pressure": round(float(pressure[i]), 2),
                "temp": round(float(temp[i]), 1),
                "brakeTemp": round(float(brake_temp[i]), 1),
            }
            for i in range(4)
        ],
    }


async def _read_static(provider: Any) -> Any | None:
    """共享内存刚启动时 static 可能还是空的，最多重试约 2 秒。"""
    last = None
    for _ in range(20):
        try:
            last = provider.read_static()
        except Exception:  # noqa: BLE001
            last = None
        if last is not None and getattr(last, "track_name", ""):
            return last
        await asyncio.sleep(0.1)
    return last


def _reset_estimator(estimator: Any | None) -> None:
    """游戏退出 / 共享内存关闭时清空磨损积分，避免下一场继承上一场。"""
    if estimator is None:
        return
    try:
        estimator.reset()
    except Exception:  # noqa: BLE001 - 估算器异常不能拖死采集循环
        logger.exception("重置轮胎磨损估算器失败")
    bus.runtime["tyre_wear"] = None


async def collect_loop(
    provider: Any,
    stop_event: asyncio.Event,
    hz: float = 100,
    frame_builder: FrameBuilder | None = None,
    estimator: Any | None = None,
) -> None:
    """以近似 hz 的频率读取 provider，并把帧分发给两条下游路径。

    `estimator`（TyreWearEstimator）每帧吃一份 Snapshot，结果写
    `bus.runtime["tyre_wear"]`，供主题状态快照合并。它只做纯计算，不碰 I/O。
    """
    interval = 1.0 / max(1.0, float(hz))
    next_tick = time.monotonic()
    opened = False
    last_liveness = 0.0
    prev_live = False
    non_live_since = None
    presence = TrackPresence()

    try:
        while not stop_event.is_set():
            # 支持运行时切换采样率（100 / 50 / 20 Hz）
            interval = 1.0 / max(1.0, float(bus.runtime.get("sample_hz", hz)))
            # ---- 游戏未启动 / 共享内存未打开：低频等待 ----
            if not opened:
                if not provider.available():
                    bus.runtime["game_running"] = False
                    # 游戏进程 / 共享内存段没了：本次上赛道结束，收尾记录
                    presence.note_game_gone()
                    # 之前在直播，现在游戏进程没了：通知前端退出「活跃」态
                    if prev_live:
                        bus.event_queue.put_nowait({"type": "stopped"})
                        prev_live = False
                        non_live_since = None
                    await asyncio.sleep(0.5)
                    next_tick = time.monotonic()
                    continue
                try:
                    provider.open()
                    opened = True
                    presence.reset_on_open()
                    bus.runtime["game_running"] = True
                    bus.latest_static = await _read_static(provider)
                    logger.info("共享内存已打开")
                except Exception:  # noqa: BLE001
                    logger.exception("打开共享内存失败，500ms 后重试")
                    bus.runtime["game_running"] = False
                    await asyncio.sleep(0.5)
                    next_tick = time.monotonic()
                    continue

            now = time.monotonic()
            if now < next_tick:
                await asyncio.sleep(next_tick - now)
            if stop_event.is_set():
                break

            tick_start = time.monotonic()

            # 每秒确认一次共享内存段仍然存在
            if tick_start - last_liveness >= 1.0:
                last_liveness = tick_start
                if not provider.available():
                    logger.info("ACC 已退出，关闭共享内存")
                    bus.runtime["game_running"] = False
                    presence.note_game_gone()
                    bus.latest_static = None
                    bus.latest_snapshot = None
                    # 之前在直播，现在游戏进程没了：通知前端退出「活跃」态
                    if prev_live:
                        bus.event_queue.put_nowait({"type": "stopped"})
                        prev_live = False
                        non_live_since = None
                    _reset_estimator(estimator)
                    try:
                        provider.close()
                    except Exception:  # noqa: BLE001
                        pass
                    opened = False
                    next_tick = time.monotonic()
                    continue

            try:
                raw = provider.read()
            except Exception:  # noqa: BLE001
                logger.exception("provider.read() 失败，关闭共享内存")
                bus.runtime["game_running"] = False
                bus.latest_static = None
                bus.latest_snapshot = None
                _reset_estimator(estimator)
                try:
                    provider.close()
                except Exception:  # noqa: BLE001
                    pass
                opened = False
                await asyncio.sleep(0.2)
                next_tick = time.monotonic()
                continue

            # 直读 graphics 实时页判定「本次上赛道是否结束」。
            # read() 里的 status 来自 copy 缓冲，数据冻结时会停在旧值；
            # 这里拿到的是游戏当下真正写下的 status / packetId。
            peek = getattr(provider, "peek_realtime_status", None)
            peeked = None
            if peek is not None:
                try:
                    peeked = peek()
                except Exception:  # noqa: BLE001 - 实时页异常时退回 copy 缓冲
                    logger.debug("读取实时 graphics 状态失败", exc_info=True)
            rt_status, rt_packet = peeked if peeked is not None else (None, None)
            presence.update(status=rt_status, packet_id=rt_packet, now=tick_start)

            # 暂停 / 回放 / 主菜单：不采样、不推送。
            # 优先用实时 status，避免 copy 缓冲滞后 / 冻结时把旧帧继续写进库。
            raw_status = (
                raw.get("status", 2) if isinstance(raw, dict) else getattr(raw, "status", 2)
            )
            is_live = (rt_status if rt_status is not None else raw_status) == ACC_LIVE
            if not is_live:
                # 暂停 / 回放 / 退到主菜单：游戏还在跑但本帧不是直播态。
                # 持续非直播超过宽限期（避免瞬时抖动）后通知前端退出「活跃」态，
                # 否则前端会一直显示冻结的旧数据和 live 点。
                if prev_live:
                    if non_live_since is None:
                        non_live_since = tick_start
                    elif tick_start - non_live_since >= 1.5:
                        bus.event_queue.put_nowait({"type": "stopped"})
                        prev_live = False
                        non_live_since = None
                next_tick += interval
                continue
            # 恢复直播：重置非直播计时，后续帧重新进入「活跃」态
            prev_live = True
            non_live_since = None

            # 轮胎磨损估算：只在直播帧上积分，暂停 / 回放不推进。
            # 估算器内部吞掉所有异常，这里再包一层，保证采集节拍不受影响。
            if estimator is not None:
                try:
                    estimator.update(raw)
                    bus.runtime["tyre_wear"] = estimator.snapshot()
                except Exception:  # noqa: BLE001
                    logger.exception("轮胎磨损估算失败，跳过该帧")

            try:
                if frame_builder is not None:
                    frame = frame_builder(raw)
                elif isinstance(raw, dict):
                    frame = raw
                else:
                    frame = snapshot_to_frame(raw)
            except Exception:  # noqa: BLE001
                logger.exception("frame_builder 执行失败，跳过该帧")
                next_tick += interval
                continue

            if not isinstance(frame, dict):
                raise TypeError(
                    "frame_builder 必须返回 dict（或让 provider.read() 直接返回 dict）"
                )

            # 路径二：实时展示。环形缓冲覆盖写，永不阻塞。
            bus.latest_buffer.append(frame)
            # 主题状态快照要的是完整 Snapshot（frame 只是子集），单独挂一份
            bus.latest_snapshot = raw
            bus.stats["produced"] += 1

            # 路径一：记录/存储。put_nowait + 丢帧计数，绝不阻塞采集。
            try:
                bus.frame_queue.put_nowait(bus.FramePacket(snapshot=raw, frame=frame))
            except asyncio.QueueFull:
                bus.stats["dropped"] += 1
                dropped = bus.stats["dropped"]
                if dropped == 1 or dropped % 100 == 0:
                    logger.warning("存储队列已满，累计丢弃 %d 帧", dropped)

            next_tick += interval
            if time.monotonic() - tick_start > interval * 2:
                next_tick = time.monotonic()
    finally:
        if opened:
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass
        _reset_estimator(estimator)
        bus.runtime["game_running"] = False
