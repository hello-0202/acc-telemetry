"""``state.standings`` 接入（``backend/api/state.py``）。

三件事必测：

1. **键永远齐全** —— 主题拿到空结构也不能缺 key；
2. **只有 streaming 才给实时值** —— 待机 / 等待 / 暂停一律空结构，沿现有稳定性规则；
3. **广播是增强不是依赖** —— 广播挂掉不能改动任何既有字段，也不能让组装抛异常。

伪造对象一律 ``SimpleNamespace``，不碰真实 socket / 不碰游戏（同
``test_state_delta_best.py`` 的写法）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acc_telemetry.domain.model import (  # noqa: E402
    SOURCE_BROADCASTING,
    StandingRow,
    StandingsSnapshot,
)
from backend import bus  # noqa: E402
from backend.api import state as state_mod  # noqa: E402

TRACK = 5_793.0


def _row(car_id: int, *, position: int = 0, spline: float = 0.5, laps: int = 1,
         name: str = "", stale: bool = False) -> StandingRow:
    return StandingRow(
        car_id=car_id,
        race_number=car_id * 10,
        driver_name=name or f"CAR {car_id}",
        team_name=None,
        position=position,
        cup_position=0,
        track_position=position,
        spline_position=spline,
        completed_laps=laps,
        last_lap_ms=100_000,
        best_lap_ms=99_000,
        current_lap_ms=1_000,
        delta_best_ms=None,
        car_location="track",
        world_x=10.0,
        world_y=20.0,
        speed_kmh=120,
        in_pit=False,
        stale=stale,
    )


def _standings_snapshot(rows: list[StandingRow], *, received_at: float | None = None,
                        track_meters: float | None = TRACK,
                        complete: bool = True) -> StandingsSnapshot:
    return StandingsSnapshot(
        received_at=time.monotonic() if received_at is None else received_at,
        source=SOURCE_BROADCASTING,
        track_meters=track_meters,
        session_type="race",
        session_phase="session",
        rows=tuple(rows),
        session_index=1,
        cars_expected=len(rows),
        entry_list_complete=complete,
    )


def _live_snapshot(**overrides) -> SimpleNamespace:
    """采集层 ``Snapshot`` 的最小替身：只要 ``state.py`` 会 getattr 的那几个键。"""
    values = dict(
        t_mono=time.monotonic(),
        player_car_id=1,
        position=2,
        progress=0.50,
        completed_laps=1,
        session_type="race",
        session_index=1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _prepare(*, recording: bool = True, game_running: bool = True) -> None:
    bus.reset()
    bus.runtime["recording"] = recording
    bus.runtime["game_running"] = game_running


# ------------------------------------------------------------------ 空结构
def test_idle_state_contains_complete_empty_standings() -> None:
    _prepare(recording=False)

    result = state_mod.build_state()

    assert result["status"] == "idle"
    assert result["standings"] == {
        "available": False,
        "stale": False,
        "complete": False,
        "source": SOURCE_BROADCASTING,
        "reason": "no-data",
        "trackMeters": None,
        "self": None,
        "rows": [],
    }


def test_reason_comes_from_broadcast_status() -> None:
    _prepare(recording=False)
    bus.standings_status = {"reason": "port-not-configured", "port": None, "connected": False}

    assert state_mod.build_state()["standings"]["reason"] == "port-not-configured"


def test_every_standings_key_survives_when_broadcast_disabled() -> None:
    _prepare()
    bus.standings_status = {"reason": "disabled", "port": None, "connected": False}

    standings = state_mod.build_state()["standings"]

    assert set(standings) == {
        "available", "stale", "complete", "source", "reason", "trackMeters", "self", "rows",
    }
    assert standings["reason"] == "disabled"


# ------------------------------------------------------------------ streaming
def test_streaming_state_carries_live_standings() -> None:
    _prepare()
    bus.latest_snapshot = _live_snapshot()
    bus.latest_standings = _standings_snapshot(
        [_row(1, position=2, spline=0.50), _row(2, position=1, spline=0.60)],
    )
    bus.standings_status = {"reason": None, "port": 9000, "connected": True}

    result = state_mod.build_state()

    assert result["status"] == "streaming"
    standings = result["standings"]
    assert standings["available"] is True
    assert standings["reason"] is None
    assert standings["trackMeters"] == TRACK
    assert [item["carId"] for item in standings["rows"]] == [2, 1]
    # self 由共享内存的 player_car_id 决定，不看 focusedCarId
    assert standings["self"]["carId"] == 1
    assert standings["self"]["gapToSelfM"] == 0.0
    assert standings["self"]["gapAheadM"] == round(0.1 * TRACK, 1)


def test_waiting_state_does_not_leak_last_rows() -> None:
    # 采集停了（帧过期）但广播槽位还留着上一份数据 → 不能继续推给主题
    _prepare()
    bus.latest_snapshot = _live_snapshot(t_mono=time.monotonic() - 10.0)
    bus.latest_standings = _standings_snapshot([_row(1, position=1)])

    result = state_mod.build_state()

    assert result["status"] == "waiting"
    assert result["standings"]["available"] is False
    assert result["standings"]["rows"] == []


def test_standings_absent_from_live_state_when_broadcast_never_connected() -> None:
    _prepare()
    bus.latest_snapshot = _live_snapshot()

    result = state_mod.build_state()

    assert result["status"] == "streaming"
    assert result["standings"]["available"] is False
    assert result["standings"]["reason"] == "no-data"


# ------------------------------------------------------------------ 隔离性
def test_broadcast_presence_does_not_change_any_other_field() -> None:
    _prepare()
    bus.latest_snapshot = _live_snapshot()
    baseline = state_mod.build_state()

    bus.latest_standings = _standings_snapshot([_row(1, position=1), _row(2, position=2)])
    bus.standings_status = {"reason": None, "port": 9000, "connected": True}
    enriched = state_mod.build_state()

    skip = {"standings", "seq", "t"}
    assert {k: v for k, v in baseline.items() if k not in skip} == {
        k: v for k, v in enriched.items() if k not in skip
    }


def test_broken_standings_payload_falls_back_to_empty_structure() -> None:
    # 兜底：槽位里塞了畸形对象时，组装不能抛，主题通道照常出数据
    _prepare()
    bus.latest_snapshot = _live_snapshot()
    bus.latest_standings = object()

    result = state_mod.build_state()

    assert result["status"] == "streaming"
    assert result["standings"]["available"] is False
    assert result["standings"]["reason"] == "error"


def test_state_version_and_existing_top_level_keys_unchanged() -> None:
    _prepare()
    bus.latest_snapshot = _live_snapshot()

    result = state_mod.build_state()

    assert state_mod.STATE_VERSION == 1
    assert result["v"] == 1
    assert result["type"] == "state"
    for key in ("session", "track", "car", "lap", "flags", "wheels", "standings"):
        assert key in result
