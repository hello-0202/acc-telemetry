"""数据源抽象。生产环境只使用 ACC 共享内存。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class GameNotRunning(RuntimeError):
    """ACC 进程 / 共享内存段不存在。"""


# ACC graphics.session 枚举 → 稳定字符串，入库和前端展示都依赖它
SESSION_TYPE_NAMES = {
    -1: "unknown",
    0: "practice",
    1: "qualify",
    2: "race",
    3: "hotlap",
    4: "timeattack",
    5: "drift",
    6: "drag",
    7: "hotstint",
    8: "hotstintsuperpole",
}


def session_type_name(value: int | None) -> str:
    try:
        return SESSION_TYPE_NAMES.get(int(value), "unknown")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"


@dataclass(frozen=True, slots=True)
class Snapshot:
    t_mono: float
    packet_physics: int
    packet_graphics: int

    speed_kmh: float
    rpm: int
    gear: int  # ACC 原始值：0=R, 1=N, 2+=1 档
    throttle: float
    brake: float
    steer: float  # -1..1
    wheel_pressure: tuple[float, float, float, float]
    tyre_core_temp: tuple[float, float, float, float]
    brake_temp: tuple[float, float, float, float]
    fuel: float
    current_max_rpm: int
    g_long: float | None
    g_lat: float | None
    world_x: float | None
    world_y: float | None

    lap_time_ms: int
    last_lap_ms: int
    completed_laps: int
    sector_index: int
    last_sector_ms: int
    split_ms: int
    progress: float
    valid_lap: bool
    in_pit_lane: bool
    in_pit: bool
    status: int
    heading: float = 0.0
    session_type: str = "unknown"
    # 场景信息：温度来自 physics 块，降雨/抓地力来自 graphics 块
    air_temp: float = 0.0
    road_temp: float = 0.0
    rain_intensity: int = 0
    track_grip: int = 0

    # ---- 以下是「主题状态契约」额外需要的字段（contracts/telemetry-state.json）----
    # 全部带默认值：老的构造调用（acc_telemetry/services/telemetry_service.py 等）不用改。
    # 单位与语义见契约文件，这里只做原样搬运，不在采集层做任何换算。
    best_lap_ms: int = 0
    estimated_lap_ms: int = 0
    penalty_ms: int = 0
    position: int = 0
    distance_m: float = 0.0
    session_time_left_ms: float = 0.0
    laps_total: int = 0
    session_index: int = 0
    tyre_compound: str = ""
    rain_tyres: bool = False
    tc_level: int = 0
    abs_level: int = 0
    engine_map: int = 0
    fuel_per_lap: float = 0.0
    fuel_laps_left: float = 0.0
    fuel_used: float = 0.0
    rain_in_10min: int = 0
    rain_in_30min: int = 0
    surface_grip: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    time_of_day: float = 0.0
    flag_yellow: int = 0
    flag_white: int = 0
    flag_green: int = 0
    flag_chequered: int = 0
    flag_red: int = 0
    track_status: str = ""
    mandatory_pits_missing: int = 0
    # physics 块：驾驶辅助 / 车体姿态 / 四轮细项
    clutch: float = 0.0
    brake_bias: float = 0.0
    tc_active: float = 0.0
    abs_active: float = 0.0
    pit_limiter_on: bool = False
    drs_active: bool = False
    auto_shifter_on: bool = False
    ai_controlled: bool = False
    air_density: float = 0.0
    wheel_slip: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    slip_ratio: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    slip_angle: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    suspension_travel: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    tyre_wear: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class StaticInfo:
    track_name: str
    track_configuration: str
    car_model: str
    max_rpm: int
    max_fuel: float
    sector_count: int
    session_type: str = "unknown"


class TelemetryProvider(Protocol):
    def available(self) -> bool: ...

    def open(self) -> None: ...

    def read(self) -> Snapshot: ...

    def read_static(self) -> StaticInfo: ...

    def close(self) -> None: ...
