"""夹具回放：原始报文 → ``parse_udp_stream`` → 适配器 → ``StandingsSnapshot``。

回放**不读系统时钟**：每条记录的时间轴来自夹具里的 ``t_ms``，所以同一份夹具回放
任意多次，得到的快照逐字段相等（``StandingsSnapshot`` 是 frozen dataclass，可直接比较）。
这条是任务书验收项「回放夹具 → StandingsSnapshot 结果确定性可重复」的实现依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..domain.model import StandingsSnapshot
from .broadcast import DEFAULT_STALE_AFTER_S, BroadcastAdapter
from .fixture import iter_fixture


@dataclass(frozen=True, slots=True)
class ReplayResult:
    snapshot: StandingsSnapshot | None
    packets: int
    duration_ms: int
    message_counts: dict[int, int]
    entry_list_complete: bool


def replay_records(
    records: Iterable[tuple[int, bytes]],
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    at_ms: int | None = None,
) -> ReplayResult:
    """回放 ``(t_ms, payload)`` 序列。

    ``at_ms`` 指定在时间轴的第几毫秒取快照（默认取最后一条）。
    早于 ``at_ms`` 的记录照常喂给适配器（累积名册），只是不在那一刻之后继续累积。
    """
    adapter = BroadcastAdapter(stale_after_s=stale_after_s)
    count = 0
    last_ms = 0
    for t_ms, payload in records:
        if at_ms is not None and t_ms > at_ms:
            break
        adapter.feed(payload, now=t_ms / 1000.0)
        last_ms = t_ms
        count += 1

    snapshot_at = (at_ms if at_ms is not None else last_ms) / 1000.0
    snapshot = adapter.snapshot(now=snapshot_at) if count else None
    return ReplayResult(
        snapshot=snapshot,
        packets=count,
        duration_ms=last_ms,
        message_counts=dict(adapter.message_counts),
        entry_list_complete=adapter.entry_list_complete,
    )


def replay_payloads(
    payloads: Sequence[bytes],
    *,
    interval_ms: int = 0,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> ReplayResult:
    """只有报文、没有时间轴的快速回放（按 ``interval_ms`` 等间隔排时间）。"""
    records = [(index * interval_ms, payload) for index, payload in enumerate(payloads)]
    return replay_records(records, stale_after_s=stale_after_s)


def replay_fixture(
    path: str | Path,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    at_ms: int | None = None,
) -> ReplayResult:
    return replay_records(
        iter_fixture(path), stale_after_s=stale_after_s, at_ms=at_ms
    )
