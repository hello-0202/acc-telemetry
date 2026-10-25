"""历史查询/删除的业务逻辑，负责组装契约响应。"""

from __future__ import annotations

import uuid

from ..repositories.lap_repo import LapRepository
from ..repositories.sample_repo import SampleRepository
from ..repositories.session_repo import SessionRepository
from ..repositories.track_repo import TrackRepository
from .lap_processor import LapResult

SAMPLE_OUT_KEYS = (
    ("fl_p", "flP"), ("fl_t", "flT"), ("fl_bt", "flBt"),
    ("fr_p", "frP"), ("fr_t", "frT"), ("fr_bt", "frBt"),
    ("rl_p", "rlP"), ("rl_t", "rlT"), ("rl_bt", "rlBt"),
    ("rr_p", "rrP"), ("rr_t", "rrT"), ("rr_bt", "rrBt"),
)


class HistoryService:
    def __init__(
        self,
        tracks: TrackRepository,
        sessions: SessionRepository,
        laps: LapRepository,
        samples: SampleRepository,
    ) -> None:
        self.tracks = tracks
        self.sessions = sessions
        self.laps = laps
        self.samples = samples

    # ------------------------------------------------------------------ 写入
    def save_lap(self, session_id: str, result: LapResult) -> dict:
        lap = {
            "id": uuid.uuid4().hex,
            "session_id": session_id,
            "no": result.no,
            "ms": result.ms,
            "kind": result.kind,
            "valid": result.valid,
            "s1": result.s1,
            "s2": result.s2,
            "s3": result.s3,
            "session_type": result.session_type,
        }
        self.laps.insert_with_samples(lap=lap, samples=result.samples)
        return lap

    def pb_ms(self, track_id: str) -> int | None:
        return self.laps.pb_ms(track_id)

    def pb_reference(self, track_id: str) -> dict | None:
        return self.samples.pb_reference(track_id)

    # ------------------------------------------------------------------ 查询
    def list_sessions(
        self, *, live_session_id: str | None = None, streaming: bool = False
    ) -> list[dict]:
        """每条 session（每次进图）作为独立历史记录。"""
        items = self.sessions.list_summaries()
        for item in items:
            item["live"] = bool(streaming and item["id"] == live_session_id)
        return items

    def list_laps(self, session_id: str) -> list[dict] | None:
        if self.sessions.get(session_id) is None:
            return None

        rows = self.laps.list_for_session(session_id)
        pb = self.laps.pb_lap_for_session(session_id)
        pb_ms = pb["ms"] if pb else None
        output = []
        for row in rows:
            delta = None
            if pb_ms is not None and row["kind"] == "ok":
                delta = row["ms"] - pb_ms
            output.append(
                {
                    "no": row["no"],
                    "ms": row["ms"],
                    "kind": row["kind"],
                    "valid": row["valid"],
                    "deltaPb": delta,
                    "pb": bool(pb and row["id"] == pb["id"]),
                    "pbMs": pb_ms,
                    "pbNo": pb["no"] if pb else None,
                    "sessionId": row["session_id"],
                    "sessionType": row.get("session_type") or "unknown",
                }
            )
        return output

    def get_lap(self, session_id: str, no: int) -> dict | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        track = self.tracks.get(session["track_id"])
        if track is None:
            return None
        lap = self.laps.find_in_session(session_id, no)
        if lap is None:
            return None
        samples = self.samples.list_for_lap(lap["id"])
        return {
            "no": lap["no"],
            "ms": lap["ms"],
            "kind": lap["kind"],
            "valid": lap["valid"],
            "s1": lap["s1"],
            "s2": lap["s2"],
            "s3": lap["s3"],
            "track": {
                "id": track["id"],
                "spline": track["spline"],
                "sectors": track["sectors"],
            },
            "samples": [self._sample_out(s) for s in samples],
            "pbMs": self.laps.pb_ms_for_session(session_id),
            "sessionId": session_id,
            "sessionType": lap.get("session_type") or session.get("session_type") or "unknown",
        }

    # ------------------------------------------------------------------ 删除
    def delete_session(self, session_id: str) -> dict | None:
        return self.sessions.delete(session_id)

    def clear_all(self) -> dict:
        return self.sessions.delete_all()

    # ------------------------------------------------------------------ 转换
    @staticmethod
    def _sample_out(sample: dict) -> dict:
        out = {
            "p": sample["p"],
            "speed": sample["speed"],
            "rpm": sample["rpm"],
            "gear": sample["gear"],
            "throttle": sample["throttle"],
            "brake": sample["brake"],
            "steer": sample["steer"],
            "tMs": sample.get("t_ms"),
            "gLong": sample.get("g_long"),
            "gLat": sample.get("g_lat"),
            "x": sample.get("wx"),
            "z": sample.get("wy"),
        }
        for src, dst in SAMPLE_OUT_KEYS:
            out[dst] = sample.get(src)
        return out
