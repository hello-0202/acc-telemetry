"""确定性的赛道形状生成。

ACC 共享内存不提供赛道几何，这里沿用前端原型 model.js 的标定方式：
一组基础航点 + 按 seed 抖动 + Catmull-Rom 平滑 + 等弧长重采样。
生成的是 480x290 视口内的近似形状，用于迷你地图，不是真实测绘数据。
"""

from __future__ import annotations

import hashlib
import math

BASE_WPTS = (
    (55, 236), (200, 242), (330, 236), (405, 218), (448, 158), (428, 104),
    (376, 66), (312, 86), (262, 56), (198, 78), (150, 58), (102, 86),
    (64, 128), (90, 188), (132, 222),
)

X_MIN, X_MAX = 44.0, 444.0
Y_MIN, Y_MAX = 46.0, 250.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def hash1(n: float) -> float:
    s = math.sin(n * 127.1 + 0.7) * 43758.5453
    return s - math.floor(s)


def fallback_seed(track_id: str) -> float:
    digest = hashlib.sha1(track_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF * 100.0


def _track_wpts(seed: float) -> list[tuple[float, float]]:
    out = []
    for i, (x, y) in enumerate(BASE_WPTS):
        a = i * 0.85 + seed
        r = 12 + 9 * hash1(seed * 7 + i * 3.1)
        out.append(
            (
                _clamp(x + math.cos(a) * r, X_MIN, X_MAX),
                _clamp(y + math.sin(a) * r * 0.62, Y_MIN, Y_MAX),
            )
        )
    return out


def _catmull_rom_closed(pts: list[tuple[float, float]], sps: int) -> list[tuple[float, float]]:
    n = len(pts)
    out: list[tuple[float, float]] = []
    for i in range(n):
        p0 = pts[(i - 1 + n) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        for j in range(sps):
            t = j / sps
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    return out


def _resample_closed(
    pts: list[tuple[float, float]], n: int
) -> tuple[list[tuple[float, float]], float]:
    cum = [0.0]
    for i in range(len(pts)):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        cum.append(cum[i] + math.hypot(b[0] - a[0], b[1] - a[1]))
    length = cum[-1]
    out: list[tuple[float, float]] = []
    j = 0
    for i in range(n):
        target = length * i / n
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        span = (cum[j + 1] - cum[j]) or 1.0
        t = (target - cum[j]) / span
        a = pts[j % len(pts)]
        b = pts[(j + 1) % len(pts)]
        out.append((_lerp(a[0], b[0], t), _lerp(a[1], b[1], t)))
    return out, length


def build_spline(seed: float, n: int = 160) -> list[list[float]]:
    """返回 n 个闭合点，格式 [[x, y], ...]，保留两位小数。"""
    wpts = _track_wpts(seed)
    dense = _catmull_rom_closed(wpts, 22)
    pts, _ = _resample_closed(dense, n)
    return [[round(x, 2), round(y, 2)] for x, y in pts]
