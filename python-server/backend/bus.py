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

# 跨 Task 共享的轻量运行时状态
runtime: dict[str, Any] = {
    "recording": False,
    "game_running": False,
    "lap": 1,
    "sample_hz": 100,
    "push_hz": 50,
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
    global frame_queue, event_queue, latest_static
    frame_queue = asyncio.Queue(maxsize=FRAME_QUEUE_MAXSIZE)
    event_queue = asyncio.Queue()
    latest_buffer.clear()
    latest_static = None
    runtime.update(recording=False, game_running=False, lap=1, sample_hz=100, push_hz=50)
    stats.update(produced=0, dropped=0, written=0, laps=0)
