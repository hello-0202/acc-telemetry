from __future__ import annotations

import math

from acc_telemetry.trackmaps.from_track import (
    build_spline_from_samples,
    spline_is_degenerate,
)


def _circle_samples(n: int = 240, radius: float = 100.0) -> list[dict]:
    samples = []
    for i in range(n):
        p = (i + 0.5) / n
        angle = 2 * math.pi * p
        samples.append(
            {"p": p, "wx": radius * math.cos(angle), "wy": radius * math.sin(angle)}
        )
    return samples


def test_builds_spline_inside_viewbox():
    spline = build_spline_from_samples(_circle_samples())
    assert spline is not None
    assert len(spline) == 160
    xs = [p[0] for p in spline]
    ys = [p[1] for p in spline]
    assert min(xs) >= 20 and max(xs) <= 460
    assert min(ys) >= 20 and max(ys) <= 270
    # 首尾应几乎重合（闭合环线）
    assert math.hypot(xs[0] - xs[-1], ys[0] - ys[-1]) < 20


def test_rejects_partial_lap_coverage():
    samples = [s for s in _circle_samples() if 0.3 < s["p"] < 0.8]
    assert build_spline_from_samples(samples) is None


def test_rejects_missing_world_coordinates():
    samples = [{"p": i / 100, "wx": None, "wy": None} for i in range(100)]
    assert build_spline_from_samples(samples) is None


def test_rejects_too_few_points():
    samples = _circle_samples(n=20)
    assert build_spline_from_samples(samples) is None


def test_rejects_degenerate_line():
    # 用错坐标轴（例如把高度 y 当平面）时生成出来是一条直线，必须拒绝
    samples = [
        {"p": (i + 0.5) / 240, "wx": float(i * 10), "wy": 0.0} for i in range(240)
    ]
    assert build_spline_from_samples(samples) is None


def test_spline_degeneracy_check():
    assert spline_is_degenerate([[0, 0], [100, 0], [200, 0]] * 20)
    circle = build_spline_from_samples(_circle_samples())
    assert circle is not None
    assert spline_is_degenerate(circle) is False
