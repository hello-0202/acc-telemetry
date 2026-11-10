"""广播数据源的领域模型。

**纯数据**：只允许标准库，不 import ctypes / pyACCSharedMemory / backend。
ACC 的字段名与整数枚举由适配器（``acc_telemetry.sources.broadcast``）负责翻译，
这里出现的一律是语义名 + 语义化字符串。

字段时间与单位口径见 ``docs/adr/0001-data-source-architecture.md``。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 合法词表（领域层是唯一真源）
#
# 适配器负责把 ACC 的整数枚举映射到下面这些字符串。之所以把词表放在领域层而不是
# 适配器里：换协议版本时只该改「映射」，不该改「上层能看到的取值集合」。
# ---------------------------------------------------------------------------

SESSION_TYPES: tuple[str, ...] = (
    "unknown",
    "practice",
    "qualify",
    "superpole",
    "race",
    "hotlap",
    "timeattack",
    "drift",
    "drag",
    "hotstint",
    "hotstintsuperpole",
)

SESSION_PHASES: tuple[str, ...] = (
    "unknown",
    "none",
    "starting",
    "pre_formation",
    "formation_lap",
    "pre_session",
    "session",
    "session_over",
    "post_session",
    "result_ui",
)

# 车在赛道上的位置分类。garage 与 unknown 分开：前者是「在维修区里停着（换人/修车）」，
# 后者是「这一帧什么都没收到」，两者的展示语义不同。
CAR_LOCATIONS: tuple[str, ...] = (
    "unknown",
    "track",
    "pitlane",
    "pit_entry",
    "pit_exit",
    "garage",
)

SOURCE_BROADCASTING = "broadcasting"


@dataclass(frozen=True, slots=True)
class StandingRow:
    """全场一辆车的一行。``car_id`` 是跨源 join 的唯一主键。

    约定：
    - ``0`` 表示「未知 / 无」的整数字段（position / 圈速 / 车号），不做 1 基换算；
    - 位置类浮点字段用 ``None`` 表示「未知」——0 是合法值（起跑线），不能拿来当空值；
    - 本行只描述「广播此刻说了什么」，不做任何跨源合并。
    """

    car_id: int
    race_number: int | None
    driver_name: str
    team_name: str | None
    position: int
    cup_position: int
    track_position: int
    spline_position: float | None
    completed_laps: int
    last_lap_ms: int
    best_lap_ms: int
    current_lap_ms: int
    delta_best_ms: int | None
    car_location: str
    world_x: float | None
    world_y: float | None
    speed_kmh: int
    in_pit: bool
    # 这一行已经超过适配器的新鲜度阈值没收到实时更新：位置类字段已被置空，
    # 名次/圈速仍保留（历史结论不该随刷新率消失）。展示层可据此置灰。
    stale: bool = False

    def __post_init__(self) -> None:
        if self.car_location not in CAR_LOCATIONS:
            raise ValueError(f"car_location 不在词表内: {self.car_location!r}")
        if self.position < 0 or self.track_position < 0 or self.cup_position < 0:
            raise ValueError("position / track_position / cup_position 不能为负")


@dataclass(frozen=True, slots=True)
class StandingsSnapshot:
    """某一时刻的全场快照。

    ``received_at`` 只代表「这份快照被组装出来的时刻」，不代表每一行都新鲜：
    广播对每辆车独立刷新（约 4Hz），某辆车卡住不影响其他行，行内位置字段会被置空。
    """

    received_at: float
    source: str
    track_meters: float | None
    session_type: str
    session_phase: str
    rows: tuple[StandingRow, ...]
    # 以下为辅助字段：生命周期与完整性判断要用，展示不依赖
    session_index: int = 0
    cars_expected: int = 0
    entry_list_complete: bool = False

    def __post_init__(self) -> None:
        if self.session_type not in SESSION_TYPES:
            raise ValueError(f"session_type 不在词表内: {self.session_type!r}")
        if self.session_phase not in SESSION_PHASES:
            raise ValueError(f"session_phase 不在词表内: {self.session_phase!r}")

    def row_for(self, car_id: int) -> StandingRow | None:
        """按主键取行。跨源 join 只允许用 ``car_id``。"""
        for row in self.rows:
            if row.car_id == car_id:
                return row
        return None
