"""广播 Task：实时帧 + 一次性事件（session/lap/trackmap/...）。

为什么每个连接独立 create_task：
- 某个客户端网络慢或卡住时，只阻塞它自己的发送任务；
- _safe_send 带 1 秒超时，超时/异常就把该连接从 connections 中移除；
- 广播循环本身不会被任何客户端拖慢，也不会反压采集。

帧只在 bus.runtime['recording'] 为 True 时发送，
与原来的「点击开始监测后才推流」行为保持一致。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from fastapi import WebSocket

from .. import bus

logger = logging.getLogger(__name__)

# 当前所有活跃的 WebSocket 连接（只在同一事件循环内访问）
connections: set[WebSocket] = set()

# 保存 fire-and-forget 的发送任务，防止被垃圾回收
_send_tasks: set[asyncio.Task[Any]] = set()


async def _safe_send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(websocket.send_json(payload), timeout=1.0)
    except Exception:  # noqa: BLE001 - 包括超时、连接已关闭等
        connections.discard(websocket)
        with suppress(Exception):
            await websocket.close()
        logger.debug("移除发送失败/超时的 WebSocket 连接")


def _spawn_send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    task = asyncio.create_task(_safe_send(websocket, payload))
    _send_tasks.add(task)
    task.add_done_callback(_send_tasks.discard)


async def broadcast_worker(stop_event: asyncio.Event, hz: float = 50) -> None:
    interval = 1.0 / max(1.0, float(hz))
    next_tick = time.monotonic()
    last_frame: dict[str, Any] | None = None

    try:
        while not stop_event.is_set():
            # 支持运行时切换推流频率（60 / 50 / 30 Hz）
            interval = 1.0 / max(1.0, float(bus.runtime.get("push_hz", hz)))
            now = time.monotonic()
            if now < next_tick:
                await asyncio.sleep(next_tick - now)
            if stop_event.is_set():
                break

            # 1) 一次性事件优先发送（session 必须先于后续帧到达前端）
            while True:
                try:
                    event = bus.event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                bus.event_queue.task_done()
                for websocket in list(connections):
                    _spawn_send(websocket, event)

            # 2) 实时帧：只有记录中才推流，且每个最新帧只发一次
            if (
                bus.runtime.get("recording")
                and bus.latest_buffer
                and connections
            ):
                frame = bus.latest_buffer[-1]
                if frame is not last_frame:
                    last_frame = frame
                    for websocket in list(connections):
                        _spawn_send(websocket, frame)

            next_tick += interval
            if time.monotonic() - next_tick > interval:
                next_tick = time.monotonic()
    finally:
        # 停止时取消尚未完成的发送任务，避免悬挂
        for task in list(_send_tasks):
            task.cancel()
        if _send_tasks:
            await asyncio.gather(*_send_tasks, return_exceptions=True)
