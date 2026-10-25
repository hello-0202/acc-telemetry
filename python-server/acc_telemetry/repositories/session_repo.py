"""监测会话仓储。"""

from __future__ import annotations

from ..db import Database


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        id: str,
        track_id: str,
        car_name: str,
        date: str,
        session_type: str = "unknown",
    ) -> None:
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, track_id, car_name, date, session_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (id, track_id, car_name, date, session_type or "unknown"),
            )

    def set_type(self, session_id: str, session_type: str) -> bool:
        """会话开始时 ACC 还没给出类型（graphics.session = -1）时，事后补写。

        只覆盖原本是 unknown 的记录，不会把已经标好的类型改掉；
        同一会话里此前落库、同样是 unknown 的圈一起补齐。
        """
        if not session_type or session_type == "unknown":
            return False
        with self.db.connection() as conn:
            cur = conn.execute(
                """
                UPDATE sessions SET session_type = ?
                 WHERE id = ? AND (session_type IS NULL OR session_type = 'unknown')
                """,
                (session_type, session_id),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                """
                UPDATE laps SET session_type = ?
                 WHERE session_id = ? AND (session_type IS NULL OR session_type = 'unknown')
                """,
                (session_type, session_id),
            )
        return True

    def latest(self, track_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT id, track_id, car_name, date, session_type
                  FROM sessions WHERE track_id = ?
                 ORDER BY date DESC, rowid DESC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
        return dict(row) if row else None

    def get(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT id, track_id, car_name, date, session_type
                  FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_for_track(self, track_id: str) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, track_id, car_name, date, session_type FROM sessions
                 WHERE track_id = ? ORDER BY date DESC, rowid DESC
                """,
                (track_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_summaries(self) -> list[dict]:
        """每个 session 作为一条独立历史记录（同一赛道多次进图 = 多条记录）。"""
        sql = """
            SELECT s.id, s.track_id, s.car_name, s.date, s.session_type,
                   t.name, t.length_m,
                   COUNT(l.id) AS laps,
                   GROUP_CONCAT(DISTINCT l.session_type) AS lap_types,
                   MIN(CASE WHEN l.kind = 'ok' AND l.valid = 1 THEN l.ms END) AS pb_ms
              FROM sessions s
              JOIN tracks t ON t.id = s.track_id
              LEFT JOIN laps l ON l.session_id = s.id
             GROUP BY s.id
             ORDER BY s.rowid DESC
        """
        with self.db.connection() as conn:
            rows = conn.execute(sql).fetchall()
        result = []
        for r in rows:
            lap_types = [t for t in (r["lap_types"] or "").split(",") if t]
            result.append(
                {
                    "id": r["id"],
                    "trackId": r["track_id"],
                    "name": r["name"],
                    "lenM": r["length_m"],
                    "car": r["car_name"],
                    "date": r["date"],
                    "sessionType": r["session_type"] or "unknown",
                    # 同一记录里可能既有排位赛又有正赛，前端据此做二级分类
                    "sessionTypes": lap_types or [r["session_type"] or "unknown"],
                    "laps": r["laps"],
                    "pbMs": r["pb_ms"],
                }
            )
        return result

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
