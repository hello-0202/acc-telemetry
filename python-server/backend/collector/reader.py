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


async def collect_loop(
    provider: Any,
    stop_event: asyncio.Event,
    hz: float = 100,
    frame_builder: FrameBuilder | None = None,
) -> None:
    """以近似 hz 的频率读取 provider，并把帧分发给两条下游路径。"""
    interval = 1.0 / max(1.0, float(hz))
    next_tick = time.monotonic()
    opened = False
    last_liveness = 0.0
    prev_live = False
    non_live_since = None

    try:
        while not stop_event.is_set():
            # 支持运行时切换采样率（100 / 50 / 20 Hz）
            interval = 1.0 / max(1.0, float(bus.runtime.get("sample_hz", hz)))
            # ---- 游戏未启动 / 共享内存未打开：低频等待 ----
            if not opened:
                if not provider.available():
                    bus.runtime["game_running"] = False
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
                    bus.latest_static = None
                    # 之前在直播，现在游戏进程没了：通知前端退出「活跃」态
                    if prev_live:
                        bus.event_queue.put_nowait({"type": "stopped"})
                        prev_live = False
                        non_live_since = None
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
                try:
                    provider.close()
                except Exception:  # noqa: BLE001
                    pass
                opened = False
                await asyncio.sleep(0.2)
                next_tick = time.monotonic()
                continue

            # 暂停 / 回放：不采样、不推送
            is_live = True
            if isinstance(raw, dict):
                is_live = raw.get("status", 2) == 2
            else:
                is_live = getattr(raw, "status", 2) == 2
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
        bus.runtime["game_running"] = False
