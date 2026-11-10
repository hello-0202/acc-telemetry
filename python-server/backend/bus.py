"""核心总线：存储队列 + 实时环形缓冲 + 一次性事件队列。

数据流：

    采集 Task
      ├─ latest_buffer.append(frame)           → 广播 Task → WebSocket 帧
      ├─ frame_queue.put_nowait(FramePacket)   → 记录/存储 Task → SQLite
      └─ event_queue.put_nowait(dict)          → 广播 Task → session/lap/...
                                                 （一次性事件）

设计约定：
- frame_queue 有界，采集端只用 put_nowait，满了丢帧并计数，绝不阻塞。
- latest_buffer 环形覆盖，采集端直接 append，永不阻塞。
- event_queue 只放低频事件（session/lap/trackmap/error/stopped），不做限流。
- 所有对象都在同一个事件循环里使用，不需要加锁。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

FRAME_QUEUE_MAXSIZE = 2000
LATEST_BUFFER_MAXLEN = 50


@dataclass(slots=True)
class FramePacket:
    """队列里同时携带原始 Snapshot 和 JSON frame。

    - frame 给广播/前端；
    - snapshot 给记录层做圈次状态机。
    """

    snapshot: Any
    frame: dict[str, Any]


# 路径一：存储队列（有界，满了丢帧）
frame_queue: asyncio.Queue[FramePacket] = asyncio.Queue(maxsize=FRAME_QUEUE_MAXSIZE)

# 路径二：实时展示环形缓冲（覆盖写，不阻塞）
latest_buffer: deque[dict[str, Any]] = deque(maxlen=LATEST_BUFFER_MAXLEN)

# 一次性事件：session / lap / trackmap / error / stopped
event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

# 采集层最近一次读到的静态信息（赛道/车辆），供记录层开 session 使用
latest_static: Any | None = None

# 采集层最近一次「直播态」的原始 Snapshot。
# 主题状态快照（backend/api/state.py）需要它来组装完整字段——frame 只是其中
# 面向前端的子集，不适合当作 state 的数据源。
latest_snapshot: Any | None = None

# 跨 Task 共享的轻量运行时状态
runtime: dict[str, Any] = {
    "recording": False,
    "game_running": False,
    "lap": 1,
    "sample_hz": 100,
    "push_hz": 50,
    # 主题状态快照推送频率（recording 中）/ 待机时的低频心跳频率
    "state_hz": 30,
    "state_idle_hz": 2,
    # 记录层发布的会话元信息（赛道 / 车辆 / 会话类型 / PB），供 state 组装使用。
    # 结构见 backend/api/state.py::build_state
    "state_meta": None,
    # 最近一次 error 事件的消息，非空且未在推流时 state.status 报 "error"
    "state_error": None,
    # 轮胎磨损估算结果（acc_telemetry.services.tyre_wear.TyreWearEstimator.snapshot）。
    # state.py 把它合并进 wheels[]，主题按 wearSource 区分实测 / 估算。
    "tyre_wear": None,
    # 采集层判定的「已离开赛道」信号：边沿触发，Recorder 消费后置 False。
    # 见 backend/collector/reader.py::TrackPresence
    "session_end_requested": False,
    "session_end_reason": None,
    # 直读 graphics 实时页拿到的 status（0=OFF / 1=REPLAY / 2=LIVE / 3=PAUSE）
    "acc_status": None,
}

# 运行统计
stats: dict[str, int] = {
    "produced": 0,
    "dropped": 0,
    "written": 0,
    "laps": 0,
}


def reset() -> None:
    """应用启动时清空总线（同一进程内重启 lifespan 时也能干净开始）。"""
    global frame_queue, event_queue, latest_static, latest_snapshot
    frame_queue = asyncio.Queue(maxsize=FRAME_QUEUE_MAXSIZE)
    event_queue = asyncio.Queue()
    latest_buffer.clear()
    latest_static = None
    latest_snapshot = None
    runtime.update(
        recording=False,
        game_running=False,
        lap=1,
        sample_hz=100,
        push_hz=50,
        state_hz=30,
        state_idle_hz=2,
        state_meta=None,
        state_error=None,
        tyre_wear=None,
        session_end_requested=False,
        session_end_reason=None,
        acc_status=None,
    )
    stats.update(produced=0, dropped=0, written=0, laps=0)
