from __future__ import annotations

from conftest import make_snapshot
from acc_telemetry.services.lap_processor import LapProcessor


def _begin(proc: LapProcessor, **overrides) -> None:
    proc.begin(make_snapshot(**overrides))


def _feed(proc: LapProcessor, count: int = 9, step_ms: int = 3_000, **overrides) -> None:
    """补充足够的采样点，避免被最小采样数保护丢弃。"""
    for i in range(1, count + 1):
        proc.consume(
            make_snapshot(progress=i / 40, lap_time_ms=i * step_ms, **overrides)
        )


def test_normal_lap_boundary_by_completed_laps():
    proc = LapProcessor(bins=120)
    _begin(proc, completed_laps=0, progress=0.0, lap_time_ms=0)
    for i in range(1, 10):
        assert proc.consume(make_snapshot(progress=i / 10, lap_time_ms=i * 9_000)) == []

    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.01, lap_time_ms=100)
    )
    assert len(results) == 1
    lap = results[0]
    assert (lap.no, lap.ms, lap.kind, lap.valid) == (1, 90_000, "ok", True)
    assert 5 <= len(lap.samples) <= 120
    assert all(0 <= s["p"] < 1 for s in lap.samples)
    assert all("speed" in s and "flP" not in s for s in lap.samples)


def test_invalid_lap_is_not_valid():
    proc = LapProcessor(bins=60)
    _begin(proc)
    _feed(proc)
    proc.consume(make_snapshot(progress=0.5, lap_time_ms=45_000, valid_lap=False))
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.02, lap_time_ms=100)
    )
    assert results[0].kind == "invalid"
    assert results[0].valid is False


def test_lap_started_mid_track_is_not_out():
    # 监测中途开始 → 采样不完整，但仍然是一圈正常的飞驰圈，不能标成出场圈
    proc = LapProcessor(bins=60)
    _begin(proc, progress=0.35, lap_time_ms=35_000)
    for i in range(1, 10):
        proc.consume(
            make_snapshot(progress=0.35 + i / 30, lap_time_ms=35_000 + i * 5_000)
        )
    results = proc.consume(
        make_snapshot(completed_laps=4, last_lap_ms=90_000, progress=0.02, lap_time_ms=100)
    )
    assert results[0].kind == "ok"
    assert results[0].valid is True
    assert results[0].started_partial is True


def test_lap_started_in_pit_is_out():
    proc = LapProcessor(bins=60)
    _begin(proc, in_pit_lane=True)
    _feed(proc)
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.02, lap_time_ms=100)
    )
    assert results[0].kind == "out"
    assert results[0].valid is False


def test_counter_reset_discards_partial_lap():
    proc = LapProcessor(bins=60)
    _begin(proc, completed_laps=5, progress=0.5, lap_time_ms=45_000)
    results = proc.consume(
        make_snapshot(completed_laps=0, last_lap_ms=90_000, progress=0.01, lap_time_ms=100)
    )
    # 新 session：半圈丢弃，不产生 lap，只发出重置信号
    assert results == []
    assert proc.consume_reset() is True
    assert proc.lap_no == 1


def test_timer_reset_after_full_lap_records_out_lap():
    proc = LapProcessor(bins=60)
    _begin(proc, in_pit_lane=True)
    for i in range(1, 20):
        proc.consume(make_snapshot(progress=i / 20, lap_time_ms=i * 5_000))
    results = proc.consume(
        make_snapshot(progress=0.05, lap_time_ms=200, last_lap_ms=100_000)
    )
    assert len(results) == 1
    assert results[0].kind == "out"
    assert results[0].ms == 100_000


def test_timer_reset_without_wrap_discards_partial_lap():
    proc = LapProcessor(bins=60)
    _begin(proc)
    _feed(proc)
    results = proc.consume(
        make_snapshot(progress=0.3, lap_time_ms=1_000)
    )
    assert results == []
    assert proc.consume_reset() is True


def test_sector_times_prefer_progress_crossings():
    proc = LapProcessor(bins=60)
    _begin(proc)
    _feed(proc)
    proc.consume(
        make_snapshot(progress=0.4, lap_time_ms=40_000, sector_index=1, last_sector_ms=30_000)
    )
    proc.consume(
        make_snapshot(progress=0.7, lap_time_ms=70_000, sector_index=2, last_sector_ms=50_000)
    )
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.01, lap_time_ms=100)
    )
    lap = results[0]
    # 按进度 1/3、2/3 对齐：40.0s / 30.0s / 20.0s
    assert (lap.s1, lap.s2, lap.s3) == (40_000, 30_000, 20_000)


def test_sector_times_fallback_to_acc_cumulative():
    proc = LapProcessor(bins=60)
    _begin(proc)
    _feed(proc)
    # 采样没有越过 2/3，进度法不可用，回退到 ACC 累计分段点
    proc.consume(
        make_snapshot(progress=0.4, lap_time_ms=40_000, sector_index=1, last_sector_ms=30_000)
    )
    proc.consume(
        make_snapshot(progress=0.5, lap_time_ms=50_000, sector_index=2, last_sector_ms=50_000)
    )
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.01, lap_time_ms=100)
    )
    lap = results[0]
    # 累计点 30s / 50s → 30s / 20s / 40s
    assert (lap.s1, lap.s2, lap.s3) == (30_000, 20_000, 40_000)


def test_downsample_has_uniform_progress_bins():
    proc = LapProcessor(bins=10)
    _begin(proc)
    for i in range(100):
        proc.consume(make_snapshot(progress=i / 100, lap_time_ms=i * 900))
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.005, lap_time_ms=50)
    )
    samples = results[0].samples
    assert len(samples) == 10
    assert samples[0]["idx"] == 0 and samples[-1]["idx"] == 9
    assert samples[0]["p"] == 0.05 and samples[-1]["p"] == 0.95


def test_short_lap_is_discarded():
    proc = LapProcessor(bins=30)
    _begin(proc)
    results = proc.consume(
        make_snapshot(completed_laps=1, last_lap_ms=5_000, progress=0.01, lap_time_ms=50)
    )
    assert results == []
