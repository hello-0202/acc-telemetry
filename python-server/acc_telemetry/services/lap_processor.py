"""圈次状态机：识别圈边界、有效性、分段，并把采样按进度降采样。

纯内存、无 I/O，便于用合成 Snapshot 做单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import MAX_LAP_MS, MIN_LAP_MS, SAMPLES_PER_LAP
from ..providers.base import Snapshot

SAMPLE_FIELDS = (
    "speed", "rpm", "gear", "throttle", "brake", "steer",
    "fl_p", "fl_t", "fl_bt", "fr_p", "fr_t", "fr_bt",
    "rl_p", "rl_t", "rl_bt", "rr_p", "rr_t", "rr_bt",
    "t_ms", "g_long", "g_lat", "wx", "wy",
)
REQUIRED_FIELDS = ("speed", "rpm", "gear", "throttle", "brake", "steer")


@dataclass
class LapResult:
    no: int
    ms: int
    kind: str  # ok / invalid / out
    valid: bool
    s1: int | None
    s2: int | None
    s3: int | None
    samples: list[dict[str, Any]]
    started_partial: bool


def _valid_ms(value: int | None) -> int | None:
    """单圈时间合法性（用于过滤哨兵值）。"""
    if value is None:
        return None
    value = int(value)
    return value if MIN_LAP_MS <= value <= MAX_LAP_MS else None


def _valid_sector_ms(value: int | None) -> int | None:
    """分段用时只需大于 0 且不超过单圈上限。"""
    if value is None:
        return None
    value = int(value)
    return value if 0 < value <= MAX_LAP_MS else None


class LapProcessor:
    def __init__(self, bins: int = SAMPLES_PER_LAP) -> None:
        self.bins = max(1, int(bins))
        self._active = False
        self._no = 1
        self._started_partial = False
        self._started_in_pit = False
        self._invalid_latched = False
        self._pit_samples = 0
        self._total_samples = 0
        self._samples: list[dict[str, Any]] = []
        self._sector_times: list[int] = []
        self._last_sector_index = 0
        self._last_completed_laps = 0
        self._last_lap_time_ms = 0
        self._last_progress = 0.0
        self._max_progress = 0.0
        self._last_boundary_mono = 0.0
        self._reset_detected = False

    # ------------------------------------------------------------------ 状态
    @property
    def active(self) -> bool:
        return self._active

    @property
    def lap_no(self) -> int:
        return self._no

    @property
    def invalid_latched(self) -> bool:
        return self._invalid_latched

    def consume_reset(self) -> bool:
        """读取并清除「session 已重置」标记（由 Service 决定是否新建 session）。"""
        flag = self._reset_detected
        self._reset_detected = False
        return flag

    def begin(self, snap: Snapshot) -> None:
        self._active = True
        self._no = int(snap.completed_laps) + 1
        # 仅记录「采样不完整」这一事实；出场圈判定只看维修区，不看这里
        self._started_partial = snap.lap_time_ms > 3_000 or snap.progress > 0.02
        self._started_in_pit = snap.in_pit_lane or snap.in_pit
        self._invalid_latched = not snap.valid_lap
        self._reset_accumulator(snap)
        self._append_sample(snap)

    def stop(self) -> None:
        self._active = False
        self._samples = []

    # ------------------------------------------------------------------ 主循环
    def consume(self, snap: Snapshot) -> list[LapResult]:
        if not self._active:
            self.begin(snap)
            return []

        results: list[LapResult] = []

        # 1) 圈计数器回退 / 起步区计时器重置 → 新 session
        reset = snap.completed_laps < self._last_completed_laps
        if not reset:
            reset = (
                snap.lap_time_ms + 5_000 < self._last_lap_time_ms
                and snap.progress < 0.1
                and self._last_progress < 0.1
            )
        if reset:
            # 圈号回退 = 新 session。正在跑的半圈直接丢弃，不能当成完整圈落库。
            self._reset_detected = True
            self._no = int(snap.completed_laps) + 1
            self._started_partial = False
            self._started_in_pit = snap.in_pit_lane or snap.in_pit
            self._invalid_latched = not snap.valid_lap
            self._reset_accumulator(snap)
            self._append_sample(snap)
            return results

        # 2) 圈边界。ACC 的 completedLaps 有时会滞后（尤其出场圈不计圈），
        #    所以同时用「进度回绕」和「圈计时器归零」兜底。
        counter_boundary = snap.completed_laps > self._last_completed_laps
        wrap_boundary = (
            self._max_progress > 0.90
            and snap.progress < 0.20
            and max(self._last_lap_time_ms, snap.lap_time_ms) > 5_000
            and (snap.t_mono - self._last_boundary_mono) > 2.0
        )
        if counter_boundary or wrap_boundary:
            result = self._finalize(snap)
            if result is not None:
                results.append(result)
            self._no = max(self._no + 1, int(snap.completed_laps) + 1)
            self._started_partial = False
            self._started_in_pit = snap.in_pit_lane or snap.in_pit
            self._invalid_latched = not snap.valid_lap
            self._reset_accumulator(snap)
            self._append_sample(snap)
            return results

        # 计时器大幅归零：如果本轮已经跑过大半圈，视为过线（出场圈有时不增加
        # completedLaps）；否则多半是回到维修区/重开，丢弃当前圈。
        time_reset = (
            self._last_lap_time_ms > 20_000
            and snap.lap_time_ms + 10_000 < self._last_lap_time_ms
            and snap.progress < 0.5
        )
        if time_reset:
            if self._max_progress > 0.90:
                result = self._finalize(snap)
                if result is not None:
                    results.append(result)
                self._no = max(self._no + 1, int(snap.completed_laps) + 1)
                self._started_partial = False
                self._started_in_pit = snap.in_pit_lane or snap.in_pit
                self._invalid_latched = not snap.valid_lap
            else:
                self._reset_detected = True
                self._no = int(snap.completed_laps) + 1
                self._started_partial = False
                self._started_in_pit = snap.in_pit_lane or snap.in_pit
                self._invalid_latched = not snap.valid_lap
            self._reset_accumulator(snap)
            self._append_sample(snap)
            return results

        # 3) 正常帧：记录分段、有效性与采样
        if snap.sector_index != self._last_sector_index:
            sector_ms = _valid_sector_ms(snap.last_sector_ms)
            # ACC 的 lastSectorTime 实际是「到达该分段时的累计时间」，
            # 只保留严格递增的累计点，避免重复/回退值污染分段计算。
            if sector_ms is not None and (
                not self._sector_times or sector_ms > self._sector_times[-1]
            ):
                self._sector_times.append(sector_ms)
            self._last_sector_index = snap.sector_index

        if not snap.valid_lap:
            self._invalid_latched = True

        self._append_sample(snap)
        self._last_lap_time_ms = snap.lap_time_ms
        self._last_progress = snap.progress
        self._max_progress = max(self._max_progress, snap.progress)
        self._last_completed_laps = snap.completed_laps
        return results

    # ------------------------------------------------------------------ 内部
    def _reset_accumulator(self, snap: Snapshot) -> None:
        self._samples = []
        self._sector_times = []
        self._pit_samples = 0
        self._total_samples = 0
        self._last_sector_index = snap.sector_index
        self._last_completed_laps = snap.completed_laps
        self._last_lap_time_ms = snap.lap_time_ms
        self._last_progress = snap.progress
        self._max_progress = snap.progress
        self._last_boundary_mono = snap.t_mono

    def _append_sample(self, snap: Snapshot) -> None:
        p = snap.progress
        if p != p:  # NaN
            return
        p = min(0.999999, max(0.0, float(p)))
        pressure = snap.wheel_pressure
        temp = snap.tyre_core_temp
        brake_temp = snap.brake_temp
        self._samples.append(
            {
                "p": p,
                "speed": float(snap.speed_kmh),
                "rpm": int(snap.rpm),
                "gear": int(snap.gear),
                "throttle": float(snap.throttle),
                "brake": float(snap.brake),
                "steer": float(snap.steer),
                "fl_p": pressure[0], "fl_t": temp[0], "fl_bt": brake_temp[0],
                "fr_p": pressure[1], "fr_t": temp[1], "fr_bt": brake_temp[1],
                "rl_p": pressure[2], "rl_t": temp[2], "rl_bt": brake_temp[2],
                "rr_p": pressure[3], "rr_t": temp[3], "rr_bt": brake_temp[3],
                "t_ms": float(snap.lap_time_ms),
                "g_long": snap.g_long,
                "g_lat": snap.g_lat,
                # 世界坐标不落库，只用于跑完一圈后自动生成赛道地图
                "wx": snap.world_x,
                "wy": snap.world_y,
            }
        )
        self._total_samples += 1
        if snap.in_pit_lane or snap.in_pit:
            self._pit_samples += 1

    def _finalize(self, snap: Snapshot) -> LapResult | None:
        ms = _valid_ms(snap.last_lap_ms) or _valid_ms(self._last_lap_time_ms)
        if ms is None:
            return None
        # 采样过少说明是误判的边界（历史数据里出现过只有 1 个点的“圈”）
        if len(self._samples) < 10:
            return None

        # out 只表示「从维修区出发」的圈；监测中途开始导致的采样不完整不算出场圈。
        if self._started_in_pit or (
            self._total_samples and self._pit_samples / self._total_samples > 0.5
        ):
            kind = "out"
        elif self._invalid_latched:
            kind = "invalid"
        else:
            kind = "ok"

        s1, s2, s3 = self._sector_times_for(ms)
        return LapResult(
            no=self._no,
            ms=ms,
            kind=kind,
            valid=(kind == "ok"),
            s1=s1,
            s2=s2,
            s3=s3,
            samples=self._downsample(),
            started_partial=self._started_partial,
        )

    def _sector_times_for(self, ms: int) -> tuple[int | None, int | None, int | None]:
        # 优先按进度 1/3、2/3 对齐——和历史图表中的分段线完全一致。
        progress_based = self._sector_times_from_samples(ms)
        if progress_based is not None:
            return progress_based

        # 回退：ACC 的 lastSectorTime 是「到达该分段时的累计时间」，不是本段耗时。
        points = [t for t in self._sector_times if 0 < t < ms]
        if len(points) >= 2 and points[1] > points[0]:
            c1, c2 = points[0], points[1]
            s1 = int(round(c1))
            s2 = int(round(c2 - c1))
            s3 = int(ms - round(c2))
            # 任一分段短于 1 秒说明累计点异常，宁可不显示也不写错数据
            if min(s1, s2, s3) >= 1_000:
                return s1, s2, s3
        return None, None, None

    def _sector_times_from_samples(
        self, ms: int
    ) -> tuple[int, int, int] | None:
        t1: float | None = None
        t2: float | None = None
        for sample in self._samples:
            t = sample.get("t_ms")
            if t is None:
                continue
            t = float(t)
            if t1 is None and sample["p"] >= 1 / 3:
                t1 = t
            if t2 is None and sample["p"] >= 2 / 3:
                t2 = t
            if t1 is not None and t2 is not None:
                break
        if t1 is None or t2 is None or not (0 < t1 < t2 < ms):
            return None
        s1 = int(round(t1))
        s2 = int(round(t2 - t1))
        s3 = int(round(ms - t2))
        if min(s1, s2, s3) <= 0:
            return None
        return s1, s2, s3

    def _downsample(self) -> list[dict[str, Any]]:
        if not self._samples:
            return []
        bins: list[list[dict[str, Any]]] = [[] for _ in range(self.bins)]
        for sample in self._samples:
            index = min(self.bins - 1, int(float(sample["p"]) * self.bins))
            bins[index].append(sample)

        known = [i for i, bucket in enumerate(bins) if bucket]
        if not known:
            return []

        series: dict[str, list[float | None]] = {}
        for name in SAMPLE_FIELDS:
            values: list[float | None] = [None] * self.bins
            for i, bucket in enumerate(bins):
                if not bucket:
                    continue
                nums = [float(s[name]) for s in bucket if s.get(name) is not None]
                if nums:
                    values[i] = sum(nums) / len(nums)
            series[name] = values

        # 只在已知桶之间做线性插值；首尾空桶（不完整圈）直接裁掉
        for values in series.values():
            previous = None
            for i in range(self.bins):
                if values[i] is None:
                    continue
                if previous is not None and i - previous > 1:
                    start, end = values[previous], values[i]  # type: ignore[assignment]
                    for k in range(previous + 1, i):
                        values[k] = start + (end - start) * (k - previous) / (i - previous)
                previous = i

        first, last = known[0], known[-1]
        out: list[dict[str, Any]] = []
        for i in range(first, last + 1):
            row: dict[str, Any] = {
                "idx": len(out),
                "p": round((i + 0.5) / self.bins, 6),
            }
            for name in SAMPLE_FIELDS:
                value = series[name][i]
                if name in ("rpm", "gear"):
                    row[name] = int(round(value)) if value is not None else 0
                else:
                    row[name] = round(value, 4) if value is not None else None
            for required in REQUIRED_FIELDS:
                if row.get(required) is None:
                    row[required] = 0
            out.append(row)
        return out
