"""
独立赛道数据采集脚本（不依赖 FastAPI / SQLite / 前端）

作用：
  以高频（默认 100Hz）直接读取 ACC 共享内存，记录玩家车的世界坐标
  （ACC 中 x-z 是赛道平面，y 是高度），每完成一圈就输出：
    - 原始采样 CSV（可事后重新处理）
    - 每圈高精度赛道 JSON（默认 600 点）
    - 一个细线 HTML 预览（含曲率识别出的弯心标记）

用法：
  cd python-server
  python tools/collect_track.py                 # 默认 100Hz / 600 点
  python tools/collect_track.py --hz 200        # 更细的采样
  python tools/collect_track.py --points 1200   # 更细的折线

  先进游戏并处于赛道内，再启动脚本；跑圈时每过一次起跑线自动保存一圈。
  Ctrl+C 结束。

输出目录：tools/track_data/
  <track>_<时间>_raw.csv
  <track>_<时间>_lap01.json
  <track>_<时间>_lap01_preview.html
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1]
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pyACCSharedMemory.acc_data import (  # noqa: E402
    ACCConstants,
    ACCGraphics,
    ACCPhysics,
    ACCStatic,
)
from pyACCSharedMemory.acc_mmap import MMapControl  # noqa: E402

FILE_MAP_READ = 0x0004
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]

BLOCKS = (
    ACCConstants.MM_PHYSICS_FILE_NAME,
    ACCConstants.MM_GRAPHICS_FILE_NAME,
    ACCConstants.MM_STATIC_FILE_NAME,
)

VIEW_W, VIEW_H = 480.0, 290.0
PADDING = 24.0


# --------------------------------------------------------------------- 探测
def section_exists(name: str) -> bool:
    handle = _k32.OpenFileMappingW(FILE_MAP_READ, False, "Local\\" + name)
    if handle:
        _k32.CloseHandle(handle)
        return True
    return False


def game_running() -> bool:
    return all(section_exists(name) for name in BLOCKS)


def wait_for_game() -> None:
    print("[..] waiting for ACC shared memory ...", flush=True)
    while not game_running():
        time.sleep(0.5)
    print("[OK] ACC detected", flush=True)


# --------------------------------------------------------------------- 坐标
def player_world_xz(graphics) -> tuple[float | None, float | None]:
    """玩家车在赛道平面上的坐标。ACC 的 y 是高度，平面是 x-z。"""
    try:
        player_id = int(graphics.playerCarID)
        if player_id < 0:
            return None, None
        index = -1
        for i in range(ACCConstants.MAX_MAPPED_VEHICLES):
            if int(graphics.carIDs[i]) == player_id:
                index = i
                break
        if index < 0:
            return None, None
        coord = graphics.carCoordinates[index]
        x, z = float(coord.x), float(coord.z)
        if x == 0.0 and z == 0.0:
            return None, None
        return x, z
    except Exception:
        return None, None


# --------------------------------------------------------------------- 处理
def sample_list(points: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """等弧长重采样闭合折线到 n 个点。"""
    if len(points) < 3:
        return []
    cumulative = [0.0]
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        cumulative.append(cumulative[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cumulative[-1]
    if total <= 1e-6:
        return []
    out: list[tuple[float, float]] = []
    j = 0
    for i in range(n):
        target = total * i / n
        while j < len(cumulative) - 2 and cumulative[j + 1] < target:
            j += 1
        span = (cumulative[j + 1] - cumulative[j]) or 1.0
        t = (target - cumulative[j]) / span
        a = points[j % len(points)]
        b = points[(j + 1) % len(points)]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def smooth_values(values: list[float], window: int, closed: bool = True) -> list[float]:
    if window <= 0:
        return list(values)
    n = len(values)
    out = []
    for i in range(n):
        if closed:
            indices = [(i + k) % n for k in range(-window, window + 1)]
        else:
            indices = list(range(max(0, i - window), min(n - 1, i + window) + 1))
        chunk = [values[j] for j in indices]
        out.append(sum(chunk) / len(chunk))
    return out


def smooth_path(points: list[tuple[float, float]], window: int) -> list[tuple[float, float]]:
    if window <= 0 or len(points) < 3:
        return list(points)
    xs = smooth_values([p[0] for p in points], window, closed=True)
    zs = smooth_values([p[1] for p in points], window, closed=True)
    return list(zip(xs, zs))


def curvature(points: list[tuple[float, float]]) -> list[float]:
    """Menger 曲率（1/m），用于识别弯心。"""
    n = len(points)
    out = []
    for i in range(n):
        a = points[(i - 1) % n]
        b = points[i]
        c = points[(i + 1) % n]
        ab = math.hypot(b[0] - a[0], b[1] - a[1])
        bc = math.hypot(c[0] - b[0], c[1] - b[1])
        ca = math.hypot(c[0] - a[0], c[1] - a[1])
        area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
        denom = ab * bc * ca
        out.append(0.0 if denom < 1e-9 else 4 * area / denom)
    return out


def detect_corners(
    world_pts: list[tuple[float, float]],
    ks: list[float],
    min_sep_m: float = 60.0,
) -> list[dict]:
    n = len(ks)
    if n < 5:
        return []
    positive = sorted(k for k in ks if k > 0)
    if not positive:
        return []
    ref = positive[int(len(positive) * 0.85)]
    threshold = max(0.003, ref * 0.55)
    candidates = [
        i
        for i in range(n)
        if ks[i] > threshold and ks[i] >= ks[(i - 1) % n] and ks[i] >= ks[(i + 1) % n]
    ]
    # 沿弧长做非极大值抑制
    total = sum(
        math.hypot(
            world_pts[(i + 1) % n][0] - world_pts[i][0],
            world_pts[(i + 1) % n][1] - world_pts[i][1],
        )
        for i in range(n)
    )
    ds = total / n if n else 1.0
    min_gap = max(1, int(min_sep_m / max(ds, 1e-6)))
    picked: list[int] = []
    for i in sorted(candidates, key=lambda idx: ks[idx], reverse=True):
        if all(min(abs(i - j), n - abs(i - j)) >= min_gap for j in picked):
            picked.append(i)
    return [
        {
            "p": round(i / n, 5),
            "index": i,
            "radiusM": round(1 / ks[i], 1) if ks[i] > 0 else None,
        }
        for i in sorted(picked)
    ]


def normalize_path(
    world_pts: list[tuple[float, float]],
    flip_x: bool,
    flip_y: bool,
) -> list[list[float]]:
    xs = [p[0] for p in world_pts]
    zs = [p[1] for p in world_pts]
    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    span_x = max(max_x - min_x, 1e-6)
    span_z = max(max_z - min_z, 1e-6)
    scale = min((VIEW_W - 2 * PADDING) / span_x, (VIEW_H - 2 * PADDING) / span_z)
    ox = (VIEW_W - span_x * scale) / 2
    oy = (VIEW_H - span_z * scale) / 2

    out = []
    for x, z in world_pts:
        sx = ox + ((max_x - x) if flip_x else (x - min_x)) * scale
        sz = oy + ((max_z - z) if flip_y else (z - min_z)) * scale
        out.append([round(sx, 2), round(sz, 2)])
    return out


def write_preview(
    path: Path,
    title: str,
    screen: list[list[float]],
    corners: list[dict],
    meta: dict,
) -> None:
    if not screen:
        return
    d = "M" + "L".join(f"{x},{y}" for x, y in screen) + "Z"
    marks = []
    for idx, corner in enumerate(corners, start=1):
        x, y = screen[corner["index"] % len(screen)]
        marks.append(
            f'<circle cx="{x}" cy="{y}" r="3.2" fill="#ff5a5f"/>'
            f'<text x="{x + 6}" y="{y - 5}" font-size="10" fill="#ff9a9d">{idx}</text>'
        )
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body{{background:#0b0e14;color:#e8edf5;font-family:Segoe UI,sans-serif;margin:24px}}
  svg{{background:#11161f;border:1px solid #232b35;border-radius:10px;width:960px;max-width:100%}}
  .meta{{font-family:Consolas,monospace;font-size:12px;color:#96a2b1;line-height:1.9}}
</style>
<h2>{title}</h2>
<svg viewBox="0 0 480 290">
  <path d="{d}" fill="none" stroke="#c8f53e" stroke-width="1.2" stroke-linejoin="round"/>
  {''.join(marks)}
</svg>
<div class="meta">
  {'<br>'.join(f'{k}: {v}' for k, v in meta.items())}<br>
  折线点数: {len(screen)} · 识别弯道: {len(corners)}
</div>
"""
    path.write_text(html, encoding="utf-8")


def process_lap(
    samples: list[dict],
    meta: dict,
    out_dir: Path,
    stem: str,
    args,
) -> None:
    pts = [
        (s["p"], s["wx"], s["wz"])
        for s in samples
        if s.get("wx") is not None and s.get("wz") is not None
    ]
    if len(pts) < args.min_samples:
        print(f"  skip: only {len(pts)} valid coordinate samples", flush=True)
        return
    pts.sort(key=lambda item: item[0])
    if pts[0][0] > 0.05 or pts[-1][0] < 0.95:
        print(f"  skip: incomplete lap coverage {pts[0][0]:.3f}..{pts[-1][0]:.3f}", flush=True)
        return

    world = [(x, z) for _, x, z in pts]
    world = smooth_path(world, args.smooth)
    resampled = sample_list(world, args.points)
    if len(resampled) < 32:
        print("  skip: degenerate path", flush=True)
        return
    screen = normalize_path(resampled, args.flip_x, args.flip_y)
    ks = smooth_values(curvature(resampled), 2, closed=True)
    corners = detect_corners(resampled, ks)

    meta = dict(meta)
    meta["splinePoints"] = len(screen)
    meta["corners"] = len(corners)
    meta["flipX"] = args.flip_x
    meta["flipY"] = args.flip_y

    payload = {
        "version": 1,
        "trackId": meta.get("track", "unknown"),
        "name": meta.get("track", "unknown"),
        "driver": "",
        "car": meta.get("car", ""),
        "lapMs": meta.get("lapMs"),
        "spline": screen,
        "corners": [
            {"p": c["p"], "radiusM": c["radiusM"]} for c in corners
        ],
        "meta": meta,
    }
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_preview(out_dir / f"{stem}_preview.html", stem, screen, corners, meta)
    print(f"  saved {json_path.name}  ({len(screen)} pts, {len(corners)} corners)", flush=True)


# --------------------------------------------------------------------- 主循环
def main() -> int:
    parser = argparse.ArgumentParser(description="ACC 赛道数据采集")
    parser.add_argument("--hz", type=float, default=100, help="采样频率，默认 100Hz")
    parser.add_argument("--points", type=int, default=600, help="输出折线点数，默认 600")
    parser.add_argument("--smooth", type=int, default=2, help="世界坐标平滑窗口，默认 2（越小越锐利）")
    parser.add_argument("--min-samples", type=int, default=200, help="一圈最少有效采样点")
    parser.add_argument("--out", type=str, default="", help="输出目录，默认 tools/track_data")
    parser.add_argument("--flip-x", action="store_true", help="水平镜像")
    parser.add_argument("--no-flip-y", action="store_true", help="不垂直翻转（默认翻转）")
    args = parser.parse_args()
    args.flip_y = not args.no_flip_y

    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent / "track_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / max(10.0, min(250.0, args.hz))

    wait_for_game()

    phys = MMapControl(ACCConstants.MM_PHYSICS_FILE_NAME, ACCPhysics)
    gfx = MMapControl(ACCConstants.MM_GRAPHICS_FILE_NAME, ACCGraphics)
    static = MMapControl(ACCConstants.MM_STATIC_FILE_NAME, ACCStatic)
    phys.create(access_mode=0)
    gfx.create(access_mode=0)
    static.create(access_mode=0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    track = "unknown"
    car = ""
    try:
        static.update()
        track = str(static.data.trackName).strip() or "unknown"
        car = str(static.data.carModel).strip()
    except Exception:
        pass
    track_safe = re.sub(r"[^A-Za-z0-9_-]+", "_", track) or "unknown"

    csv_path = out_dir / f"{track_safe}_{stamp}_raw.csv"
    csv_file = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        csv_file,
        fieldnames=[
            "t", "lap", "completed", "p", "wx", "wz", "speed", "throttle",
            "brake", "steer", "rpm", "gear", "heading", "lapMs",
        ],
    )
    writer.writeheader()

    samples: list[dict] = []
    lap_counter = 0
    last_completed = -1
    last_p: float | None = None
    last_lap_ms = 0
    last_boundary = time.monotonic()
    live_warned = False

    print(f"[..] recording -> {csv_path.name}  ({args.hz:.0f} Hz)", flush=True)
    print("     drive a lap; each crossing of the start line saves one lap", flush=True)
    try:
        while True:
            phys.update()
            gfx.update()
            p = phys.data
            g = gfx.data
            if int(g.status) != 2 or int(g.activeCars) == 0:
                if not live_warned:
                    print("[..] waiting for a live track session ...", flush=True)
                    live_warned = True
                time.sleep(0.2)
                continue
            live_warned = False

            progress = float(g.normalizedCarPosition)
            lap_ms = int(g.iCurrentTime)
            completed = int(g.completedLaps)
            wx, wz = player_world_xz(g)
            row = {
                "t": int(time.time() * 1000),
                "lap": lap_counter,
                "completed": completed,
                "p": round(progress, 6),
                "wx": "" if wx is None else round(wx, 3),
                "wz": "" if wz is None else round(wz, 3),
                "speed": round(float(p.speedKmh), 2),
                "throttle": round(float(p.throttle), 4),
                "brake": round(float(p.brake), 4),
                "steer": round(float(p.steerAngle), 4),
                "rpm": int(p.rpm),
                "gear": int(p.gear),
                "heading": round(float(p.heading), 4),
                "lapMs": lap_ms,
            }
            writer.writerow(row)
            csv_file.flush()

            if last_completed >= 0 and completed < last_completed:
                # 回到维修区/重开 session：丢掉半圈，重新开始
                print("[..] session reset, discarding partial lap", flush=True)
                samples = []
                last_p = None
                last_lap_ms = 0
                last_completed = completed
                last_boundary = time.monotonic()
                continue

            boundary = completed > last_completed or (
                last_p is not None
                and last_p > 0.90
                and progress < 0.10
                and last_lap_ms > 5_000
                and (time.monotonic() - last_boundary) > 2.0
            )
            if boundary:
                total_ms = int(g.iLastTime) or last_lap_ms
                if samples and len(samples) >= args.min_samples and total_ms > 0:
                    lap_counter += 1
                    meta = {
                        "track": track,
                        "car": car,
                        "lap": lap_counter,
                        "completedLaps": completed,
                        "lapMs": total_ms,
                        "samples": len(samples),
                        "recordedAt": datetime.now().isoformat(timespec="seconds"),
                    }
                    print(f"[LAP {lap_counter}] {total_ms} ms, {len(samples)} samples", flush=True)
                    process_lap(
                        samples, meta, out_dir,
                        f"{track_safe}_{stamp}_lap{lap_counter:02d}", args,
                    )
                samples = []
                last_completed = completed
                last_boundary = time.monotonic()

            samples.append(row)
            last_p = progress
            last_lap_ms = lap_ms
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[OK] stopped", flush=True)
    finally:
        csv_file.close()
        for control in (static, gfx, phys):
            try:
                control.close()
            except Exception:
                pass
    print(f"[OK] raw data: {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
