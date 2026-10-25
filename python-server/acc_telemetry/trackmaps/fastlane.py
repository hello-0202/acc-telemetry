"""ACC fastlane.ai 解析与赛道路径文件定位。

文件布局（详见游戏 Cache 内的 AI_PARSING_NOTES.md）：

    Header (16 B):
        int32 version
        int32 point_count
        int64 reserved

    第一段（point_count × 36 B）:
        double x, y, z, distance
        int32 index

    第二段（20 B 段头 + point_count × 80 B）:
        int32 count + 16 B 零
        每条 20 个 float32:
          f0 radius, f1 left_edge, f2 right_edge,
          f10 segment_length, f11..f13 direction

    第三段是未解码的 AI 辅助索引，直接跳过。
"""

from __future__ import annotations

import math
import os
import re
import struct
from pathlib import Path

GAME_FOLDER = "Assetto Corsa Competizione"
CACHE_SUFFIX = Path("AC2") / "Content" / "Cache"

_DEFAULT_CACHES = (
    Path(r"D:\Game\SteamLibrary\steamapps\common") / GAME_FOLDER / CACHE_SUFFIX,
    Path(r"C:\Program Files (x86)\Steam\steamapps\common") / GAME_FOLDER / CACHE_SUFFIX,
    Path(r"C:\Program Files\Steam\steamapps\common") / GAME_FOLDER / CACHE_SUFFIX,
)

_VDF_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"),
    Path(r"C:\Program Files\Steam\steamapps\libraryfolders.vdf"),
)


def _steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    for vdf in _VDF_CANDIDATES:
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            raw = match.group(1).replace("\\\\", "\\")
            libraries.append(Path(raw))
    return libraries


def find_cache_dir() -> Path | None:
    """定位 ACC 的 AC2/Content/Cache 目录。

    优先使用环境变量 ACC_CACHE_DIR；否则按常见安装路径 + Steam 库探测。
    """
    env = os.getenv("ACC_CACHE_DIR")
    if env:
        candidate = Path(env)
        if candidate.name.lower() != "cache" and (candidate / CACHE_SUFFIX).is_dir():
            candidate = candidate / CACHE_SUFFIX
        if candidate.is_dir():
            return candidate

    for candidate in _DEFAULT_CACHES:
        if candidate.is_dir():
            return candidate

    for library in _steam_libraries():
        candidate = library / "steamapps" / "common" / GAME_FOLDER / CACHE_SUFFIX
        if candidate.is_dir():
            return candidate
    return None


def find_fastlane(track_id: str, cache_dir: Path | str | None = None) -> Path | None:
    """按 ACC 内部赛道名查找 fastlane.ai。"""
    cache = Path(cache_dir) if cache_dir else find_cache_dir()
    if cache is None or not cache.is_dir():
        return None

    direct = cache / track_id / "fastlane.ai"
    if direct.is_file():
        return direct

    # Windows 文件系统大小写不敏感，但这里仍做个兜底，兼容其他环境
    wanted = (track_id or "").lower()
    try:
        for child in cache.iterdir():
            if child.is_dir() and child.name.lower() == wanted:
                candidate = child / "fastlane.ai"
                if candidate.is_file():
                    return candidate
    except OSError:
        return None
    return None


def parse_fastlane(path: Path | str) -> dict:
    """供 GeometryService 使用的完整几何结构。"""
    points = parse_fastlane_ai(path)
    length_m = float(points[-1]["distance"]) if points else 0.0
    return {
        "pointCount": len(points),
        "lengthM": round(length_m, 1),
        "points": points,
    }


def parse_fastlane_ai(path: Path | str) -> list[dict]:
    """解析 fastlane.ai，返回前端地图需要的点。

    每个点:
        index, x, z, distance, l, r, dir_x, dir_z
    """
    data = Path(path).read_bytes()
    if len(data) < 16:
        raise ValueError("fastlane.ai 文件过短")

    version, count = struct.unpack_from("<2i", data, 0)
    if count <= 0 or count > 1_000_000:
        raise ValueError(f"非法 point_count: {count}")

    points_start = 16
    sec2 = points_start + 36 * count
    if len(data) < sec2 + 20:
        raise ValueError("fastlane.ai 第一段长度不足")

    count2 = struct.unpack_from("<i", data, sec2)[0]
    if count2 != count:
        raise ValueError(f"第二段点数不一致: {count2} != {count}")

    # 第一段：中心线坐标 + 累计里程
    spline: list[tuple[float, float, float, float]] = []
    off = points_start
    for _ in range(count):
        x, y, z, distance = struct.unpack_from("<4d", data, off)
        spline.append((x, y, z, distance))
        off += 36

    # 第二段：左右宽度 / 曲率 / 方向
    rec = sec2 + 20
    if len(data) < rec + 80 * count:
        raise ValueError("fastlane.ai 第二段长度不足")

    out: list[dict] = []
    for i in range(count):
        floats = struct.unpack_from("<20f", data, rec + 80 * i)
        x, y, z, distance = spline[i]

        left = max(0.0, float(floats[1]))
        right = max(0.0, float(floats[2]))

        # 用相邻点中心差分求行进方向，比文件里的近似方向更稳定
        prev = spline[(i - 1) % count]
        nxt = spline[(i + 1) % count]
        dx = nxt[0] - prev[0]
        dz = nxt[2] - prev[2]
        norm = math.hypot(dx, dz)
        if norm < 1e-6:
            dx, dz = float(floats[11]), float(floats[13])
            norm = math.hypot(dx, dz)
        if norm < 1e-6:
            dx, dz, norm = 0.0, 1.0, 1.0
        dx /= norm
        dz /= norm

        out.append(
            {
                "index": i,
                "x": round(x, 3),
                "y": round(y, 3),
                "z": round(z, 3),
                "distance": round(distance, 3),
                "l": round(left, 3),
                "r": round(right, 3),
                "dir_x": round(dx, 6),
                "dir_z": round(dz, 6),
                "radius": round(float(floats[0]), 2),
            }
        )
    return out
