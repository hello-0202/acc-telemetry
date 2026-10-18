"""单圈采样仓储。"""

from __future__ import annotations

from ..db import Database


class SampleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_for_lap(self, lap_id: str) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT idx, p, speed, rpm, gear, throttle, brake, steer,
                       fl_p, fl_t, fl_bt, fr_p, fr_t, fr_bt,
                       rl_p, rl_t, rl_bt, rr_p, rr_t, rr_bt,
                       t_ms, g_long, g_lat
                  FROM samples WHERE lap_id = ? ORDER BY idx ASC
                """,
                (lap_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def pb_reference(self, track_id: str) -> dict | None:
        """PB 圈按进度对齐的时间参考，用于实时 delta 与前端 PB 对比。"""
        from .lap_repo import LapRepository

        pb = LapRepository(self.db).pb_lap(track_id)
        if not pb:
            return None
        samples = self.list_for_lap(pb["id"])
        if not samples:
            return None
        ps: list[float] = []
        ts: list[float] = []
        for s in samples:
            ps.append(float(s["p"]))
            t = s.get("t_ms")
            # t_ms 缺失或为 0（旧数据/测试数据）时，退回按进度线性估算
            ts.append(float(t) if t is not None and float(t) > 0 else float(s["p"]) * pb["ms"])
        # 保证单调递增，插值才安全
        for i in range(1, len(ts)):
            if ts[i] < ts[i - 1]:
                ts[i] = ts[i - 1]
        return {"p": ps, "tMs": ts, "lapId": pb["id"], "lapNo": pb["no"], "ms": pb["ms"]}
