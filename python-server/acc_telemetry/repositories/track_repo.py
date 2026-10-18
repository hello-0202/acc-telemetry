"""赛道仓储。"""

from __future__ import annotations

import json

from ..db import Database


class TrackRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        *,
        id: str,
        name: str,
        length_m: float | None,
        spline: list[list[float]],
        sectors: list[float],
        date: str,
        source: str = "generated",
    ) -> None:
        """source='recorded' 表示由实跑数据生成，后续不会被 generated 覆盖。"""
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO tracks (id, name, length_m, spline, sectors, date, map_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name     = excluded.name,
                    length_m = COALESCE(excluded.length_m, tracks.length_m),
                    spline   = CASE
                                 WHEN tracks.map_source = 'recorded'
                                      AND excluded.map_source != 'recorded'
                                   THEN tracks.spline
                                 WHEN excluded.spline NOT IN ('', '[]')
                                   THEN excluded.spline
                                 ELSE tracks.spline
                               END,
                    sectors  = excluded.sectors,
                    map_source = CASE WHEN tracks.map_source = 'recorded'
                                      THEN 'recorded' ELSE excluded.map_source END,
                    date     = CASE WHEN tracks.date IS NULL OR excluded.date > tracks.date
                                    THEN excluded.date ELSE tracks.date END
                """,
                (
                    id,
                    name,
                    length_m,
                    json.dumps(spline, ensure_ascii=False),
                    json.dumps(sectors),
                    date,
                    source,
                ),
            )

    def get(self, track_id: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id, name, length_m, spline, sectors, date, map_source "
                "FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def list_summaries(self) -> list[dict]:
        sql = """
            SELECT t.id, t.name, t.length_m, t.date,
                   COUNT(l.id) AS laps,
                   MIN(CASE WHEN l.kind = 'ok' AND l.valid = 1 THEN l.ms END) AS pb_ms,
                   (SELECT s2.car_name FROM sessions s2
                     WHERE s2.track_id = t.id
                     ORDER BY s2.date DESC, s2.rowid DESC LIMIT 1) AS car
              FROM tracks t
              LEFT JOIN sessions s ON s.track_id = t.id
              LEFT JOIN laps l     ON l.session_id = s.id
             GROUP BY t.id
             ORDER BY t.date DESC, t.name ASC
        """
        with self.db.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "lenM": r["length_m"],
                "laps": r["laps"],
                "pbMs": r["pb_ms"],
                "date": r["date"],
                "car": r["car"],
            }
            for r in rows
        ]

    def delete(self, track_id: str) -> dict | None:
        """删除赛道并返回删除数量；不存在返回 None。级联依赖 FK + foreign_keys=ON。"""
        with self.db.connection() as conn:
            exists = conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not exists:
                return None
            sessions = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE track_id = ?", (track_id,)
            ).fetchone()[0]
            laps = conn.execute(
                """
                SELECT COUNT(*) FROM laps l
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ?
                """,
                (track_id,),
            ).fetchone()[0]
            samples = conn.execute(
                """
                SELECT COUNT(*) FROM samples sa
                  JOIN laps l     ON l.id = sa.lap_id
                  JOIN sessions s ON s.id = l.session_id
                 WHERE s.track_id = ?
                """,
                (track_id,),
            ).fetchone()[0]
            conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        return {"sessions": sessions, "laps": laps, "samples": samples}

    def count(self) -> int:
        with self.db.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    @staticmethod
    def _decode(row) -> dict:
        try:
            spline = json.loads(row["spline"] or "[]")
        except (TypeError, ValueError):
            spline = []
        try:
            sectors = json.loads(row["sectors"] or "[0.3333,0.6667]")
        except (TypeError, ValueError):
            sectors = [1 / 3, 2 / 3]
        return {
            "id": row["id"],
            "name": row["name"],
            "length_m": row["length_m"],
            "spline": spline,
            "sectors": sectors,
            "date": row["date"],
            "map_source": row["map_source"],
        }
