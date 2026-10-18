from __future__ import annotations

from conftest import make_sample

from acc_telemetry.db import Database
from acc_telemetry.repositories.lap_repo import LapRepository
from acc_telemetry.repositories.sample_repo import SampleRepository
from acc_telemetry.repositories.session_repo import SessionRepository
from acc_telemetry.repositories.track_repo import TrackRepository
from acc_telemetry.services.history_service import HistoryService
from acc_telemetry.services.lap_processor import LapResult


def _build(tmp_path):
    db = Database(tmp_path / "telemetry.db")
    assert db.initialize()
    tracks = TrackRepository(db)
    sessions = SessionRepository(db)
    laps = LapRepository(db)
    samples = SampleRepository(db)
    return db, tracks, sessions, laps, samples, HistoryService(tracks, sessions, laps, samples)


def test_schema_created(tmp_path):
    db, *_ = _build(tmp_path)
    conn = db.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        track_columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    finally:
        conn.close()
    assert mode.lower() == "wal"
    assert version == 2
    assert {"tracks", "sessions", "laps", "samples", "config"} <= tables
    assert "map_source" in track_columns


def test_lap_roundtrip_and_cascade_delete(tmp_path):
    db, tracks, sessions, laps, samples, history = _build(tmp_path)
    tracks.upsert(
        id="spa",
        name="Spa-Francorchamps",
        length_m=7004,
        spline=[[1, 2], [3, 4]],
        sectors=[1 / 3, 2 / 3],
        date="2026-10-17",
    )
    sessions.create(id="s1", track_id="spa", car_name="Mercedes-AMG GT3 EVO", date="2026-10-17")

    result = LapResult(
        no=1,
        ms=90_000,
        kind="ok",
        valid=True,
        s1=30_000,
        s2=30_000,
        s3=30_000,
        samples=[
            make_sample(idx=0, p=0.25, fl_p=27.8),
            make_sample(idx=1, p=0.75, fl_p=27.9),
        ],
        started_partial=False,
    )
    history.save_lap("s1", result)

    summaries = history.list_sessions()
    assert len(summaries) == 1
    assert summaries[0]["id"] == "s1"
    assert summaries[0]["trackId"] == "spa"
    assert summaries[0]["laps"] == 1
    assert summaries[0]["pbMs"] == 90_000
    assert summaries[0]["car"] == "Mercedes-AMG GT3 EVO"
    assert summaries[0]["lenM"] == 7004

    lap_list = history.list_laps("s1")
    assert lap_list[0]["no"] == 1
    assert lap_list[0]["pb"] is True
    assert lap_list[0]["deltaPb"] == 0

    detail = history.get_lap("s1", 1)
    assert len(detail["samples"]) == 2
    assert detail["samples"][0]["flP"] == 27.8
    assert detail["track"]["id"] == "spa"

    ref = history.pb_reference("spa")
    assert ref is not None
    assert ref["p"] == [0.25, 0.75]
    assert ref["tMs"] == [0.25 * 90_000, 0.75 * 90_000]

    deleted = history.delete_session("s1")
    assert deleted == {"sessions": 1, "laps": 1, "samples": 2}
    assert history.list_sessions() == []

    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_removes_out_laps_and_renumbers(tmp_path):
    db = Database(tmp_path / "telemetry.db")
    assert db.initialize()
    tracks = TrackRepository(db)
    sessions = SessionRepository(db)
    tracks.upsert(
        id="monza", name="Monza", length_m=5793, spline=[], sectors=[1 / 3, 2 / 3],
        date="2026-10-18",
    )
    sessions.create(id="s1", track_id="monza", car_name="Car", date="2026-10-18")

    conn = db.connect()
    try:
        conn.executemany(
            "INSERT INTO laps (id, session_id, no, ms, kind, valid) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("a", "s1", 1, 120_000, "out", 0),
                ("b", "s1", 2, 118_000, "ok", 1),
                ("c", "s1", 3, 119_000, "invalid", 0),
            ],
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()

    assert db.initialize()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT no, kind FROM laps WHERE session_id='s1' ORDER BY no"
        ).fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert [(row["no"], row["kind"]) for row in rows] == [(1, "ok"), (2, "invalid")]
    assert version == 2


def test_recorded_track_map_is_not_overwritten(tmp_path):
    _db, tracks, _sessions, _laps, _samples, _history = _build(tmp_path)
    tracks.upsert(
        id="spa",
        name="Spa",
        length_m=7004,
        spline=[[1, 1], [2, 2]],
        sectors=[1 / 3, 2 / 3],
        date="2026-10-17",
        source="generated",
    )
    tracks.upsert(
        id="spa",
        name="Spa",
        length_m=7004,
        spline=[[10, 10], [20, 20], [30, 30]],
        sectors=[1 / 3, 2 / 3],
        date="2026-10-17",
        source="recorded",
    )
    # 后续 session 启动时再次写入 generated，不应覆盖实跑地图
    tracks.upsert(
        id="spa",
        name="Spa",
        length_m=7004,
        spline=[[9, 9], [8, 8]],
        sectors=[1 / 3, 2 / 3],
        date="2026-10-18",
        source="generated",
    )
    track = tracks.get("spa")
    assert track["map_source"] == "recorded"
    assert track["spline"] == [[10, 10], [20, 20], [30, 30]]
    assert track["date"] == "2026-10-18"


def test_queries_are_parameterized(tmp_path):
    _db, tracks, _sessions, _laps, _samples, history = _build(tmp_path)
    tracks.upsert(
        id="spa", name="Spa", length_m=7004, spline=[], sectors=[1 / 3, 2 / 3], date="2026-10-17"
    )
    # 注入式字符串不能匹配到任何记录
    assert history.list_laps("s1' OR 1=1 --") is None
    assert history.delete_session("'; DROP TABLE tracks; --") is None
    assert tracks.count() == 1
