"""夹具格式 + 回放：同一份夹具回放任意多次，结果必须逐字段相等。

夹具在 ``tmp_path`` 里现场生成，仓库不存二进制、也不存任何真实车手姓名。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import broadcast_fixtures as F  # noqa: E402
from acc_telemetry.domain.model import StandingsSnapshot  # noqa: E402
from acc_telemetry.sources.fixture import (  # noqa: E402
    FIXTURE_MAGIC,
    FIXTURE_VERSION,
    FixtureError,
    FixtureWriter,
    iter_fixture,
    read_fixture,
    write_fixture,
)
from acc_telemetry.sources.replay import replay_fixture, replay_payloads  # noqa: E402


def sample_capture() -> list[tuple[int, bytes]]:
    """一段小样本：2 辆车，3 个时刻。时间轴 0 → 500ms → 1000ms。"""
    return [
        (0, F.registration_result(4)),
        (0, F.entry_list(4, 2)),
        (0, F.entry_list_car(1, team_name="TEAM A", race_number=11)),
        (0, F.entry_list_car(2, team_name="TEAM B", race_number=22)),
        (10, F.track_data(connection_id=4, track_name="spa", meters=7004)),
        (20, F.realtime_update(session_type=10, session_phase=5, focused_car_id=1)),
        (100, F.car_update(1, position=2, track_position=2, spline_position=0.25, world_x=10.0, world_y=20.0, speed_kmh=180)),
        (120, F.car_update(2, position=1, track_position=1, spline_position=0.75, world_x=-30.0, world_y=40.0, speed_kmh=200)),
        (600, F.car_update(1, position=2, track_position=2, spline_position=0.30, world_x=12.0, world_y=22.0, speed_kmh=185)),
        (620, F.car_update(2, position=1, track_position=1, spline_position=0.80, world_x=-32.0, world_y=42.0, speed_kmh=202, last_lap_ms=101_000, best_lap_ms=100_500)),
        (1_000, F.car_update(2, position=1, track_position=1, spline_position=0.85, completed_laps=4)),
    ]


# ---------------------------------------------------------------- 夹具格式
def test_fixture_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "capture.bin"
    records = sample_capture()

    written = write_fixture(path, records)

    assert written == len(records)
    assert read_fixture(path) == records
    assert path.read_bytes()[:4] == FIXTURE_MAGIC


def test_fixture_writer_streams(tmp_path: Path) -> None:
    path = tmp_path / "stream.bin"

    with FixtureWriter(path) as writer:
        writer.write(0, b"\x01")
        writer.write(250, b"\x02\x03")
        assert writer.count == 2

    assert read_fixture(path) == [(0, b"\x01"), (250, b"\x02\x03")]


def test_fixture_rejects_wrong_magic(tmp_path: Path) -> None:
    path = tmp_path / "bad.bin"
    path.write_bytes(b"NOPE" + (0).to_bytes(2, "little") + b"\x00\x00")

    with pytest.raises(FixtureError):
        read_fixture(path)


def test_fixture_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "old.bin"
    path.write_bytes(FIXTURE_MAGIC + (FIXTURE_VERSION + 1).to_bytes(2, "little") + b"\x00\x00")

    with pytest.raises(FixtureError):
        read_fixture(path)


def test_fixture_reports_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "truncated.bin"
    write_fixture(path, [(0, b"abcdef")])
    data = path.read_bytes()
    path.write_bytes(data[:-2])  # 砍掉两个字节正文

    with pytest.raises(FixtureError, match="截断"):
        read_fixture(path)


def test_fixture_handles_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    with pytest.raises(FixtureError):
        read_fixture(path)


# ---------------------------------------------------------------- 回放
def test_replay_produces_standings(tmp_path: Path) -> None:
    path = tmp_path / "capture.bin"
    write_fixture(path, sample_capture())

    result = replay_fixture(path)

    assert isinstance(result.snapshot, StandingsSnapshot)
    snapshot = result.snapshot
    assert result.packets == len(sample_capture())
    assert snapshot.source == "broadcasting"
    assert snapshot.session_type == "race"
    assert snapshot.session_phase == "session"
    assert snapshot.track_meters == 7004.0
    assert snapshot.cars_expected == 2
    assert snapshot.entry_list_complete is True
    assert [row.car_id for row in snapshot.rows] == [2, 1]
    assert snapshot.rows[0].driver_name == "Test Driver"
    assert snapshot.rows[0].race_number == 22
    assert snapshot.rows[0].team_name == "TEAM B"


def test_replay_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "capture.bin"
    write_fixture(path, sample_capture())

    first = replay_fixture(path)
    second = replay_fixture(path)

    assert first == second
    assert first.snapshot.received_at == 1.0  # 时间轴来自夹具，不读系统时钟


def test_replay_message_counts(tmp_path: Path) -> None:
    path = tmp_path / "capture.bin"
    write_fixture(path, sample_capture())

    counts = replay_fixture(path).message_counts

    assert counts[F.REGISTRATION_RESULT] == 1
    assert counts[F.ENTRY_LIST] == 1
    assert counts[F.ENTRY_LIST_CAR] == 2
    assert counts[F.TRACK_DATA] == 1
    assert counts[F.REALTIME_UPDATE] == 1
    assert counts[F.REALTIME_CAR_UPDATE] == 5


def test_replay_at_specific_time(tmp_path: Path) -> None:
    """在 700ms 取快照：1000ms 那条（2 号车 4 圈）还没发生。"""
    path = tmp_path / "capture.bin"
    write_fixture(path, sample_capture())

    snapshot = replay_fixture(path, at_ms=700).snapshot

    assert snapshot.received_at == 0.7
    assert snapshot.row_for(2).completed_laps == 0
    # 广播的坐标/进度都是 float32，比较要用 approx
    assert snapshot.row_for(1).spline_position == pytest.approx(0.30, abs=1e-6)


def test_replay_marks_stale_rows_by_time_axis(tmp_path: Path) -> None:
    """2 号车 1 秒后再没更新：到 20 秒时它仍然在榜，但位置字段被清空。"""
    path = tmp_path / "capture.bin"
    records = sample_capture() + [(20_000, F.car_update(1, position=2, spline_position=0.5))]
    write_fixture(path, records)

    snapshot = replay_fixture(path).snapshot

    assert snapshot.row_for(2).stale is True
    assert snapshot.row_for(2).position == 1
    assert snapshot.row_for(2).spline_position is None
    assert snapshot.row_for(1).stale is False


def test_replay_without_time_axis(tmp_path: Path) -> None:
    payloads = [packet for _, packet in sample_capture()]

    result = replay_payloads(payloads, interval_ms=100)

    assert result.packets == len(payloads)
    assert result.duration_ms == (len(payloads) - 1) * 100


def test_replay_empty_capture(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    write_fixture(path, [])

    result = replay_fixture(path)

    assert result.snapshot is None
    assert result.packets == 0
    assert list(iter_fixture(path)) == []
