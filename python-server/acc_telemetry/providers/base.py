"""数据源抽象。生产环境只使用 ACC 共享内存。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class GameNotRunning(RuntimeError):
    """ACC 进程 / 共享内存段不存在。"""


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


@dataclass(frozen=True, slots=True)
class StaticInfo:
    track_name: str
    track_configuration: str
    car_model: str
    max_rpm: int
    max_fuel: float
    sector_count: int


class TelemetryProvider(Protocol):
    def available(self) -> bool: ...

    def open(self) -> None: ...

    def read(self) -> Snapshot: ...

    def read_static(self) -> StaticInfo: ...

    def close(self) -> None: ...
