"""TyreWearEstimator 的纯内存单测（不依赖 ACC / 共享内存）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acc_telemetry.providers.base import Snapshot  # noqa: E402
from acc_telemetry.services.tyre_wear import TyreWearEstimator  # noqa: E402


def snap(
    t: float,
    *,
    laps: int = 0,
    tyre_set: int = 1,
    slip: float = 0.08,
    raw=(0.0, 0.0, 0.0, 0.0),
    speed_kmh: float = 126.0,
    temp: float = 88.0,
    progress: float = 0.0,
) -> Snapshot:
    return Snapshot(
        t_mono=t,
        packet_physics=0,
        packet_graphics=0,
        speed_kmh=speed_kmh,
        rpm=7000,
        gear=5,
        throttle=0.8,
        brake=0.0,
        steer=0.1,
        wheel_pressure=(27.5, 27.5, 27.5, 27.5),
        tyre_core_temp=(temp, temp, temp, temp),
        brake_temp=(400.0, 400.0, 400.0, 400.0),
        fuel=50.0,
        current_max_rpm=7600,
        g_long=0.2,
        g_lat=1.1,
        world_x=0.0,
        world_y=0.0,
        lap_time_ms=int(t * 1000),
        last_lap_ms=110_000,
        completed_laps=laps,
        sector_index=1,
        last_sector_ms=0,
        split_ms=0,
        progress=progress,
        valid_lap=True,
        in_pit_lane=False,
        in_pit=False,
        status=2,
        wheel_slip=(slip, slip, slip, slip),
        slip_ratio=(slip / 2, slip / 2, slip / 2, slip / 2),
        slip_angle=(slip / 2, slip / 2, slip / 2, slip / 2),
        tyre_wear=raw,
        current_tyre_set=tyre_set,
    )


def test_wear_accumulates_and_rate_after_lap() -> None:
    est = TyreWearEstimator()
    # 两圈、每圈 110 秒（100Hz）
    for i in range(22_000):
        est.update(snap(i * 0.01, laps=i // 11_000))

    out = est.snapshot()
    assert out["source"] == "estimated"
    assert 0.0 < out["wheels"][0]["wear"] < 0.2
    assert out["wheels"][0]["ratePerLap"] is not None
    assert out["wheels"][0]["ratePerLap"] > 0
    assert out["wheels"][0]["lapsLeft"] is not None


def test_wear_rate_self_calibrates_to_sane_lap_budget() -> None:
    """完成一圈后，正常驾驶的每圈磨损应落在 2~3% 量级，而不是 20%+。"""
    est = TyreWearEstimator()
    for i in range(22_000):
        est.update(snap(i * 0.01, laps=i // 11_000))

    wheel = est.snapshot()["wheels"][0]
    assert 0.015 <= wheel["ratePerLap"] <= 0.04
    assert 0.015 <= wheel["wear"] <= 0.06
    assert wheel["lapsLeft"] >= 15


def test_partial_first_lap_is_not_used_for_calibration() -> None:
    """中途接入（progress 已过半）时，第一圈不完整，不能拿来做校准。"""
    est = TyreWearEstimator()
    # 从前一圈 60% 处接入，只看到剩下的 40%
    for i in range(5_500):
        est.update(snap(i * 0.01, laps=0, progress=0.6))
    # 跑完接入时的半圈：不校准、不给速率
    for i in range(11_000):
        est.update(snap(55.0 + i * 0.01, laps=1, progress=i * 0.01 / 110.0))
    assert est.snapshot()["wheels"][0]["ratePerLap"] is None

    # 再完成一整圈后，才开始用整圈能量自校准
    for i in range(11_000, 22_000):
        est.update(snap(55.0 + i * 0.01, laps=2, progress=i * 0.01 / 110.0))

    wheel = est.snapshot()["wheels"][0]
    assert wheel["ratePerLap"] is not None
    assert 0.015 <= wheel["ratePerLap"] <= 0.04


def test_tyre_set_change_resets() -> None:
    est = TyreWearEstimator()
    for i in range(2_000):
        est.update(snap(i * 0.01))
    assert est.snapshot()["wheels"][0]["wear"] > 0

    est.update(snap(20.01, tyre_set=2))
    out = est.snapshot()
    assert out["wheels"][0]["wear"] == 0.0
    assert out["wheels"][0]["ratePerLap"] is None


def test_shared_raw_wear_adopted_only_when_growing() -> None:
    est = TyreWearEstimator()
    for i in range(2_000):
        est.update(snap(i * 0.01, raw=(i * 0.0001,) * 4))
    out = est.snapshot()
    assert out["source"] == "shared"
    assert out["wheels"][0]["wear"] > 0.19


def test_constant_zero_raw_wear_is_ignored() -> None:
    est = TyreWearEstimator()
    for i in range(2_000):
        est.update(snap(i * 0.01, raw=(0.0, 0.0, 0.0, 0.0)))
    out = est.snapshot()
    assert out["source"] == "estimated"
    assert out["wheels"][0]["wear"] > 0


def test_no_data_before_frames() -> None:
    out = TyreWearEstimator().snapshot()
    assert out["source"] == "none"
    assert out["wheels"][0]["wear"] is None
    assert out["wheels"][0]["lapsLeft"] is None
