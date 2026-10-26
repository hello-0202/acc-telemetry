"""从一圈遥测的世界坐标自动生成赛道地图。

ACC 不提供赛道几何，但 graphics.carCoordinates 里有玩家车的世界坐标（米）。
跑完一圈后把 (p, wx, wy) 按进度排序、平滑、归一化到 480x290 视口，
再等弧长重采样成固定点数。生成结果可覆盖内置的近似形状。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..sample_util import monotonic_progress

VIEW_W = 480.0
VIEW_H = 290.0
PADDING = 24.0
MIN_POINTS = 40
MIN_LENGTH_M = 200.0


def _smooth(values: Sequence[float], window: int = 2) -> list[float]:
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n - 1, i + window)
        chunk = values[lo : hi + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def spline_is_degenerate(spline: Sequence[Sequence[float]]) -> bool:
    """形状退化成一条线（例如用错了坐标轴）时不值得使用。"""
    if not spline or len(spline) < 32:
        return True
    xs = [float(p[0]) for p in spline]
    ys = [float(p[1]) for p in spline]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    longest = max(span_x, span_y)
    if longest <= 1e-6:
        return True
    return min(span_x, span_y) / longest < 0.03


def _resample_closed(
    points: Sequence[Sequence[float]], n: int
) -> list[list[float]]:
    """把闭合折线等弧长重采样为 n 个点。"""
    if len(points) < 2 or n < 2:
        return []
    cumulative = [0.0]
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        cumulative.append(
            cumulative[-1] + ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        )
    total = cumulative[-1]
    if total <= 0:
        return []
    out: list[list[float]] = []
    j = 0
    for i in range(n):
        target = total * i / n
        while j < len(cumulative) - 2 and cumulative[j + 1] < target:
            j += 1
        span = (cumulative[j + 1] - cumulative[j]) or 1.0
        t = (target - cumulative[j]) / span
        a = points[j % len(points)]
        b = points[(j + 1) % len(points)]
        out.append(
            [
                round(a[0] + (b[0] - a[0]) * t, 2),
                round(a[1] + (b[1] - a[1]) * t, 2),
            ]
        )
    return out


def build_spline_from_geometry(
    geometry: dict,
    n: int = 240,
    width: float = VIEW_W,
    height: float = VIEW_H,
    padding: float = PADDING,
) -> list[list[float]] | None:
    """把 fastlane.ai 的真实世界坐标转成 480x290 视口内的中心线折线。

    与 `build_spline_from_samples` 的区别：这里使用赛道中心线，不依赖实跑数据，
    因此第一次进入赛道就能得到准确形状。
    """
    points = geometry.get("points") if isinstance(geometry, dict) else None
    if not points or len(points) < 32:
        return None

    rows = []
    min_x = min_z = float("inf")
    max_x = max_z = float("-inf")
    for p in points:
        try:
            x, z = float(p["x"]), float(p["z"])
        except (KeyError, TypeError, ValueError):
            continue
        nx = -float(p.get("dir_z", 0.0))
        nz = float(p.get("dir_x", 0.0))
        l = max(0.0, float(p.get("l", 0.0)))
        r = max(0.0, float(p.get("r", 0.0)))
        rows.append((x, z))
        for px, pz in ((x, z), (x + nx * l, z + nz * l), (x - nx * r, z - nz * r)):
            min_x = min(min_x, px)
            max_x = max(max_x, px)
            min_z = min(min_z, pz)
            max_z = max(max_z, pz)

    if len(rows) < 32:
        return None

    span_x = max(max_x - min_x, 1e-6)
    span_z = max(max_z - min_z, 1e-6)
    if min(span_x, span_z) / max(span_x, span_z) < 0.03:
        return None

    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_z)
    offset_x = (width - span_x * scale) / 2
    offset_y = (height - span_z * scale) / 2
    # 世界 Z 向上，屏幕 Y 向下 → 翻转
    screen = [
        (offset_x + (x - min_x) * scale, offset_y + (max_z - z) * scale)
        for x, z in rows
    ]
    return _resample_closed(screen, n)


def build_spline_from_samples(
    samples: Sequence[dict],
    n: int = 160,
    width: float = VIEW_W,
    height: float = VIEW_H,
    padding: float = PADDING,
) -> list[list[float]] | None:
    """从一圈的降采样点生成地图折线；数据不足或覆盖不全时返回 None。"""
    # samples 按时间存、p 非单调（出赛道绕圈会让 p 回退）。先滤成 p 单调的
    # 序列，否则绕圈段那一堆点排序后会互相穿插，把赛道形状糊成一团。
    rows = monotonic_progress(
        s for s in samples
        if s.get("wx") is not None and s.get("wy") is not None
    )
    points = [(float(s["p"]), float(s["wx"]), float(s["wy"])) for s in rows]
    if len(points) < MIN_POINTS:
        return None

    points.sort(key=lambda item: item[0])
    if points[0][0] > 0.05 or points[-1][0] < 0.95:
        # 起步/停表位置覆盖不到整圈，形状不可靠
        return None

    xs = _smooth([p[1] for p in points])
    ys = _smooth([p[2] for p in points])

    length = sum(
        ((xs[i] - xs[i - 1]) ** 2 + (ys[i] - ys[i - 1]) ** 2) ** 0.5
        for i in range(1, len(xs))
    )
    if length < MIN_LENGTH_M:
        return None

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    # 退化成一维（坐标轴用错）时直接放弃，避免覆盖成一条直线
    if min(span_x, span_y) / max(span_x, span_y) < 0.03:
        return None
    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)
    offset_x = (width - span_x * scale) / 2
    offset_y = (height - span_y * scale) / 2

    # 世界坐标 y 向上，屏幕 y 向下 → 翻转
    screen = [
        (offset_x + (x - min_x) * scale, offset_y + (max_y - y) * scale)
        for x, y in zip(xs, ys)
    ]

    cumulative = [0.0]
    for i in range(len(screen)):
        a = screen[i]
        b = screen[(i + 1) % len(screen)]
        cumulative.append(
            cumulative[-1] + ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        )
    total = cumulative[-1]
    if total <= 0:
        return None

    out: list[list[float]] = []
    j = 0
    for i in range(n):
        target = total * i / n
        while j < len(cumulative) - 2 and cumulative[j + 1] < target:
            j += 1
        span = (cumulative[j + 1] - cumulative[j]) or 1.0
        t = (target - cumulative[j]) / span
        a = screen[j % len(screen)]
        b = screen[(j + 1) % len(screen)]
        out.append(
            [
                round(a[0] + (b[0] - a[0]) * t, 2),
                round(a[1] + (b[1] - a[1]) * t, 2),
            ]
        )
    return out
