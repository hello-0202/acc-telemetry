"""监测会话仓储。"""

from __future__ import annotations

from ..db import Database


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, *, id: str, track_id: str, car_name: str, date: str) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, track_id, car_name, date) VALUES (?, ?, ?, ?)",
                (id, track_id, car_name, date),
            )

    def latest(self, track_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT id, track_id, car_name, date
                  FROM sessions WHERE track_id = ?
                 ORDER BY date DESC, rowid DESC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
        return dict(row) if row else None

    def get(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id, track_id, car_name, date FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_for_track(self, track_id: str) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, track_id, car_name, date FROM sessions
                 WHERE track_id = ? ORDER BY date DESC, rowid DESC
                """,
                (track_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_summaries(self) -> list[dict]:
        """每个 session 作为一条独立历史记录（同一赛道多次进图 = 多条记录）。"""
        sql = """
            SELECT s.id, s.track_id, s.car_name, s.date,
                   t.name, t.length_m,
                   COUNT(l.id) AS laps,
                   MIN(CASE WHEN l.kind = 'ok' AND l.valid = 1 THEN l.ms END) AS pb_ms
              FROM sessions s
              JOIN tracks t ON t.id = s.track_id
              LEFT JOIN laps l ON l.session_id = s.id
             GROUP BY s.id
             ORDER BY s.rowid DESC
        """
        with self.db.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            {
                "id": r["id"],
                "trackId": r["track_id"],
                "name": r["name"],
                "lenM": r["length_m"],
                "car": r["car_name"],
                "date": r["date"],
                "laps": r["laps"],
                "pbMs": r["pb_ms"],
            }
            for r in rows
        ]

    def delete(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not exists:
                return None
            laps = conn.execute(
                "SELECT COUNT(*) FROM laps WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            samples = conn.execute(
                """
                SELECT COUNT(*) FROM samples sa
                  JOIN laps l ON l.id = sa.lap_id
                 WHERE l.session_id = ?
                """,
                (session_id,),
            ).fetchone()[0]
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return {"sessions": 1, "laps": int(laps), "samples": int(samples)}

    def delete_all(self) -> dict:
        with self.db.connection() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            laps = conn.execute("SELECT COUNT(*) FROM laps").fetchone()[0]
            samples = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            conn.execute("DELETE FROM sessions")
        return {"sessions": int(sessions), "laps": int(laps), "samples": int(samples)}
