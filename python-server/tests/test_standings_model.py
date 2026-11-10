"""榜单读模型（``acc_telemetry.services.standings_model``）。

这里覆盖的是**跨源合并的规则**，不是广播解析（那部分在
``test_broadcast_adapter.py``）。重点：TTL、self 匹配、相对距离的符号与跨圈修正。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acc_telemetry.domain.model import SOURCE_BROADCASTING, StandingRow, StandingsSnapshot  # noqa: E402
from acc_telemetry.services.standings_model import (  # noqa: E402
    STANDINGS_REASONS,
    StandingsSelf,
    build_standings,
    empty_standings,
    gap_to_self_m,
)

NOW = 10_000.0
TRACK = 5_793.0


def row(
    car_id: int,
    *,
    position: int = 0,
    track_position: int = 0,
    spline: float | None = 0.0,
    laps: int = 0,
    stale: bool = False,
    name: str = "",
) -> StandingRow:
    return StandingRow(
        car_id=car_id,
        race_number=car_id * 10,
        driver_name=name or f"CAR {car_id}",
        team_name=None,
        position=position,
        cup_position=0,
        track_position=track_position,
        spline_position=spline,
        completed_laps=laps,
        last_lap_ms=100_000,
        best_lap_ms=99_000,
        current_lap_ms=1_000,
        delta_best_ms=None,
        car_location="track",
        world_x=1.0,
        world_y=2.0,
        speed_kmh=120,
        in_pit=False,
        stale=stale,
    )


def snapshot(rows, *, track_meters: float | None = TRACK, received_at: float = NOW,
             complete: bool = True, session_type: str = "race") -> StandingsSnapshot:
    return StandingsSnapshot(
        received_at=received_at,
        source=SOURCE_BROADCASTING,
        track_meters=track_meters,
        session_type=session_type,
        session_phase="session",
        rows=tuple(rows),
        session_index=1,
        cars_expected=len(rows),
        entry_list_complete=complete,
    )


# ------------------------------------------------------------------ 空 / TTL
def test_no_snapshot_returns_empty_structure_with_reason() -> None:
    result = build_standings(None, now=NOW, reason="disabled")

    assert result == empty_standings("disabled")
    assert result["available"] is False
    assert result["reason"] == "disabled"
    assert result["rows"] == []
    assert result["self"] is None
    assert result["trackMeters"] is None
    # 键必须齐全：主题不能因为缺 key 崩掉
    assert set(result) == {
        "available", "stale", "complete", "source", "reason", "trackMeters", "self", "rows"
    }


def test_unknown_reason_falls_back_to_no_data() -> None:
    assert build_standings(None, now=NOW, reason="接口乱传")["reason"] == "no-data"


def test_all_reasons_are_accepted() -> None:
    for reason in STANDINGS_REASONS:
        assert build_standings(None, now=NOW, reason=reason)["reason"] == reason


def test_snapshot_older_than_ttl_is_stale_but_keeps_rows() -> None:
    snap = snapshot([row(1), row(2)], received_at=NOW - 5.0)

    result = build_standings(snap, now=NOW, stale_after_s=3.0)

    assert result["available"] is True
    assert result["stale"] is True
    assert len(result["rows"]) == 2


def test_fresh_snapshot_is_not_stale() -> None:
    assert build_standings(snapshot([row(1)]), now=NOW, stale_after_s=3.0)["stale"] is False


def test_all_rows_stale_marks_snapshot_stale_even_if_received_at_is_fresh() -> None:
    # 广播服务每 250ms 重新组装一次，received_at 会一直刷新；
    # 真正有意义的信号是「所有车都没有实时更新」。
    snap = snapshot([row(1, stale=True), row(2, stale=True)], received_at=NOW)

    assert build_standings(snap, now=NOW, stale_after_s=3.0)["stale"] is True


def test_one_fresh_row_keeps_snapshot_fresh() -> None:
    snap = snapshot([row(1, stale=True), row(2, stale=False)], received_at=NOW)

    assert build_standings(snap, now=NOW, stale_after_s=3.0)["stale"] is False


def test_complete_flag_follows_entry_list() -> None:
    assert build_standings(snapshot([row(1)], complete=True), now=NOW)["complete"] is True
    assert build_standings(snapshot([row(1)], complete=False), now=NOW)["complete"] is False


# ------------------------------------------------------------------ self
def test_self_is_matched_by_car_id_only() -> None:
    snap = snapshot([row(1, position=3), row(7, position=1)], received_at=NOW)
    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=7, spline=0.5))

    assert result["self"]["carId"] == 7
    assert result["self"]["position"] == 1
    assert result["self"]["gapToSelfM"] == 0.0


def test_self_missing_still_outputs_rows_and_gaps() -> None:
    # 名册里还没有自己（迟到加入 / 名册没齐）：rows 照出，相对距离用共享内存参照算
    snap = snapshot([row(2, position=1, spline=0.4)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=99, spline=0.2))

    assert result["self"] is None
    assert len(result["rows"]) == 1
    assert result["rows"][0]["gapToSelfM"] == round(0.2 * TRACK, 1)


def test_self_falls_back_to_broadcast_spline_when_shared_memory_has_none() -> None:
    snap = snapshot([row(1, spline=0.25), row(2, spline=0.35)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=None))

    gaps = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}
    assert gaps[2] == round(0.1 * TRACK, 1)


# ------------------------------------------------------------------ 相对距离
def test_gap_sign_positive_is_ahead_negative_is_behind() -> None:
    result = build_standings(
        snapshot(
            [row(1, position=1, spline=0.5), row(2, position=2, spline=0.6),
             row(3, position=3, spline=0.4)],
            received_at=NOW,
        ),
        now=NOW,
        self_state=StandingsSelf(car_id=1, spline=0.5),
    )

    gaps = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}
    assert gaps[1] == 0.0
    assert gaps[2] > 0  # 在前方
    assert gaps[3] < 0  # 在后方
    assert result["self"]["gapAheadM"] == gaps[2]
    assert result["self"]["gapBehindM"] == gaps[3]


def test_gap_wraps_across_start_line() -> None:
    # 自己在 0.98、对手刚过线 0.02：相差 +0.04 圈，不是 -0.96 圈
    snap = snapshot([row(1, spline=0.98), row(2, spline=0.02)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=0.98))

    gap = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}[2]
    assert gap == round(0.04 * TRACK, 1)
    assert gap > 0


def test_gap_counts_lapped_traffic() -> None:
    # 对手领先整整一圈、进度还略靠前 → 距离应该约等于一整圈，而不是几十米
    snap = snapshot([row(1, spline=0.50, laps=2), row(2, spline=0.55, laps=3)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=0.50, completed_laps=2))

    gap = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}[2]
    assert gap == round(1.05 * TRACK, 1)


def test_gaps_are_null_without_track_length() -> None:
    snap = snapshot([row(1, spline=0.2), row(2, spline=0.4)], track_meters=None, received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=0.2))

    gaps = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}
    assert gaps[1] == 0.0  # 自己到自己恒为 0，与赛道长度无关
    assert gaps[2] is None  # 别人：没有赛道长度就算不出米
    assert result["self"]["gapAheadM"] is None
    assert result["self"]["gapBehindM"] is None
    assert result["trackMeters"] is None


def test_gaps_are_null_when_position_fields_were_blanked_by_adapter() -> None:
    # 过期的行位置字段已被适配器置空；不能拿 0（起跑线）当真值
    snap = snapshot([row(1, spline=None, stale=True), row(2, spline=None, stale=True)],
                    received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=0.3))

    gaps = {item["carId"]: item["gapToSelfM"] for item in result["rows"]}
    assert gaps[1] == 0.0
    assert gaps[2] is None  # 位置字段被置空 → 不猜，给 None


def test_gap_helper_returns_none_for_unknown_spline() -> None:
    assert gap_to_self_m(row(2, spline=None), self_spline=0.1, self_laps=0,
                         track_meters=TRACK) is None
    assert gap_to_self_m(row(2, spline=0.1), self_spline=None, self_laps=0,
                         track_meters=TRACK) is None


def test_gap_ahead_ignores_self() -> None:
    snap = snapshot([row(1, position=1, spline=0.5)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1, spline=0.5))

    assert result["self"]["gapAheadM"] is None
    assert result["self"]["gapBehindM"] is None


# ------------------------------------------------------------------ 排序 / 精度
def test_sort_order_matches_adapter_rules() -> None:
    snap = snapshot(
        [
            row(9, position=0, track_position=0, spline=0.1),   # 什么都没有 → 最后，按 carId
            row(5, position=0, track_position=2, spline=0.2),   # 只有赛道位置
            row(3, position=2, spline=0.3),                     # 官方名次优先
            row(2, position=1, spline=0.4),
        ],
        received_at=NOW,
    )

    result = build_standings(snap, now=NOW)

    assert [item["carId"] for item in result["rows"]] == [2, 3, 5, 9]


def test_rounding_of_coordinates_and_spline() -> None:
    snap = snapshot([row(1, spline=0.123456789)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1))
    item = result["rows"][0]

    assert item["spline"] == 0.12346  # 5 位
    assert item["x"] == 1.0  # 0.1m
    assert item["z"] == 2.0


def test_missing_race_number_normalises_to_zero() -> None:
    # 领域层允许 None（没有车号），契约是 int / 未知 0
    snap = snapshot([dataclasses.replace(row(1), race_number=None)], received_at=NOW)

    assert build_standings(snap, now=NOW)["rows"][0]["raceNumber"] == 0


def test_row_fields_are_contract_names() -> None:
    snap = snapshot([row(1, position=1)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1))
    item = result["rows"][0]

    assert set(item) == {
        "carId", "raceNumber", "name", "team", "position", "cupPosition", "trackPosition",
        "spline", "laps", "lastLapMs", "bestLapMs", "currentLapMs", "deltaBestMs",
        "location", "inPit", "x", "z", "speedKmh", "gapToSelfM", "stale",
    }
    assert "gapAheadM" in result["self"]


def test_no_domain_objects_leak_to_output() -> None:
    snap = snapshot([row(1), row(2)], received_at=NOW)

    result = build_standings(snap, now=NOW, self_state=StandingsSelf(car_id=1))

    for item in result["rows"] + [result["self"]]:
        assert isinstance(item, dict)
        assert not isinstance(item, StandingRow)
