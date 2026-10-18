from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acc_telemetry.providers.base import GameNotRunning, Snapshot, StaticInfo  # noqa: E402


def make_snapshot(**overrides) -> Snapshot:
    defaults = dict(
        t_mono=0.0,
        packet_physics=0,
        packet_graphics=0,
        speed_kmh=200.0,
        rpm=7000,
        gear=5,
        throttle=0.8,
        brake=0.0,
        steer=0.0,
        wheel_pressure=(27.5, 27.5, 27.5, 27.5),
        tyre_core_temp=(90.0, 90.0, 90.0, 90.0),
        brake_temp=(400.0, 400.0, 400.0, 400.0),
        fuel=50.0,
        current_max_rpm=7600,
        g_long=0.0,
        g_lat=0.0,
        world_x=None,
        world_y=None,
        lap_time_ms=0,
        last_lap_ms=0,
        completed_laps=0,
        sector_index=0,
        last_sector_ms=0,
        split_ms=0,
        progress=0.0,
        valid_lap=True,
        in_pit_lane=False,
        in_pit=False,
        status=2,
    )
    defaults.update(overrides)
    return Snapshot(**defaults)


def make_sample(**overrides) -> dict:
    sample = dict(
        idx=0,
        p=0.0,
        speed=200.0,
        rpm=7000,
        gear=5,
        throttle=0.8,
        brake=0.0,
        steer=0.0,
        fl_p=27.5, fl_t=90.0, fl_bt=400.0,
        fr_p=27.5, fr_t=90.0, fr_bt=400.0,
        rl_p=27.5, rl_t=90.0, rl_bt=400.0,
        rr_p=27.5, rr_t=90.0, rr_bt=400.0,
        t_ms=0.0,
        g_long=0.0,
        g_lat=0.0,
    )
    sample.update(overrides)
    return sample


class FakeProvider:
    """测试替身：按脚本返回 Snapshot，耗尽后重复最后一个。"""

    def __init__(self, snapshots=None, static=None, available=True):
        self._snapshots = list(snapshots or [])
        self._index = 0
        self._available = available
        self.static = static or StaticInfo(
            track_name="spa",
            track_configuration="",
            car_model="mercedes_amg_gt3_evo",
            max_rpm=7600,
            max_fuel=100.0,
            sector_count=3,
        )
        self.opened = False
        self.closed = False

    def available(self) -> bool:
        return self._available

    def open(self) -> None:
        if not self._available:
            raise GameNotRunning("game not running")
        self.opened = True

    def read(self) -> Snapshot:
        if not self._snapshots:
            return make_snapshot()
        index = min(self._index, len(self._snapshots) - 1)
        self._index += 1
        return self._snapshots[index]

    def read_static(self) -> StaticInfo:
        return self.static

    def close(self) -> None:
        self.closed = True
