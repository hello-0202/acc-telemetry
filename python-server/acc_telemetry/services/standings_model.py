"""榜单读模型：广播 ``StandingsSnapshot`` + 共享内存里的自己 → 契约 ``standings``。

**纯函数、纯计算**：不读总线、不碰 socket、不 import backend，方便离线测试。

三条跨源规则（ADR-0001 §2）：

1. **join 只用 ``car_id``**——名次、车号、姓名都可能重名或被改成 0，不能当键；
2. **自身参照优先取共享内存**（100Hz、始终在直播态），广播行只用来补「自己这一行」
   的姓名/车号；广播自己的行已经过期（位置字段被适配器置空）时也还能算相对距离；
3. **相对距离只有距离，没有时间差**。正 = 在自车前方，负 = 在自车后方，单位米。

字段与单位口径见 ``contracts/telemetry-state.json`` 的 ``standings.*`` 与
``docs/theme-state-contract.md``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.model import SOURCE_BROADCASTING, StandingRow, StandingsSnapshot

# 整份快照超过这么久没更新 → ``stale``（广播约 4Hz，3s 等于丢了十几帧）
STANDINGS_STALE_AFTER_S = 3.0

# ``reason`` 的合法取值（写进契约 enums.standingsReason），未知值一律归一到 no-data
STANDINGS_REASONS: tuple[str, ...] = (
    "disabled",  # 配置关掉了广播
    "port-not-configured",  # broadcasting.json 的 updListenerPort 为 0
    "connecting",  # 正在注册 / 等第一个包
    "timeout",  # 注册超时 / 超过 3s 没有新包
    "no-session",  # 游戏没开，或不在赛道上
    "no-data",  # 还没收到过任何广播数据
    "error",  # 注册被拒 / 协议异常
)

# 相对距离 / 坐标的最小精度：0.1m 对 UI 已经够，也避免把浮点噪声当数据
_POS_ND = 1
_SPLINE_ND = 5


def _r(value: Any, nd: int) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, nd)


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True, slots=True)
class StandingsSelf:
    """自车在共享内存里的样子（``Snapshot`` 里那四个字段）。

    ``spline`` 就是 ``Snapshot.progress``（``graphics.normalizedCarPosition``），
    与广播的 ``splinePosition`` 同一套归一化进度；未知给 ``None``（不是 0，
    0 是起跑线）。
    """

    car_id: int = 0
    position: int = 0
    spline: float | None = None
    completed_laps: int = 0

    @classmethod
    def from_snapshot(cls, snap: Any) -> "StandingsSelf":
        """从采集层 ``Snapshot`` 里取自身参照。"""
        if snap is None:
            return cls()
        progress = getattr(snap, "progress", None)
        return cls(
            car_id=_i(getattr(snap, "player_car_id", 0)),
            position=_i(getattr(snap, "position", 0)),
            spline=None if progress is None else float(progress),
            completed_laps=_i(getattr(snap, "completed_laps", 0)),
        )


def empty_standings(reason: str = "no-data") -> dict[str, Any]:
    """不可用时的完整空结构：键一个不少，主题不会因为缺 key 崩。"""
    return {
        "available": False,
        "stale": False,
        "complete": False,
        "source": SOURCE_BROADCASTING,
        "reason": _reason(reason),
        "trackMeters": None,
        "self": None,
        "rows": [],
    }


def gap_to_self_m(
    row: StandingRow,
    *,
    self_spline: float | None,
    self_laps: int,
    track_meters: float | None,
) -> float | None:
    """这一行相对自车的距离（米）。正 = 在前方，负 = 在后方，未知 ``None``。

    ``spline`` 的差值要跨起跑线校正：接近 1 的车与刚过线（0.02）的车相差其实是
    ``+0.03`` 而不是 ``-0.96``；不修正会得到「差一整圈」的假距离。差圈要单独加，
    否则套圈的车会被算成「就在前面几十米」。
    """
    if row.spline_position is None or self_spline is None or track_meters is None:
        return None
    delta = float(row.spline_position) - float(self_spline)
    if delta > 0.5:
        delta -= 1.0
    elif delta < -0.5:
        delta += 1.0
    laps = int(row.completed_laps) - int(self_laps)
    return (laps + delta) * float(track_meters)


def build_standings(
    snapshot: StandingsSnapshot | None,
    *,
    now: float,
    self_state: StandingsSelf | None = None,
    stale_after_s: float = STANDINGS_STALE_AFTER_S,
    reason: str = "no-data",
) -> dict[str, Any]:
    """组装契约里的 ``standings``。

    - ``snapshot is None`` → ``available=False`` + ``reason``（由服务层给）；
    - 超过 ``stale_after_s`` → ``stale=True``，**rows 照旧保留**（名次/圈速是历史结论，
      位置类字段已由适配器按每车新鲜度置空）；
    - 自车在广播里找不到 → ``self=None``，但其他行的 ``gapToSelfM`` 仍用共享内存参照算。
    """
    if snapshot is None:
        return empty_standings(reason)

    self_state = self_state or StandingsSelf()
    track_meters = snapshot.track_meters
    self_row = snapshot.row_for(self_state.car_id) if self_state.car_id > 0 else None

    # 自身参照：共享内存优先（新鲜），广播行兜底（共享内存没给 position 时）
    if self_state.car_id > 0:
        self_spline = (
            self_state.spline
            if self_state.spline is not None
            else (self_row.spline_position if self_row is not None else None)
        )
        self_laps = self_state.completed_laps
    else:
        self_spline = None
        self_laps = 0

    rows: list[dict[str, Any]] = []
    ahead: list[float] = []
    behind: list[float] = []
    for row in sorted(snapshot.rows, key=_sort_key):
        is_self = self_state.car_id > 0 and row.car_id == self_state.car_id
        gap = (
            0.0
            if is_self
            else gap_to_self_m(
                row, self_spline=self_spline, self_laps=self_laps, track_meters=track_meters
            )
        )
        if gap is not None and not is_self:
            if gap > 0:
                ahead.append(gap)
            elif gap < 0:
                behind.append(gap)
        rows.append(_row_dict(row, gap))

    self_dict = _row_dict(self_row, 0.0) if self_row is not None else None
    if self_dict is not None:
        # 正 gap 里最小的 = 紧贴自车前面的那辆；负 gap 里最大的 = 紧贴后面的那辆
        self_dict["gapAheadM"] = _r(min(ahead), _POS_ND) if ahead else None
        self_dict["gapBehindM"] = _r(max(behind), _POS_ND) if behind else None

    # 整份快照的 stale 不能只看 received_at：广播服务每 250ms 都会重新组装一次，
    # received_at 一直在刷新，单看时间永远到不了 TTL。真正有意义的信号是
    # 「所有车都超过 5s 没实时更新」——此时连接可能还活着（会话包仍在来），
    # 但榜单位置已经不可信。保留时间条件，兼容离线/将来直接喂旧快照的场景。
    rows_stale = bool(snapshot.rows) and all(row.stale for row in snapshot.rows)
    snapshot_stale = now - float(snapshot.received_at) > float(stale_after_s)
    return {
        "available": True,
        "stale": bool(rows_stale or snapshot_stale),
        "complete": bool(snapshot.entry_list_complete),
        "source": snapshot.source or SOURCE_BROADCASTING,
        "reason": None,
        "trackMeters": _r(track_meters, _POS_ND),
        "self": self_dict,
        "rows": rows,
    }


def _row_dict(row: StandingRow, gap: float | None) -> dict[str, Any]:
    """``StandingRow`` → 契约字段名。**绝不把领域对象直接丢给前端。**"""
    return {
        "carId": row.car_id,
        # 契约是 int、未知为 0：领域层的 None（没有车号）在这里归一
        "raceNumber": row.race_number or 0,
        "name": row.driver_name,
        "team": row.team_name,
        "position": row.position,
        "cupPosition": row.cup_position,
        "trackPosition": row.track_position,
        "spline": _r(row.spline_position, _SPLINE_ND),
        "laps": row.completed_laps,
        "lastLapMs": row.last_lap_ms,
        "bestLapMs": row.best_lap_ms,
        "currentLapMs": row.current_lap_ms,
        "deltaBestMs": row.delta_best_ms,
        "location": row.car_location,
        "inPit": row.in_pit,
        "x": _r(row.world_x, _POS_ND),
        "z": _r(row.world_y, _POS_ND),
        "speedKmh": row.speed_kmh,
        "gapToSelfM": _r(gap, _POS_ND),
        "stale": row.stale,
    }


def _sort_key(row: StandingRow) -> tuple[int, int, int]:
    """官方名次优先；没有的按赛道位置；都没有的按 carId。

    与适配器 ``broadcast._sort_key`` 同一口径 —— 这里再排一次是防御：读模型不该
    依赖上游灌进来的顺序。
    """
    if row.position > 0:
        return (0, row.position, row.car_id)
    if row.track_position > 0:
        return (1, row.track_position, row.car_id)
    return (2, 0, row.car_id)


def _reason(value: Any) -> str:
    text = str(value or "no-data")
    return text if text in STANDINGS_REASONS else "no-data"
