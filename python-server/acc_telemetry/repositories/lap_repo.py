"""圈速仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from ..db import Database

LAP_FIELDS = (
    "id", "session_id", "no", "ms", "kind", "valid",
    "s1", "s2", "s3", "session_type", "created_at",
)


class LapRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_with_samples(self, *, lap: dict, samples: Sequence[dict]) -> None:
        """一圈 + 全部采样在同一事务内写入，任一失败整体回滚。"""
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO laps (
                    id, session_id, no, ms, kind, valid, s1, s2, s3, session_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lap["id"],
                    lap["session_id"],
                    lap["no"],
                    lap["ms"],
                    lap["kind"],
                    1 if lap["valid"] else 0,
                    lap.get("s1"),
                    lap.get("s2"),
                    lap.get("s3"),
                    lap.get("session_type") or "unknown",
                ),
            )
            if samples:
                conn.executemany(
                    """
                    INSERT INTO samples (
                        lap_id, idx, p, speed, rpm, gear, throttle, brake, steer,
                        fl_p, fl_t, fl_bt, fr_p, fr_t, fr_bt,
                        rl_p, rl_t, rl_bt, rr_p, rr_t, rr_bt,
                        t_ms, g_long, g_lat, wx, wy
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            lap["id"],
                            s["idx"],
                            s["p"],
                            s["speed"],
                            s["rpm"],
                            s["gear"],
                            s["throttle"],
                            s["brake"],
                            s["steer"],
                            s.get("fl_p"),
                            s.get("fl_t"),
                            s.get("fl_bt"),
                            s.get("fr_p"),
                            s.get("fr_t"),
                            s.get("fr_bt"),
                            s.get("rl_p"),
                            s.get("rl_t"),
                            s.get("rl_bt"),
                            s.get("rr_p"),
                            s.get("rr_t"),
                            s.get("rr_bt"),
                            s.get("t_ms"),
                            s.get("g_long"),
                            s.get("g_lat"),
                            s.get("wx"),
                            s.get("wy"),
                        )
                        for s in samples
                    ],
                )

    def list_for_session(self, session_id: str) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT l.id, l.session_id, l.no, l.ms, l.kind, l.valid,
                       l.s1, l.s2, l.s3, l.session_type, l.created_at
                  FROM laps l
                 WHERE l.session_id = ?
                 ORDER BY l.no ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._decode(r) for r in rows]

    def find_latest(self, track_id: str, no: int) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT l.id, l.session_id, l.no, l.ms, l.kind, l.valid,
                       l.s1, l.s2, l.s3, l.session_type, l.created_at,
                       s.date AS session_date
                  FROM laps l
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ? AND l.no = ?
                 ORDER BY s.date DESC, s.rowid DESC, l.created_at DESC
                 LIMIT 1
                """,
                (track_id, no),
            ).fetchone()
        return self._decode(row) if row else None

    def find_in_session(self, session_id: str, no: int) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, no, ms, kind, valid, s1, s2, s3,
                       session_type, created_at
                  FROM laps
                 WHERE session_id = ? AND no = ?
                 ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, no),
            ).fetchone()
        return self._decode(row) if row else None

    def pb_lap_for_session(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, no, ms, kind, valid, s1, s2, s3,
                       session_type, created_at
                  FROM laps
                 WHERE session_id = ? AND kind = 'ok' AND valid = 1
                 ORDER BY ms ASC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def pb_ms_for_session(self, session_id: str) -> int | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT MIN(ms) AS pb FROM laps
                 WHERE session_id = ? AND kind = 'ok' AND valid = 1
                """,
                (session_id,),
            ).fetchone()
        return int(row["pb"]) if row and row["pb"] is not None else None

    def pb_lap(self, track_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT l.id, l.session_id, l.no, l.ms, l.kind, l.valid,
                       l.s1, l.s2, l.s3, l.session_type, l.created_at
                  FROM laps l
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ? AND l.kind = 'ok' AND l.valid = 1
                 ORDER BY l.ms ASC, s.date DESC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def pb_ms(self, track_id: str) -> int | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT MIN(l.ms) AS pb
                  FROM laps l
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ? AND l.kind = 'ok' AND l.valid = 1
                """,
                (track_id,),
            ).fetchone()
        return int(row["pb"]) if row and row["pb"] is not None else None

    def count_for_track(self, track_id: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM laps l
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ?
                """,
                (track_id,),
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _decode(row) -> dict:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "no": row["no"],
            "ms": row["ms"],
            "kind": row["kind"],
            "valid": bool(row["valid"]),
            "s1": row["s1"],
            "s2": row["s2"],
            "s3": row["s3"],
            "session_type": row["session_type"] or "unknown",
            "created_at": row["created_at"],
        }
