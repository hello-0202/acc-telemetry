"""lap.deltaBestMs（对游戏内本场最佳）的计算口径。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.state import _delta_best_ms  # noqa: E402


def snap(*, lap_ms: int = 52_000, progress: float = 0.5, best_ms: int = 100_000):
    return SimpleNamespace(
        lap_time_ms=lap_ms,
        progress=progress,
        best_lap_ms=best_ms,
    )


def ref(ms: int, *, p=(0.0, 0.5, 1.0), ts=(0.0, 50_000.0, 100_000.0)):
    return {"p": list(p), "tMs": list(ts), "ms": ms}


def test_uses_session_best_reference_when_it_matches_game_best() -> None:
    meta = {"sessionRef": ref(100_000), "pbRef": ref(98_000)}
    # p=0.5 → 参考时间 50s；LAP 52s → +2s
    assert _delta_best_ms(snap(), meta) == 2_000


def test_falls_back_to_pb_and_scales_to_game_best() -> None:
    # PB 圈 98s，本场最佳 100s：参考曲线按 100/98 缩放。
    # p=0.5 → 50s × 100/98 ≈ 51.02s；LAP 52s → ≈ +980ms
    meta = {"pbRef": ref(98_000)}
    assert _delta_best_ms(snap(), meta) == 980


def test_falls_back_without_step_at_start_line() -> None:
    # 起跑线附近参考时间≈0，缩放后仍是 0，不能出现 -2s 的假「更快」
    meta = {"pbRef": ref(98_000)}
    assert _delta_best_ms(snap(lap_ms=100, progress=0.0), meta) == 100


def test_ignores_session_ref_whose_time_does_not_match_game_best() -> None:
    # 本场记录到的最快圈 99s ≠ 游戏本场最佳 100s（最佳圈不是我们录的）
    # → 应该改用 PB 参考（98s）并缩放到 100s
    meta = {"sessionRef": ref(99_000), "pbRef": ref(98_000)}
    assert _delta_best_ms(snap(), meta) == 980


def test_uses_fastest_overall_ref_when_best_lap_was_invalid() -> None:
    # ACC 的 iBestTime 可能包含被判无效的圈：有效圈曲线 99s 对不上，
    # 但「任意圈最快」等于 100s 的曲线能对上
    meta = {"sessionRef": ref(99_000), "sessionFastRef": ref(100_000)}
    assert _delta_best_ms(snap(), meta) == 2_000


def test_linear_fallback_without_any_reference_curve() -> None:
    # 无曲线时按 progress 线性估算，终点对齐本场最佳
    meta = {"pbMs": 98_000}
    assert _delta_best_ms(snap(), meta) == 2_000


def test_none_before_game_has_a_best_lap() -> None:
    meta = {"pbRef": ref(98_000)}
    assert _delta_best_ms(snap(best_ms=0), meta) is None
