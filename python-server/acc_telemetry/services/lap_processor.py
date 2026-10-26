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
    # 记录完成该圈时 ACC 的会话类型（练习 / 排位 / 正赛），用于历史列表二次分类
    session_type: str = "unknown"


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
        # 本圈已见到的最大圈计时。ACC 过线前会先发一帧「计时已归零、进度还是
        # 1.0」的残留帧，它会把 _last_lap_time_ms 覆盖成 ≈0；若再过线时
        # completedLaps 没有前进（出场圈不计圈），回绕判定就会因为
        # `max(_last_lap_time_ms, lap_time) > 5s` 不成立而漏掉这次过线，
        # 直到 5 秒后才补判——L1 开头约 5 秒的走线因此丢失。
        self._max_lap_time_ms = 0
        self._reset_detected = False
        # 第一圈 priming：开始监测后第一帧可能已经在圈中（app 启动 / 点击开始的
        # 延迟里车又跑了约 5 秒）。此刻直接 begin 会把半圈当成第一圈、丢掉开头
        # 一段轨迹。于是先进入 priming，只跟踪进度与圈计数，等第一次过线再从
        # t≈0 起第一圈；只有在起跑线附近（t≲3s 且 p≲0.02）才直接 begin，
        # 以免把正常「从出场圈起点开始记录」也误判成半圈。
        self._priming = True
        self._priming_base_laps = -1
        # 上一圈落库时的圈速 / 圈计数器，用于挡掉「同一圈被判定两次」的重复记录
        self._last_finalized_ms: int | None = None
        self._last_finalized_laps = -1

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
            # 第一圈 priming：开始监测后第一帧往往已在圈中（app 启动 / 点击开始的
            # 延迟里车又跑了约 5 秒）。此刻直接 begin 会把半圈当第一圈、丢掉开头
            # 一段轨迹。于是先进入 priming，只跟踪进度与圈计数，等第一次过线
            # （completedLaps 增加或进度回绕）再从 t≈0 起第一圈；但若第一帧就在
            # 起跑线附近（t≲3s 且 p≲0.02），说明是从出场圈起点开始记录，直接 begin、
            # 正常保留出场圈，不丢轨迹。
            if self._priming:
                if self._priming_base_laps < 0:
                    self._priming_base_laps = snap.completed_laps
                    self._max_progress = 0.0
                    self._last_progress = snap.progress
                    self._last_lap_time_ms = snap.lap_time_ms

                prev_t = self._last_lap_time_ms
                # 「就在起跑线附近」每帧都判：ACC 过线后常常先加 completedLaps
                # /把圈计时归零，下一帧才把 normalizedCarPosition 绕回 0。
                # 只判第一帧的话，回放首帧 p≈0.999 就会漏掉这个起点，
                # 一直卡到下一圈，白丢一整圈。
                at_lap_start = snap.lap_time_ms <= 3_000 and snap.progress <= 0.02
                counter_boundary = snap.completed_laps > self._priming_base_laps
                wrap_boundary = (
                    self._max_progress > 0.90
                    and snap.progress < 0.20
                    and (snap.t_mono - self._last_boundary_mono) > 2.0
                )
                # 圈计时大幅归零（出场圈 / 重开计时器）：同上，也是圈起点
                timer_reset = (
                    prev_t > 10_000
                    and snap.lap_time_ms + 5_000 < prev_t
                    and snap.progress < 0.20
                )
                if at_lap_start or counter_boundary or wrap_boundary or timer_reset:
                    self._priming = False
                    self.begin(snap)
                    return []

                self._max_progress = max(self._max_progress, snap.progress)
                self._last_progress = snap.progress
                self._last_lap_time_ms = snap.lap_time_ms
                return []
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
        # 用「本圈见过的最大圈计时」而不是 _last_lap_time_ms 做 5 秒门槛：
        # ACC 过线前会先发一帧计时已归零、进度仍为 1.0 的残留帧，把
        # _last_lap_time_ms 覆盖成 ≈0。出场圈过线时 completedLaps 常常不前进，
        # 若此时再看 _last_lap_time_ms，回绕判定就会漏掉这次过线，5 秒后才补判，
        # 首圈开头约 5 秒的走线被丢掉（实测坏圈首点固定 t≈5050ms）。
        wrap_boundary = (
            self._max_progress > 0.90
            and snap.progress < 0.20
            and max(self._max_lap_time_ms, snap.lap_time_ms) > 5_000
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
                # 已跑完大半圈：视为过线（出场圈有时不增加 completedLaps），落库本圈
                result = self._finalize(snap)
                if result is not None:
                    results.append(result)
                self._no = max(self._no + 1, int(snap.completed_laps) + 1)
                self._started_partial = False
                self._started_in_pit = snap.in_pit_lane or snap.in_pit
                self._invalid_latched = not snap.valid_lap
            elif self._max_progress > 0.3:
                # 飞驰圈中途计时器抖动（ACC 偶发把 lap_time 瞬间归零）：这是误报，
                # 正跑着的圈不能丢、会话也不能重开，只平滑恢复计时器基准。
                self._last_lap_time_ms = snap.lap_time_ms
                self._last_progress = snap.progress
                self._last_completed_laps = snap.completed_laps
                self._append_sample(snap)
                return results
            else:
                # 起步段计时器真实归零（pit 出场 / 重开），丢弃当前半圈并重启会话
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

        # 过线后 ACC 往往还会再给一两帧**旧进度**（p≈1.0，而圈计时已归零）。
        # 若照抄这帧：_max_progress 会被重新抬回 0.99，于是约 5 秒后
        # 「进度回绕」（p<0.2）条件再次成立 → 误判出第二个圈边界，
        # 这一圈开头的采样被整段丢掉（历史现象：首圈首点固定 t≈5000ms，
        # L1 前 5 秒没有走线）。这里把「过线刚发生 + 我们自己估计的进度还很小
        # + 却报了一个很大的 p」的帧当作旧值跳过，不写采样也不抬 _max_progress。
        if (
            snap.progress > 0.90
            and self._max_progress < 0.50
            and (snap.t_mono - self._last_boundary_mono) < 1.0
        ):
            self._last_lap_time_ms = snap.lap_time_ms
            self._last_completed_laps = snap.completed_laps
            return results

        self._append_sample(snap)
        self._last_lap_time_ms = snap.lap_time_ms
        self._last_progress = snap.progress
        self._max_progress = max(self._max_progress, snap.progress)
        lap_ms = int(snap.lap_time_ms)
        if 0 <= lap_ms <= MAX_LAP_MS:  # 哨兵值不能抬高 5 秒门槛
            self._max_lap_time_ms = max(self._max_lap_time_ms, lap_ms)
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
        # 过线瞬间 ACC 常常先加 completedLaps、下一帧才把 normalizedCarPosition
        # 绕回 0。这时如果照抄 ~1.0 的旧进度，几秒后「进度回绕」条件会再次成立，
        # 同一圈会被判定两次（用还没更新的 iLastTime 重复落库）。
        # 新圈的起点进度只可能是小值，大于 0.5 一律按 0 起算。
        self._max_progress = snap.progress if snap.progress < 0.5 else 0.0
        # 新圈计时上限从 0 起算：过线帧若还带着旧圈的计时（completedLaps 滞后
        # 或残留帧），不能让它成为下一圈的 5 秒门槛依据。
        lap_ms = int(snap.lap_time_ms)
        self._max_lap_time_ms = lap_ms if 0 <= lap_ms < 5_000 else 0
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
        # ACC 的 iLastTime 在下一次过线前一直保留旧值：圈速和上一圈完全相同、
        # 圈计数器又没有前进，说明并没有真的跑完新的一圈（边界被重复触发 /
        # 中途重置 / 回放），直接丢弃，避免同一圈落库两次。
        if ms == self._last_finalized_ms and snap.completed_laps <= self._last_finalized_laps:
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
        self._last_finalized_ms = ms
        self._last_finalized_laps = snap.completed_laps
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
        """按**时间**（采样序号）等距抽稀，保留真实的进度 p 和世界坐标。

        以前是按进度 p 分 1200 个桶、桶内所有字段取平均。出赛道绕圈时车的
        p 几乎不前进，整圈轨迹会全部落进同一个桶、被平均成一个"质心点"，
        圆圈在落库那一刻就没了（RBR L5 实测：129 秒的圈只存下 4338 m 折线）。
        抽稀不改数值、只降采样率，绕圈形状才能保下来。

        代价：p 不再单调（绕圈、打转、倒车都会让 p 回退）。凡是要按 p 查值的
        地方（曲线图、PB 参考、自动生成赛道形状）都得先过
        `sample_util.monotonic_progress()`。
        """
        if not self._samples:
            return []
        total = len(self._samples)
        if total <= self.bins:
            picked = list(self._samples)
        else:
            step = total / self.bins
            picked = [
                self._samples[min(total - 1, int((i + 0.5) * step))]
                for i in range(self.bins)
            ]

        out: list[dict[str, Any]] = []
        for sample in picked:
            row: dict[str, Any] = {
                "idx": len(out),
                "p": round(float(sample["p"]), 6),
            }
            for name in SAMPLE_FIELDS:
                value = sample.get(name)
                if value is None:
                    row[name] = 0 if name in ("rpm", "gear") else None
                elif name in ("rpm", "gear"):
                    row[name] = int(round(float(value)))
                else:
                    row[name] = round(float(value), 4)
            for required in REQUIRED_FIELDS:
                if row.get(required) is None:
                    row[required] = 0
            out.append(row)
        return out
