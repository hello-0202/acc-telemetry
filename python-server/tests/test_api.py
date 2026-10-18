from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import FakeProvider, make_sample

from acc_telemetry.app import create_app
from acc_telemetry.services.lap_processor import LapResult


def _client(tmp_path, *, available=False, snapshots=None):
    app = create_app(
        provider_factory=lambda: FakeProvider(snapshots=snapshots, available=available),
        db_path=tmp_path / "telemetry.db",
    )
    return TestClient(app)


def test_health_and_empty_history(tmp_path):
    with _client(tmp_path) as client:
        health = client.get("/health").json()
        assert health["ok"] is True
        assert health["gameRunning"] is False
        assert health["dbOk"] is True
        assert health["streaming"] is False

        assert client.get("/api/sessions").json() == []
        assert client.get("/api/sessions/unknown/laps").status_code == 404
        assert client.get("/api/sessions/unknown/laps/1").status_code == 404
        assert client.delete("/api/sessions/unknown").status_code == 404


def test_seeded_track_laps_and_detail(tmp_path):
    with _client(tmp_path) as client:
        history = client.app.state.history
        history.tracks.upsert(
            id="spa",
            name="Spa-Francorchamps",
            length_m=7004,
            spline=[[1.0, 2.0]],
            sectors=[1 / 3, 2 / 3],
            date="2026-10-17",
        )
        history.sessions.create(
            id="s1", track_id="spa", car_name="Mercedes-AMG GT3 EVO", date="2026-10-17"
        )
        history.save_lap(
            "s1",
            LapResult(
                no=1,
                ms=90_000,
                kind="ok",
                valid=True,
                s1=30_000,
                s2=30_000,
                s3=30_000,
                samples=[make_sample(idx=0, p=0.5)],
                started_partial=False,
            ),
        )

        visits = client.get("/api/sessions").json()
        assert visits[0]["id"] == "s1"
        assert visits[0]["trackId"] == "spa"
        assert visits[0]["laps"] == 1
        assert visits[0]["pbMs"] == 90_000
        assert visits[0]["live"] is False

        laps = client.get("/api/sessions/s1/laps").json()
        assert laps[0]["no"] == 1
        assert laps[0]["pb"] is True
        assert laps[0]["sessionId"] == "s1"

        detail = client.get("/api/sessions/s1/laps/1").json()
        assert detail["ms"] == 90_000
        assert detail["track"]["id"] == "spa"
        assert detail["samples"][0]["flP"] == 27.5
        assert detail["samples"][0]["tMs"] == 0.0

        deleted = client.delete("/api/sessions/s1").json()
        assert deleted["ok"] is True
        assert deleted["deleted"]["laps"] == 1
        assert client.get("/api/sessions").json() == []


def test_db_unavailable_returns_503(tmp_path):
    with _client(tmp_path) as client:
        client.app.state.db.is_ok = False
        assert client.get("/api/sessions").status_code == 503
        assert client.get("/health").json()["dbOk"] is False


def test_config_roundtrip(tmp_path):
    with _client(tmp_path) as client:
        config = client.get("/api/config").json()
        assert config["pushHz"] == 50
        updated = client.post("/api/config", json={"pushHz": 30, "autoStart": False}).json()
        assert updated["pushHz"] == 30
        assert updated["autoStart"] is False
        assert client.get("/api/config").json()["pushHz"] == 30


def test_ws_start_without_game_returns_error(tmp_path):
    with _client(tmp_path, available=False) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"cmd": "start"})
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == "GAME_NOT_FOUND"
            assert isinstance(message["error"], str)
