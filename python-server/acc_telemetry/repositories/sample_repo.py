"""单圈采样仓储。"""

from __future__ import annotations

from ..db import Database
from ..sample_util import monotonic_progress


def build_reference(
    samples: list[dict],
    ms: int,
    *,
    lap_id: str | None = None,
    lap_no: int | None = None,
) -> dict | None:
    """把一圈采样压成「进度 → 时间」的参考曲线。

    `samples` 按时间存、p 可能非单调（出赛道绕圈时 p 不前进），必须先滤成
    p 单调递增，否则前端按 p 插值会把 delta 算到别的弯上去。

    参考曲线必须覆盖到接近终点。早先圈边界被重复判定时会落下一批「圈速正常、
    采样却只有起跑线几百毫秒」的残圈；用它插值会在整圈上夹到 ts[-1]，
    delta 直接变成几十秒。宁可没有参考（null），也不能给错数。
    """
    ordered = monotonic_progress(samples)
    if len(ordered) < 2 or float(ordered[-1]["p"]) < 0.9:
        return None
    ps: list[float] = []
    ts: list[float] = []
    for s in ordered:
        p = float(s["p"])
        ps.append(p)
        t = s.get("t_ms")
        # t_ms 缺失或为 0（旧数据/测试数据）时，退回按进度线性估算
        ts.append(float(t) if t is not None and float(t) > 0 else p * ms)
    # 保证单调递增，插值才安全
    for i in range(1, len(ts)):
        if ts[i] < ts[i - 1]:
            ts[i] = ts[i - 1]
    return {
        "p": ps,
        "tMs": ts,
        "lapId": lap_id,
        "lapNo": lap_no,
        "ms": ms,
    }


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
                       t_ms, g_long, g_lat, wx, wy
                  FROM samples WHERE lap_id = ? ORDER BY idx ASC
                """,
                (lap_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _reference(self, pb: dict) -> dict | None:
        """从数据库读一圈采样并压成参考曲线（见 `build_reference`）。"""
        return build_reference(
            self.list_for_lap(pb["id"]),
            pb["ms"],
            lap_id=pb["id"],
            lap_no=pb["no"],
        )

    def pb_reference(
        self, track_id: str, car_name: str | None = None
    ) -> dict | None:
        """PB 圈按进度对齐的时间参考，用于实时 delta 与前端 PB 对比。

        `car_name` 传入当前车时只取这台车的 PB —— 参考圈必须和正在跑的车一致，
        否则换车后的 delta 会被另一台车的走线/圈速带偏。
        """
        from .lap_repo import LapRepository

        pb = LapRepository(self.db).pb_lap(track_id, car_name)
        if not pb:
            return None
        return self._reference(pb)

    def pb_reference_for_type(
        self, track_id: str, session_type: str, car_name: str | None = None
    ) -> dict | None:
        """按会话类型取 PB 参考圈；类型未知时退化为整条赛道的全局 PB 参考。

        用于实时监测：跑正赛时只对比历史正赛最佳、跑排位时只对比排位最佳，
        而不是整条赛道混合所有类型的最快圈。车同样参与过滤（见 `pb_reference`）。
        """
        from .lap_repo import LapRepository

        if not session_type or session_type == "unknown":
            return self.pb_reference(track_id, car_name)
        pb = LapRepository(self.db).pb_lap_for_type(track_id, session_type, car_name)
        if not pb:
            return None
        return self._reference(pb)
