"""轮胎磨损估算器。

ACC 官方共享内存文档（v1.8.12）把 `physics.tyreWear` 标成红色
「Field is not used by ACC」，实际比赛里通常恒为 0，不能当实时数据源。
但和磨损相关的绿色字段都在：`wheelSlip` / `slipRatio` / `slipAngle` /
`tyreCoreTemp` / `wheelPressure` / `speedKmh` / 雨胎与配方标记。

所以这里用「滑移耗散能量」做物理近似，逐帧积分：

    单轮摩擦功率 ≈ 归一化垂向载荷 × 车速 × 综合滑移 × 温度/胎压修正
    磨损 0..1     = ∫ 功率 dt / FULL_ENERGY

载荷用纵向加速度（速度差分，符号可靠）做前后转移；左右差异交给每个轮子
各自的滑移量，不依赖 accG / heading 的方向约定。

约定：
- 估算从「估算器开始收到帧」那一刻算起，不是整条胎的绝对寿命；
- 换胎（`currentTyreSet` 变化）、圈数回退、session 切换时清零；
- 每完成一圈，用该圈实测滑移能量反推 `full_energy`（自校准），使正常
  驾驶的一圈约 2.5% 磨损，避免滑移字段量纲差异导致几圈就「磨光」；
- 如果某一帧里 `tyreWear` 真的在单调增长（偶发可用），优先用原始值，
  并在输出里把 `source` 标成 "shared"，否则一律 "estimated"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CORNERS = ("FL", "FR", "RL", "RR")

# 兜底校准常数：把「单轮归一化滑移功率」的积分换算成 0..1 磨损。
# 这里不能假设 wheelSlip 是 0..1 归一化量——ACC 的滑移字段量纲不透明，
# 直接拿 4000 当分母会让正常驾驶 4~5 圈就把胎跑成 100%。
# 因此取「一圈约 2.5%」作为初值，并在每完成一圈后用实测单圈能量
# 自动校准（见 TARGET_WEAR_PER_LAP），避免不同赛道 / 驾驶风格下失控。
FULL_ENERGY = 40000.0

# 每完成一圈，把「本圈滑移能量」折算成目标磨损率，用来反推 full_energy：
# 正常驾驶约 2.5%/圈（≈ 40 圈用完），激进驾驶会自动略高。
TARGET_WEAR_PER_LAP = 0.025
# 单圈能量的指数平滑系数：越大跟手越快，越小越抗单圈异常（出站圈 / 失误）
CALIBRATION_GAIN = 0.25
# 单圈能量低于这个值不做校准（基本是停停走走 / 几乎没有滑移的圈）
MIN_LAP_ENERGY = 1.0
# 单圈磨损率上限：防止一次打滑 / 冲出赛道把「剩余圈数」瞬间打到 0
MAX_WEAR_PER_LAP = 0.12
# 刚开始观察时 normalizedCarPosition 已经过了这个值，说明是半圈中途接入，
# 这一圈不能拿来做单圈能量校准
PARTIAL_LAP_PROGRESS = 0.10

# 两帧间隔超过这个值视为暂停 / 回放 / 卡顿，跳过积分（只更新基线）
MAX_DT = 0.5
# 低于这个车速（km/h）不产生有效滑移磨损
MIN_SPEED_KMH = 3.0
# 原始 tyreWear 至少变化这么多才认为是「真的在更新」，否则当噪声丢弃
SHARED_MIN_DELTA = 0.01
# 当前值必须接近本 stint 的最大值（允许 0.002 回差），否则认为不是单调磨损
SHARED_MAX_BACKOFF = 0.002


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _seq(snap: Any, name: str, index: int) -> float | None:
    """安全读取 Snapshot 里的四元组字段。"""
    arr = getattr(snap, name, None)
    try:
        value = float(arr[index])  # type: ignore[index]
    except (TypeError, IndexError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _temp_factor(temp_c: float | None) -> float:
    """胎芯温度修正：冷胎和过热都磨得更快。"""
    if temp_c is None or temp_c <= 0:
        return 1.0
    if temp_c < 50:
        return 0.80
    if temp_c < 80:
        return 0.80 + 0.20 * (temp_c - 50) / 30.0
    if temp_c <= 100:
        return 1.0
    if temp_c <= 115:
        return 1.0 + 0.02 * (temp_c - 100)
    return 1.30 + 0.02 * min(temp_c - 115, 25)


def _pressure_factor(pressure_psi: float | None) -> float:
    """胎压偏离 27.5 psi 越多，接地面压力分布越差，磨损略增。"""
    if pressure_psi is None or pressure_psi <= 0:
        return 1.0
    return 1.0 + min(abs(pressure_psi - 27.5), 8.0) * 0.01


@dataclass(slots=True)
class _WheelState:
    energy: float = 0.0
    lap_energy: float = 0.0
    rate_per_lap: float | None = None
    # 原始 tyreWear 观测：只有「持续变化且单调增加」才采用
    raw_first: float | None = None
    raw_last: float | None = None
    raw_min: float | None = None
    raw_max: float | None = None


class TyreWearEstimator:
    """吃 Snapshot 流，输出四轮磨损估算。纯内存、无 I/O，可单测。"""

    def __init__(self, full_energy: float = FULL_ENERGY) -> None:
        self.full_energy = max(1.0, float(full_energy))
        self._wheels = [_WheelState() for _ in range(4)]
        self._last_t: float | None = None
        self._last_speed = 0.0
        self._last_laps: int | None = None
        self._last_tyre_set: int | None = None
        self._last_session_index: int | None = None
        self._has_data = False
        # 单圈能量参考值（滑移能量单位），用于自校准 full_energy
        self._lap_energy_ref: float | None = None
        # 接入 / 换胎后的第一圈可能是半圈，先跳过校准
        self._laps_started_partial = True

    # ------------------------------------------------------------------ 公共
    def reset(self) -> None:
        """清空全部积分（游戏退出 / 共享内存关闭时调用）。"""
        for wheel in self._wheels:
            wheel.energy = 0.0
            wheel.lap_energy = 0.0
            wheel.rate_per_lap = None
            wheel.raw_first = None
            wheel.raw_last = None
            wheel.raw_min = None
            wheel.raw_max = None
        self._last_t = None
        self._last_speed = 0.0
        self._last_laps = None
        self._last_tyre_set = None
        self._last_session_index = None
        self._has_data = False
        self._lap_energy_ref = None
        self._laps_started_partial = True

    def update(self, snap: Any) -> None:
        """喂一帧 Snapshot。非直播帧由采集层过滤，这里不重复判断。"""
        if snap is None:
            return
        now = _num(getattr(snap, "t_mono", None), -1.0)
        if now < 0:
            return

        speed_kmh = max(0.0, _num(getattr(snap, "speed_kmh", 0.0)))
        speed = speed_kmh / 3.6
        laps = int(_num(getattr(snap, "completed_laps", 0)))
        tyre_set = int(_num(getattr(snap, "current_tyre_set", 0)))
        session_index = int(_num(getattr(snap, "session_index", 0)))
        progress = _num(getattr(snap, "progress", 0.0))

        # 第一次收到帧时判断是不是从半圈接入：半圈的能量不能当整圈参考
        if self._last_laps is None:
            self._laps_started_partial = progress > PARTIAL_LAP_PROGRESS

        # 新 stint：圈数回退（重开比赛）/ 换胎 / 换了 session（练习→排位→正赛）
        if (
            (self._last_laps is not None and laps < self._last_laps)
            or (
                self._last_tyre_set
                and tyre_set
                and tyre_set != self._last_tyre_set
            )
            or (
                self._last_session_index is not None
                and session_index != self._last_session_index
            )
        ):
            self._reset_stint()

        dt = None if self._last_t is None else now - self._last_t
        if dt is not None and 0.0 < dt <= MAX_DT and speed_kmh >= MIN_SPEED_KMH:
            self._integrate(snap, dt, speed)
            self._has_data = True

        # 圈边界：completedLaps 前进时结算上一圈的四轮磨损率。
        # 顺便用这一圈的实际滑移能量校准 full_energy，保证「正常驾驶一整圈」
        # 落在 TARGET_WEAR_PER_LAP 附近，而不是几圈就把胎磨光。
        if self._last_laps is not None and laps > self._last_laps:
            lap_count = max(1, laps - self._last_laps)
            deltas = [
                max(0.0, wheel.energy - wheel.lap_energy) / lap_count
                for wheel in self._wheels
            ]
            if self._laps_started_partial:
                # 接入 / 换胎后的第一圈不完整，只更新基线，不校准也不给速率
                self._laps_started_partial = False
            else:
                self._calibrate(deltas)
                for wheel, delta in zip(self._wheels, deltas):
                    if delta > 0:
                        wheel.rate_per_lap = min(
                            MAX_WEAR_PER_LAP, delta / self.full_energy
                        )
            for wheel in self._wheels:
                wheel.lap_energy = wheel.energy
        elif self._last_laps is None:
            for wheel in self._wheels:
                wheel.lap_energy = wheel.energy

        self._observe_raw_wear(snap)

        self._last_t = now
        self._last_speed = speed
        self._last_laps = laps
        if tyre_set:
            self._last_tyre_set = tyre_set
        self._last_session_index = session_index

    def snapshot(self) -> dict[str, Any]:
        """给 `bus.runtime["tyre_wear"]`，由 state.py 合并进主题状态。"""
        wheels: list[dict[str, Any]] = []
        weakest_laps: float | None = None
        all_shared = True
        any_shared = False

        for index, wheel in enumerate(self._wheels):
            estimate = min(0.999, wheel.energy / self.full_energy)
            shared = self._shared_wear(wheel)
            if shared is not None:
                wear = shared
                source = "shared"
                any_shared = True
            else:
                wear = estimate if self._has_data else None
                source = "estimated" if self._has_data else "none"
                all_shared = False

            rate = wheel.rate_per_lap
            laps_left: float | None = None
            if wear is not None and rate is not None and rate > 1e-6:
                laps_left = max(0.0, (1.0 - wear) / rate)
                if weakest_laps is None or laps_left < weakest_laps:
                    weakest_laps = laps_left

            wheels.append(
                {
                    "corner": CORNERS[index],
                    "wear": None if wear is None else round(wear, 4),
                    "ratePerLap": None if rate is None else round(rate, 5),
                    "lapsLeft": None if laps_left is None else round(laps_left, 1),
                    "source": source,
                }
            )

        if all_shared and any_shared:
            overall = "shared"
        elif self._has_data:
            overall = "estimated"
        else:
            overall = "none"

        return {
            "source": overall,
            "lapsLeft": None if weakest_laps is None else round(weakest_laps, 1),
            "wheels": wheels,
        }

    # ------------------------------------------------------------------ 内部
    def _reset_stint(self) -> None:
        for wheel in self._wheels:
            wheel.energy = 0.0
            wheel.lap_energy = 0.0
            wheel.rate_per_lap = None
            wheel.raw_first = None
            wheel.raw_last = None
            wheel.raw_min = None
            wheel.raw_max = None
        self._has_data = False
        # 换胎 / 换 session 后重新学习单圈能量参考值
        self._lap_energy_ref = None
        self._laps_started_partial = True

    def _calibrate(self, deltas: list[float]) -> None:
        """用一圈的实测滑移能量反推 full_energy。

        `deltas` 是四轮各自的单圈能量；取中位数避免某个轮子单圈打滑把
        参考值带偏。参考值做指数平滑，既跟得上赛道 / 驾驶风格变化，
        又不会被一次失误直接改坏。
        """
        ordered = sorted(deltas)
        observed = 0.5 * (ordered[1] + ordered[2])  # 四轮中位数
        if observed < MIN_LAP_ENERGY:
            return
        if self._lap_energy_ref is None:
            self._lap_energy_ref = observed
        else:
            self._lap_energy_ref += CALIBRATION_GAIN * (
                observed - self._lap_energy_ref
            )
        self.full_energy = max(1.0, self._lap_energy_ref / TARGET_WEAR_PER_LAP)

    def _integrate(self, snap: Any, dt: float, speed: float) -> None:
        # 纵向载荷转移：用速度差分（符号确定），不依赖 accG / heading 约定。
        # 0.18 ≈ 重心高度 / 轴距，GT3 的大致值；夹住防止刹车瞬间尖峰。
        accel = (speed - self._last_speed) / dt
        transfer = _clamp(0.18 * accel / 9.81, -0.35, 0.35)
        front = 0.5 * (1.0 - transfer)
        rear = 0.5 * (1.0 + transfer)
        loads = (front / 2.0, front / 2.0, rear / 2.0, rear / 2.0)

        compound = 0.90 if bool(getattr(snap, "rain_tyres", False)) else 1.0
        temps = getattr(snap, "tyre_core_temp", ())
        pressures = getattr(snap, "wheel_pressure", ())

        for index, wheel in enumerate(self._wheels):
            slip = self._slip(snap, index)
            try:
                temp = float(temps[index])
            except (TypeError, IndexError, ValueError):
                temp = None
            try:
                pressure = float(pressures[index])
            except (TypeError, IndexError, ValueError):
                pressure = None

            # 基础滚阻项让直道也有缓慢磨损，弯中滑移是主项
            slide = slip + 0.005 * min(1.0, speed / 50.0)
            power = loads[index] * speed * slide
            power *= (
                _temp_factor(temp)
                * _pressure_factor(pressure)
                * compound
            )
            wheel.energy += power * dt

    @staticmethod
    def _slip(snap: Any, index: int) -> float:
        """综合滑移：优先用 wheelSlip，再和 slipRatio/slipAngle 推导值取大。"""
        ws = _seq(snap, "wheel_slip", index)
        sr = _seq(snap, "slip_ratio", index)
        sa = _seq(snap, "slip_angle", index)
        derived = 0.0
        if sr is not None or sa is not None:
            derived = math.hypot(sr or 0.0, math.tan(_clamp(sa or 0.0, -1.2, 1.2)))
        value = 0.0
        if ws is not None:
            value = _clamp(abs(ws), 0.0, 1.5)
        if derived > value:
            value = _clamp(derived, 0.0, 1.5)
        return value

    def _observe_raw_wear(self, snap: Any) -> None:
        raw_values = getattr(snap, "tyre_wear", None)
        if not raw_values:
            return
        for index, wheel in enumerate(self._wheels):
            try:
                raw = float(raw_values[index])
            except (TypeError, IndexError, ValueError):
                continue
            if not math.isfinite(raw) or raw < 0.0 or raw > 1.5:
                continue
            if wheel.raw_first is None:
                wheel.raw_first = raw
            wheel.raw_min = raw if wheel.raw_min is None else min(wheel.raw_min, raw)
            wheel.raw_max = raw if wheel.raw_max is None else max(wheel.raw_max, raw)
            wheel.raw_last = raw

    @staticmethod
    def _shared_wear(wheel: _WheelState) -> float | None:
        """原始 tyreWear 只有在持续、单调增长时才可信。

        ACC 平时返回 0 或常数；如果某次真的在更新，用实测值覆盖估算值。
        """
        if wheel.raw_last is None or wheel.raw_first is None:
            return None
        if wheel.raw_last - wheel.raw_first < SHARED_MIN_DELTA:
            return None
        # 当前值明显低于本 stint 最大值 → 不是磨损曲线（可能被重置 / 噪声）
        if wheel.raw_max is not None and wheel.raw_last < wheel.raw_max - SHARED_MAX_BACKOFF:
            return None
        if not (0.0 <= wheel.raw_last <= 1.0):
            return None
        return wheel.raw_last
