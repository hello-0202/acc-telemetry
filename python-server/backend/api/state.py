"""主题状态快照：把 Snapshot + 会话元信息 + PB 参考合成为一份扁平对象。

契约：`python-server/contracts/telemetry-state.json`
对外文档：`docs/theme-state-contract.md`
消息：`{"type": "state", ...}`，由 `broadcaster` 按 `bus.runtime["state_hz"]` 推送。

设计原则：
- 这里只做「换名 + 定单位 + 语义化」，采集层不加计算，避免污染记录链路；
- 计算类字段（`lap.deltaMs` / `car.gearText` / `lap.num`）在这里算好，主题不用自己推；
- **体积控制**：PB 参考圈有上千点，绝不进 state；state 只给 `lap.pbMs` / `lap.deltaMs`，
  逐点曲线仍走一次性的 `pbref` 事件（历史图表用）。
"""

from __future__ import annotations

import time
from bisect import bisect_left
from typing import Any

from .. import bus

# 与 contracts/telemetry-state.json 的 version 保持一致：只有破坏性变更才 +1
STATE_VERSION = 1

_CORNERS = ("FL", "FR", "RL", "RR")
# 超过这么久没有新帧，就认为「不在直播」（暂停 / 回放 / 退到菜单）
_STALE_AFTER_S = 1.5

# 上一次成功组装的实时状态，用于「非 streaming 时保留最后一个有效值」
_last_live: dict[str, Any] | None = None
# 上一次有效的会话元信息。停止监测后 recorder 会清掉 bus.runtime["state_meta"]，
# 但此时总线里还留着最后一帧快照——不能因此把赛道名 / 车名 / PB 抹成空，
# 否则主题会从「Zolder + 车名」闪回空白。
_last_meta: dict[str, Any] | None = None
_seq = 0


# --------------------------------------------------------------------- 小工具
def _r(value: Any, nd: int = 3) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), nd)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def gear_text(raw_gear: Any) -> str:
    """ACC 原始挡位 → 显示文本。0=R, 1=N, 2+=1 挡。"""
    raw = _i(raw_gear)
    if raw is None:
        return "N"
    if raw == 0:
        return "R"
    if raw == 1:
        return "N"
    return str(raw - 1)


def semantic_gear(raw_gear: Any) -> int | None:
    """ACC 原始挡位 → 语义化挡位：-1=倒挡，0=空挡，1..n=前进挡。"""
    raw = _i(raw_gear)
    if raw is None:
        return None
    return raw - 1


def interp_reference(ps: list[float], ts: list[float], p: float) -> float | None:
    """按赛道进度 p 在 PB 参考曲线上插值出「此刻应处的时间」。

    与前端 `useTelemetry.js::interpReference` 同一套算法，两边算出来的 delta
    必须一致，否则主面板和主题会显示不同的差值。
    """
    if not ps or not ts:
        return None
    if p <= ps[0]:
        return ts[0]
    if p >= ps[-1]:
        return ts[-1]
    i = bisect_left(ps, p)
    if i <= 0:
        return ts[0]
    span = ps[i] - ps[i - 1]
    if span <= 0:
        return ts[i]
    ratio = (p - ps[i - 1]) / span
    return ts[i - 1] + (ts[i] - ts[i - 1]) * ratio


# --------------------------------------------------------------------- 状态机
def current_status() -> str:
    """idle | waiting | streaming | error（枚举见契约 enums.status）。"""
    if not bus.runtime.get("recording"):
        return "error" if bus.runtime.get("state_error") else "idle"
    snap = bus.latest_snapshot
    if snap is None:
        return "waiting"
    t_mono = float(getattr(snap, "t_mono", 0.0) or 0.0)
    if time.monotonic() - t_mono > _STALE_AFTER_S:
        return "waiting"
    return "streaming"


# --------------------------------------------------------------------- 组装
def _tyre(index: int, snap: Any) -> dict[str, Any]:
    def pick(name: str) -> tuple:
        return tuple(getattr(snap, name, ()) or ())

    def at(seq: tuple, nd: int) -> float | None:
        try:
            return round(float(seq[index]), nd)
        except (IndexError, TypeError, ValueError):
            return None

    return {
        "corner": _CORNERS[index],
        "pressure": at(pick("wheel_pressure"), 2),
        "temp": at(pick("tyre_core_temp"), 1),
        "brakeTemp": at(pick("brake_temp"), 1),
        "slip": at(pick("wheel_slip"), 3),
        "slipRatio": at(pick("slip_ratio"), 3),
        "slipAngle": at(pick("slip_angle"), 4),
        "suspensionTravel": at(pick("suspension_travel"), 4),
        "wear": at(pick("tyre_wear"), 3),
    }


def _delta_ms(snap: Any, meta: dict[str, Any]) -> int | None:
    lap_time = _i(getattr(snap, "lap_time_ms", None))
    progress = _r(getattr(snap, "progress", None), 5)
    if lap_time is None or progress is None:
        return None
    pb_ref = meta.get("pbRef") or {}
    ps = pb_ref.get("p") or []
    ts = pb_ref.get("tMs") or []
    if ps and ts:
        reference = interp_reference(ps, ts, progress)
        return None if reference is None else int(round(lap_time - reference))
    pb_ms = meta.get("pbMs")
    if pb_ms:
        # 没有逐点参考时退化为按进度线性估算（与前端一致）
        return int(round(lap_time - float(pb_ms) * progress))
    return None


def _build_live(snap: Any, meta: dict[str, Any]) -> dict[str, Any]:
    track = meta.get("track") or {}
    car = meta.get("car") or {}
    session_type = (
        meta.get("sessionType")
        or getattr(snap, "session_type", "unknown")
        or "unknown"
    )
    current_max_rpm = _i(getattr(snap, "current_max_rpm", 0)) or 0
    static_max_rpm = _i(car.get("maxRpm")) or 0
    return {
        "session": {
            "type": session_type,
            "timeLeftMs": _i(getattr(snap, "session_time_left_ms", 0)),
            "lapsTotal": _i(getattr(snap, "laps_total", 0)),
            "index": _i(getattr(snap, "session_index", 0)),
        },
        "track": {
            "name": track.get("name") or "",
            "title": track.get("title") or "",
            "configuration": track.get("configuration") or "",
            "lengthM": _r(track.get("lengthM"), 1),
            "sectorCount": _i(track.get("sectorCount")) or 0,
        },
        "car": {
            "name": car.get("name") or "",
            "maxRpm": static_max_rpm,
            "maxFuel": _r(car.get("maxFuel"), 2),
            "maxTorqueNm": _r(car.get("maxTorque"), 1),
            "maxPowerKw": _r(car.get("maxPower"), 1),
            "speed": _r(getattr(snap, "speed_kmh", 0.0), 1),
            "rpm": _i(getattr(snap, "rpm", 0)),
            # 当前允许的最大转速为 0 时退回静态最大转速，主题画红线才不会除以 0
            "maxRpmCurrent": current_max_rpm or static_max_rpm,
            "gear": semantic_gear(getattr(snap, "gear", None)),
            "gearText": gear_text(getattr(snap, "gear", None)),
            "throttle": _r(getattr(snap, "throttle", 0.0), 3),
            "brake": _r(getattr(snap, "brake", 0.0), 3),
            "steer": _r(getattr(snap, "steer", 0.0), 3),
            "clutch": _r(getattr(snap, "clutch", 0.0), 3),
            "fuel": _r(getattr(snap, "fuel", 0.0), 2),
            "fuelPerLap": _r(getattr(snap, "fuel_per_lap", 0.0), 2),
            "fuelLapsLeft": _r(getattr(snap, "fuel_laps_left", 0.0), 1),
            "fuelUsed": _r(getattr(snap, "fuel_used", 0.0), 2),
            "brakeBias": _r(getattr(snap, "brake_bias", 0.0), 3),
            "tcLevel": _i(getattr(snap, "tc_level", 0)),
            "tcActive": _r(getattr(snap, "tc_active", 0.0), 2),
            "absLevel": _i(getattr(snap, "abs_level", 0)),
            "absActive": _r(getattr(snap, "abs_active", 0.0), 2),
            "engineMap": _i(getattr(snap, "engine_map", 0)),
            "pitLimiter": bool(getattr(snap, "pit_limiter_on", False)),
            "drs": bool(getattr(snap, "drs_active", False)),
            "autoShifter": bool(getattr(snap, "auto_shifter_on", False)),
            "aiControlled": bool(getattr(snap, "ai_controlled", False)),
            "tyreCompound": getattr(snap, "tyre_compound", "") or "",
            "rainTyres": bool(getattr(snap, "rain_tyres", False)),
            "gLong": _r(getattr(snap, "g_long", None), 2),
            "gLat": _r(getattr(snap, "g_lat", None), 2),
        },
        "lap": {
            # 本次监测内的第几圈（与主面板一致）；session 自身的圈数看 lap.completed + 1
            "num": _i(bus.runtime.get("lap", 1)) or 1,
            "completed": _i(getattr(snap, "completed_laps", 0)),
            "timeMs": _i(getattr(snap, "lap_time_ms", 0)),
            "lastMs": _i(getattr(snap, "last_lap_ms", 0)),
            "bestMs": _i(getattr(snap, "best_lap_ms", 0)),
            "pbMs": _i(meta.get("pbMs")),
            "deltaMs": _delta_ms(snap, meta),
            "estimatedMs": _i(getattr(snap, "estimated_lap_ms", 0)),
            "progress": _r(getattr(snap, "progress", 0.0), 5),
            "sector": _i(getattr(snap, "sector_index", 0)),
            "sectorLastMs": _i(getattr(snap, "last_sector_ms", 0)),
            "splitMs": _i(getattr(snap, "split_ms", 0)),
            "valid": bool(getattr(snap, "valid_lap", False)),
            "inPit": bool(getattr(snap, "in_pit", False)),
            "inPitLane": bool(getattr(snap, "in_pit_lane", False)),
            "position": _i(getattr(snap, "position", 0)),
            "penaltyMs": _i(getattr(snap, "penalty_ms", 0)),
        },
        "position": {
            "x": _r(getattr(snap, "world_x", None), 2),
            "z": _r(getattr(snap, "world_y", None), 2),
            "heading": _r(getattr(snap, "heading", 0.0), 4),
            "distanceM": _r(getattr(snap, "distance_m", 0.0), 1),
        },
        "wheels": [_tyre(i, snap) for i in range(4)],
        "env": {
            "airTemp": _r(getattr(snap, "air_temp", 0.0), 1),
            "roadTemp": _r(getattr(snap, "road_temp", 0.0), 1),
            "airDensity": _r(getattr(snap, "air_density", 0.0), 3),
            "rainIntensity": _i(getattr(snap, "rain_intensity", 0)),
            "rainIn10min": _i(getattr(snap, "rain_in_10min", 0)),
            "rainIn30min": _i(getattr(snap, "rain_in_30min", 0)),
            "grip": _i(getattr(snap, "track_grip", 0)),
            "surfaceGrip": _r(getattr(snap, "surface_grip", 0.0), 3),
            "windSpeed": _r(getattr(snap, "wind_speed", 0.0), 2),
            "windDirection": _r(getattr(snap, "wind_direction", 0.0), 3),
            "timeOfDay": _r(getattr(snap, "time_of_day", 0.0), 3),
        },
        "flags": {
            "yellow": _i(getattr(snap, "flag_yellow", 0)),
            "white": _i(getattr(snap, "flag_white", 0)),
            "green": _i(getattr(snap, "flag_green", 0)),
            "chequered": _i(getattr(snap, "flag_chequered", 0)),
            "red": _i(getattr(snap, "flag_red", 0)),
            "trackStatus": getattr(snap, "track_status", "") or "",
            "mandatoryPitMissing": _i(getattr(snap, "mandatory_pits_missing", 0)),
        },
    }


def _blank_state() -> dict[str, Any]:
    """还没收到任何遥测时的骨架：结构齐全、实时值为 null，主题不会因缺 key 崩。"""
    return {
        "session": {"type": "unknown", "timeLeftMs": None, "lapsTotal": None, "index": None},
        "track": {
            "name": "",
            "title": "",
            "configuration": "",
            "lengthM": None,
            "sectorCount": 0,
        },
        "car": {
            "name": "",
            "maxRpm": 0,
            "maxFuel": None,
            "maxTorqueNm": None,
            "maxPowerKw": None,
            "speed": None,
            "rpm": None,
            "maxRpmCurrent": 0,
            "gear": None,
            "gearText": "N",
            "throttle": None,
            "brake": None,
            "steer": None,
            "clutch": None,
            "fuel": None,
            "fuelPerLap": None,
            "fuelLapsLeft": None,
            "fuelUsed": None,
            "brakeBias": None,
            "tcLevel": None,
            "tcActive": None,
            "absLevel": None,
            "absActive": None,
            "engineMap": None,
            "pitLimiter": False,
            "drs": False,
            "autoShifter": False,
            "aiControlled": False,
            "tyreCompound": "",
            "rainTyres": False,
            "gLong": None,
            "gLat": None,
        },
        "lap": {
            "num": 1,
            "completed": None,
            "timeMs": None,
            "lastMs": None,
            "bestMs": None,
            "pbMs": None,
            "deltaMs": None,
            "estimatedMs": None,
            "progress": None,
            "sector": None,
            "sectorLastMs": None,
            "splitMs": None,
            "valid": False,
            "inPit": False,
            "inPitLane": False,
            "position": None,
            "penaltyMs": None,
        },
        "position": {"x": None, "z": None, "heading": None, "distanceM": None},
        "wheels": [
            {
                "corner": _CORNERS[i],
                "pressure": None,
                "temp": None,
                "brakeTemp": None,
                "slip": None,
                "slipRatio": None,
                "slipAngle": None,
                "suspensionTravel": None,
                "wear": None,
            }
            for i in range(4)
        ],
        "env": {
            "airTemp": None,
            "roadTemp": None,
            "airDensity": None,
            "rainIntensity": None,
            "rainIn10min": None,
            "rainIn30min": None,
            "grip": None,
            "surfaceGrip": None,
            "windSpeed": None,
            "windDirection": None,
            "timeOfDay": None,
        },
        "flags": {
            "yellow": None,
            "white": None,
            "green": None,
            "chequered": None,
            "red": None,
            "trackStatus": "",
            "mandatoryPitMissing": None,
        },
    }


def _apply_meta(state: dict[str, Any], meta: dict[str, Any]) -> None:
    """把会话级元信息覆盖到（可能来自缓存的）状态上。

    赛道 / 车辆 / 会话类型不随帧变化，但换赛道重开会话时缓存里的旧值必须换掉。
    """
    if not meta:
        return
    track = meta.get("track") or {}
    car = meta.get("car") or {}
    if track:
        state["track"].update(
            name=track.get("name") or state["track"].get("name", ""),
            title=track.get("title") or state["track"].get("title", ""),
            configuration=track.get("configuration")
            or state["track"].get("configuration", ""),
            sectorCount=_i(track.get("sectorCount")) or state["track"].get("sectorCount", 0),
        )
        if track.get("lengthM") is not None:
            state["track"]["lengthM"] = _r(track.get("lengthM"), 1)
    if car:
        if car.get("name"):
            state["car"]["name"] = car["name"]
        if car.get("maxRpm"):
            state["car"]["maxRpm"] = _i(car.get("maxRpm")) or 0
        if car.get("maxFuel") is not None:
            state["car"]["maxFuel"] = _r(car.get("maxFuel"), 2)
    if meta.get("sessionType"):
        state["session"]["type"] = meta["sessionType"]
    if meta.get("pbMs") is not None:
        state["lap"]["pbMs"] = _i(meta.get("pbMs"))


def build_state() -> dict[str, Any]:
    """组装一份完整状态快照（主题唯一数据源）。"""
    global _last_live, _last_meta, _seq

    status = current_status()
    meta = bus.runtime.get("state_meta") or {}
    if meta:
        _last_meta = meta
    elif _last_meta is not None:
        meta = _last_meta
    snap = bus.latest_snapshot

    if snap is not None:
        state = _build_live(snap, meta)
        _last_live = state
    elif _last_live is not None:
        # 非 streaming：保留最后一个有效值，只刷新元信息与状态（契约 stability 约定）
        state = _last_live
        _apply_meta(state, meta)
    else:
        state = _blank_state()
        _apply_meta(state, meta)

    _seq += 1
    # 顶层字段放在最前面，方便排查日志时一眼看到状态
    return {
        "type": "state",
        "v": STATE_VERSION,
        "seq": _seq,
        "t": int(time.time() * 1000),
        "status": status,
        **state,
    }


def reset() -> None:
    """应用重启 / 换会话时清掉缓存，避免把上一场的值当成当前值。"""
    global _last_live, _last_meta, _seq
    _last_live = None
    _last_meta = None
    _seq = 0
