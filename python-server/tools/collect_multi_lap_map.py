"""
多圈赛道地图采集脚本（自动合并 + 导出地图文件）

流程：
  1. 高频读取 ACC 共享内存，记录每圈的玩家世界坐标 (x, z)
  2. 过滤掉不完整/出场圈/异常圈
  3. 跑满指定圈数（默认 5 圈）后，按圈进度 p 对齐所有圈，逐点取中位数
  4. 自动导出：
       <track>_<时间>_map.json          ← 发给后端填入数据库
       <track>_<时间>_map_preview.html  ← 浏览器预览
       <track>_<时间>_raw.csv           ← 原始采样（可选保留）

用法：
  cd python-server
  python tools/collect_multi_lap_map.py              # 5 圈，1200 点
  python tools/collect_multi_lap_map.py --laps 3     # 3 圈后自动停止
  python tools/collect_multi_lap_map.py --hz 200 --points 1600 --smooth 1

  中途 Ctrl+C 也会用已跑完的圈生成地图（至少 1 圈）。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_track as base  # noqa: E402  复用坐标/平滑/预览等工具


def evaluate_lap(samples: list[dict], total_ms: int, args) -> dict | None:
    """判断这一圈是否可用于合并。"""
    coords = [s for s in samples if s.get("wx") is not None]
    if len(coords) < args.min_samples:
        return None
    ps = [s["p"] for s in coords]
    if min(ps) > 0.05 or max(ps) < 0.95:
        return None
    if not (15_000 <= total_ms <= 1_200_000):
        return None
    valid_frac = sum(1 for s in samples if s["valid"]) / max(1, len(samples))
    pit_frac = sum(1 for s in samples if s["pit"]) / max(1, len(samples))
    if valid_frac < 0.5 or pit_frac > 0.2:
        return None
    return {
        "samples": samples,
        "ms": total_ms,
        "validFrac": round(valid_frac, 3),
        "pitFrac": round(pit_frac, 3),
    }


def resample_by_progress(samples: list[dict], grid: int) -> tuple[list[float], list[float]]:
    pts = sorted(
        (float(s["p"]), float(s["wx"]), float(s["wz"]))
        for s in samples
        if s.get("wx") is not None and s.get("wz") is not None
    )
    if len(pts) < 2:
        return [], []
    xs: list[float] = []
    zs: list[float] = []
    j = 0
    for i in range(grid):
        p = (i + 0.5) / grid
        while j + 1 < len(pts) and pts[j + 1][0] < p:
            j += 1
        p0, x0, z0 = pts[j]
        p1, x1, z1 = pts[min(j + 1, len(pts) - 1)]
        span = (p1 - p0) or 1.0
        t = max(0.0, min(1.0, (p - p0) / span))
        xs.append(x0 + (x1 - x0) * t)
        zs.append(z0 + (z1 - z0) * t)
    return xs, zs


def _median_path(series: list[tuple[list[float], list[float]]], grid: int):
    mx = [0.0] * grid
    mz = [0.0] * grid
    for k in range(grid):
        mx[k] = statistics.median(s[0][k] for s in series)
        mz[k] = statistics.median(s[1][k] for s in series)
    return mx, mz


def _rms(series: tuple[list[float], list[float]], mx, mz, grid: int) -> float:
    total = 0.0
    for k in range(grid):
        dx = series[0][k] - mx[k]
        dz = series[1][k] - mz[k]
        total += dx * dx + dz * dz
    return math.sqrt(total / max(1, grid))


def merge_laps(laps: list[dict], args):
    grid = max(200, args.grid)
    pairs = [(resample_by_progress(lap["samples"], grid), lap) for lap in laps]
    pairs = [(series, lap) for series, lap in pairs if series[0]]
    if not pairs:
        return None
    series = [item[0] for item in pairs]
    used_laps = [item[1] for item in pairs]

    mx, mz = _median_path(series, grid)
    # 剔除整体偏离中位数过大的圈（例如某一圈明显切弯/跑偏）
    deviations = [_rms(s, mx, mz, grid) for s in series]
    keep = [
        (s, dev, lap)
        for s, dev, lap in zip(series, deviations, used_laps)
        if dev <= args.max_lap_deviation
    ]
    if keep and len(keep) < len(series):
        series = [item[0] for item in keep]
        mx, mz = _median_path(series, grid)

    mx = base.smooth_values(mx, args.smooth, closed=True)
    mz = base.smooth_values(mz, args.smooth, closed=True)
    world = list(zip(mx, mz))
    resampled = base.sample_list(world, args.points)
    if len(resampled) < 32:
        return None
    screen = base.normalize_path(resampled, args.flip_x, args.flip_y)
    ks = base.smooth_values(base.curvature(resampled), 2, closed=True)
    corners = base.detect_corners(resampled, ks, min_sep_m=args.corner_sep)

    kept_laps = keep if keep else [
        (s, dev, lap) for s, dev, lap in zip(series, deviations, laps)
    ]
    return {
        "screen": screen,
        "corners": corners,
        "lapsUsed": len(series),
        "bestMs": min(lap["ms"] for _, _, lap in kept_laps),
        "deviations": [round(dev, 2) for _, dev, _ in kept_laps],
        "lapMeta": [
            {
                "lapMs": lap["ms"],
                "samples": len(lap["samples"]),
                "validFrac": lap["validFrac"],
                "pitFrac": lap["pitFrac"],
                "rmsDeviationM": round(dev, 2),
            }
            for _, dev, lap in kept_laps
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ACC 多圈采集并合并赛道地图")
    parser.add_argument("--laps", type=int, default=5, help="跑满多少圈后自动导出")
    parser.add_argument("--hz", type=float, default=120, help="采样频率")
    parser.add_argument("--grid", type=int, default=1200, help="按进度对齐的网格数")
    parser.add_argument("--points", type=int, default=1200, help="导出的折线点数")
    parser.add_argument("--smooth", type=int, default=1, help="合并后的平滑窗口")
    parser.add_argument("--corner-sep", type=float, default=80, help="弯心最小间距（米）")
    parser.add_argument("--max-lap-deviation", type=float, default=15, help="单圈偏离中位数的 RMS 上限（米）")
    parser.add_argument("--min-samples", type=int, default=200, help="一圈最少有效坐标采样点")
    parser.add_argument("--out", type=str, default="", help="输出目录，默认 tools/track_data")
    parser.add_argument("--flip-x", action="store_true", help="水平镜像")
    parser.add_argument("--no-flip-y", action="store_true", help="不垂直翻转")
    args = parser.parse_args()
    args.flip_y = not args.no_flip_y

    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent / "track_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / max(10.0, min(250.0, args.hz))

    base.wait_for_game()

    phys = base.MMapControl(base.ACCConstants.MM_PHYSICS_FILE_NAME, base.ACCPhysics)
    gfx = base.MMapControl(base.ACCConstants.MM_GRAPHICS_FILE_NAME, base.ACCGraphics)
    static = base.MMapControl(base.ACCConstants.MM_STATIC_FILE_NAME, base.ACCStatic)
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
            "t", "completed", "p", "wx", "wz", "speed", "throttle", "brake",
            "steer", "rpm", "gear", "heading", "lapMs", "valid", "pit",
        ],
    )
    writer.writeheader()

    laps: list[dict] = []
    current: list[dict] = []
    last_completed = -1
    last_p: float | None = None
    last_lap_ms = 0
    last_boundary = time.monotonic()
    live_warned = False

    print(f"[..] recording {args.laps} laps at {args.hz:.0f} Hz -> {csv_path.name}", flush=True)
    print("     Ctrl+C early will still export the laps collected so far", flush=True)

    try:
        while len(laps) < args.laps:
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
            wx, wz = base.player_world_xz(g)
            row = {
                "t": int(time.time() * 1000),
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
                "valid": int(g.isValidLap),
                "pit": int(g.isInPitLane),
            }
            writer.writerow(row)
            csv_file.flush()

            if last_completed >= 0 and completed < last_completed:
                print("[..] session reset, discarding partial lap", flush=True)
                current = []
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
                lap = evaluate_lap(current, total_ms, args) if current else None
                if lap is not None:
                    laps.append(lap)
                    print(
                        f"[LAP {len(laps)}/{args.laps}] {total_ms} ms · "
                        f"{len(lap['samples'])} samples · valid {lap['validFrac']*100:.0f}%"
                        f" · pit {lap['pitFrac']*100:.0f}%",
                        flush=True,
                    )
                elif current:
                    print(
                        f"[..] skip lap: {total_ms} ms · {len(current)} samples "
                        f"(incomplete / invalid / pit lap)",
                        flush=True,
                    )
                current = []
                last_completed = completed
                last_boundary = time.monotonic()

            current.append(row)
            last_p = progress
            last_lap_ms = lap_ms
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[..] interrupted, merging collected laps ...", flush=True)
    finally:
        csv_file.close()
        for control in (static, gfx, phys):
            try:
                control.close()
            except Exception:
                pass

    if not laps:
        print("[!] no complete laps collected, nothing exported", flush=True)
        return 1

    merged = merge_laps(laps, args)
    if merged is None:
        print("[!] merge failed (insufficient/degenerate data)", flush=True)
        return 1

    meta = {
        "track": track,
        "car": car,
        "laps": merged["lapsUsed"],
        "bestLapMs": merged["bestMs"],
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "source": "multi-lap-median",
        "lapMeta": merged["lapMeta"],
        "flipX": args.flip_x,
        "flipY": args.flip_y,
        "splinePoints": len(merged["screen"]),
        "corners": len(merged["corners"]),
    }
    payload = {
        "version": 1,
        "trackId": track,
        "name": track,
        "car": car,
        "laps": merged["lapsUsed"],
        "lapMs": merged["bestMs"],
        "spline": merged["screen"],
        "corners": [
            {"p": c["p"], "radiusM": c["radiusM"]} for c in merged["corners"]
        ],
        "flipX": args.flip_x,
        "flipY": args.flip_y,
        "meta": meta,
    }

    map_path = out_dir / f"{track_safe}_{stamp}_map.json"
    map_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_path = out_dir / f"{track_safe}_{stamp}_map_preview.html"
    base.write_preview(preview_path, f"{track_safe} · {merged['lapsUsed']} laps", merged["screen"], merged["corners"], meta)

    print("\n[OK] merged map exported", flush=True)
    print(f"     map:     {map_path}", flush=True)
    print(f"     preview: {preview_path}", flush=True)
    print(f"     raw:     {csv_path}", flush=True)
    print(f"     laps used: {merged['lapsUsed']} · best {merged['bestMs']} ms · "
          f"{len(merged['screen'])} points", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
