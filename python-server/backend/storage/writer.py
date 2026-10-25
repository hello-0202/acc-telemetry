"""存储 Task（Consumer）。

这是整个进程里唯一写数据库的 Task（单写者），避免 SQLite 并发写锁冲突。

write_batch_func 约定：
- 是同步函数，接收一个 list[dict]（一批帧）；
- 内部可以一次事务写多条记录；
- 通过 asyncio.to_thread 执行，只阻塞当前存储 Task，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Sequence

from .. import bus

logger = logging.getLogger(__name__)

WriteBatchFunc = Callable[[Sequence[dict[str, Any]]], None]


async def _flush(
    batch: list[dict[str, Any]],
    write_batch_func: WriteBatchFunc,
) -> None:
    """把当前批次交给线程池写库，成功后清空批次。"""
    if not batch:
        return
    frames = list(batch)
    batch.clear()
    try:
        await asyncio.to_thread(write_batch_func, frames)
        bus.stats["written"] += len(frames)
    except Exception:  # noqa: BLE001 - 写库失败不能影响后续采集
        logger.exception("批量写库失败，丢弃 %d 帧", len(frames))


async def storage_worker(
    stop_event: asyncio.Event,
    write_batch_func: WriteBatchFunc,
    batch_size: int = 50,
    flush_interval: float = 0.5,
) -> None:
    """从 frame_queue 取帧，攒批后写入数据库。

    Args:
        stop_event: 停止信号。
        write_batch_func: 同步批量写库函数。
        batch_size: 攒够多少帧写一次。
        flush_interval: 队列空闲多久后强制刷新不足一批的数据。
    """
    batch: list[dict[str, Any]] = []

    while not stop_event.is_set():
        try:
            frame = await asyncio.wait_for(
                bus.frame_queue.get(), timeout=flush_interval
            )
        except asyncio.TimeoutError:
            # 没有新帧时，把不足一批的数据先刷掉，避免长时间不落库
            await _flush(batch, write_batch_func)
            continue

        batch.append(frame)
        bus.frame_queue.task_done()

        if len(batch) >= batch_size:
            await _flush(batch, write_batch_func)

    # 关键：停止时必须排空队列，保证最后一圈/最后一批数据不丢。
    # 采集 Task 已经停止，不会再有新帧进来，这里可以安心清空。
    while True:
        try:
            frame = bus.frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        batch.append(frame)
        bus.frame_queue.task_done()
        if len(batch) >= batch_size:
            await _flush(batch, write_batch_func)

    await _flush(batch, write_batch_func)
    logger.info(
        "存储 Task 退出：written=%d dropped=%d",
        bus.stats["written"],
        bus.stats["dropped"],
    )
