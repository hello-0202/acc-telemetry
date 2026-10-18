from __future__ import annotations

import math

from fastapi.testclient import TestClient

from conftest import FakeProvider, make_snapshot

import acc_telemetry.services.telemetry_service as telemetry_module
from acc_telemetry.app import create_app


def test_ws_streams_session_frames_and_lap(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_module, "SAMPLE_INTERVAL", 0.002)
    snapshots = [make_snapshot(progress=0.0, lap_time_ms=0)]
    for i in range(1, 11):
        snapshots.append(
            make_snapshot(progress=i / 20, lap_time_ms=i * 9_000)
        )
    snapshots.append(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.01, lap_time_ms=100)
    )
    app = create_app(
        provider_factory=lambda: FakeProvider(snapshots=snapshots),
        db_path=tmp_path / "telemetry.db",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"cmd": "start"})
            messages = []
            for _ in range(300):
                messages.append(ws.receive_json())
                kinds = [m["type"] for m in messages]
                if (
                    "session" in kinds
                    and "lap" in kinds
                    and kinds.count("frame") >= 2
                ):
                    break

            kinds = [m["type"] for m in messages]
            assert "session" in kinds
            assert "lap" in kinds
            assert kinds.count("frame") >= 2

            session = next(m for m in messages if m["type"] == "session")
            assert session["track"]["id"] == "spa"
            assert len(session["track"]["spline"]) > 50
            assert session["car"]["name"] == "Mercedes-AMG GT3 EVO"
            assert session["carSpec"]["maxRpm"] == 7600
            assert session["sessionId"]

            lap = next(m for m in messages if m["type"] == "lap")
            assert lap["ms"] == 90_000
            assert lap["kind"] == "ok"
            assert lap["persisted"] is True

            frame = next(m for m in messages if m["type"] == "frame")
            assert set(frame) >= {"t", "lap", "lapTime", "speed", "rpm", "gear", "wheels"}
            assert frame["gearText"] == "4"  # 原始 gear=5 → 第 4 档
            assert len(frame["wheels"]) == 4

            ws.send_json({"cmd": "stop"})
            for _ in range(300):
                if ws.receive_json()["type"] == "stopped":
                    break


def test_ws_records_track_map_after_valid_lap(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_module, "SAMPLE_INTERVAL", 0.002)
    snapshots = []
    count = 60
    for i in range(count):
        p = (i + 0.5) / count
        angle = 2 * math.pi * p
        snapshots.append(
            make_snapshot(
                progress=p,
                lap_time_ms=int(p * 90_000),
                world_x=120 * math.cos(angle),
                world_y=80 * math.sin(angle),
            )
        )
    snapshots.append(
        make_snapshot(completed_laps=1, last_lap_ms=90_000, progress=0.002, lap_time_ms=50)
    )
    app = create_app(
        provider_factory=lambda: FakeProvider(snapshots=snapshots),
        db_path=tmp_path / "telemetry.db",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"cmd": "start"})
            trackmap = None
            for _ in range(400):
                message = ws.receive_json()
                if message["type"] == "trackmap":
                    trackmap = message
                    break
            assert trackmap is not None
            assert trackmap["trackId"] == "spa"
            assert len(trackmap["spline"]) == 160
            assert trackmap["geometryKnown"] is True

            ws.send_json({"cmd": "stop"})
            for _ in range(200):
                if ws.receive_json()["type"] == "stopped":
                    break

        stored = client.app.state.history.tracks.get("spa")
        assert stored["map_source"] == "recorded"
        assert len(stored["spline"]) == 160
