"""真实 ACC 共享内存数据源。"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from ctypes import wintypes

from pyACCSharedMemory.acc_data import ACCConstants, ACCGraphics, ACCPhysics, ACCStatic
from pyACCSharedMemory.acc_mmap import MMapControl

from .base import GameNotRunning, Snapshot, StaticInfo, session_type_name

FILE_MAP_READ = 0x0004

BLOCKS = (
    ACCConstants.MM_PHYSICS_FILE_NAME,
    ACCConstants.MM_GRAPHICS_FILE_NAME,
    ACCConstants.MM_STATIC_FILE_NAME,
)

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenFileMappingW.restype = wintypes.HANDLE
    _k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
else:  # pragma: no cover - ACC 仅 Windows
    _k32 = None


def section_exists(name: str) -> bool:
    """共享内存段是否存在 → 等价于「游戏是否在运行」。

    mmap.mmap(..., tagname=...) 在段不存在时会自己创建一个全零段，
    所以必须先探测，避免把「游戏没开」误判成「数据全是 0」。
    """
    if _k32 is None:
        return False
    handle = _k32.OpenFileMappingW(FILE_MAP_READ, False, "Local\\" + name)
    if handle:
        _k32.CloseHandle(handle)
        return True
    return False


def game_running() -> bool:
    return all(section_exists(name) for name in BLOCKS)


def _lap_ms(value: int) -> int:
    """过滤 ACC 用哨兵值表示的「暂无成绩」。"""
    if value is None:
        return 0
    value = int(value)
    if value <= 0 or value in (99999999, 2147483647):
        return 0
    return value


def _wheel_tuple(arr) -> tuple[float, float, float, float]:
    return (float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3]))


def _player_world_xy(graphics) -> tuple[float | None, float | None]:
    """从 graphics.carCoordinates 里找出玩家车的世界坐标（米）。

    ACC 不直接给玩家坐标，需要按 playerCarID 在 carIDs 里匹配下标。
    """
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
        # ACC 世界坐标里 y 是高度方向，赛道平面是 x-z
        x, z = float(coord.x), float(coord.z)
        if x == 0.0 and z == 0.0:
            return None, None
        return x, z
    except Exception:
        return None, None


class AccSharedMemoryProvider:
    """三个共享内存块：physics / graphics / static（copy 模式，避免撕裂）。"""

    def __init__(self) -> None:
        self._phys: MMapControl | None = None
        self._gfx: MMapControl | None = None
        self._static: MMapControl | None = None

    # ------------------------------------------------------------------ 探测
    def available(self) -> bool:
        return game_running()

    # ------------------------------------------------------------------ 打开/关闭
    def open(self) -> None:
        if not self.available():
            raise GameNotRunning("未找到 ACC 游戏进程，请先进入比赛")

        created: list[MMapControl] = []
        try:
            phys = MMapControl(ACCConstants.MM_PHYSICS_FILE_NAME, ACCPhysics)
            phys.create(access_mode=0)
            created.append(phys)

            gfx = MMapControl(ACCConstants.MM_GRAPHICS_FILE_NAME, ACCGraphics)
            gfx.create(access_mode=0)
            created.append(gfx)

            static = MMapControl(ACCConstants.MM_STATIC_FILE_NAME, ACCStatic)
            static.create(access_mode=0)
            created.append(static)
        except Exception:
            for control in reversed(created):
                try:
                    control.close()
                except Exception:
                    pass
            raise

        self._phys, self._gfx, self._static = phys, gfx, static

    def close(self) -> None:
        for control in (self._static, self._gfx, self._phys):
            if control is not None:
                try:
                    control.close()
                except Exception:
                    pass
        self._phys = self._gfx = self._static = None

    # ------------------------------------------------------------------ 读取
    def read(self) -> Snapshot:
        if self._phys is None or self._gfx is None:
            raise GameNotRunning("共享内存尚未打开")

        self._phys.update()
        self._gfx.update()
        p = self._phys.data
        g = self._gfx.data

        heading = float(getattr(p, "heading", 0.0))
        ax = float(getattr(p.acceleration, "x", 0.0))
        ay = float(getattr(p.acceleration, "y", 0.0))
        # 世界坐标 → 车体坐标；符号约定以曲线图观感为准，仅用于历史展示
        g_long = (ax * math.cos(heading) + ay * math.sin(heading)) / 9.81
        g_lat = (-ax * math.sin(heading) + ay * math.cos(heading)) / 9.81
        world_x, world_y = _player_world_xy(g)

        return Snapshot(
            t_mono=time.monotonic(),
            packet_physics=int(p.packetId),
            packet_graphics=int(g.packetId),
            speed_kmh=float(p.speedKmh),
            rpm=int(p.rpm),
            gear=int(p.gear),
            throttle=float(p.throttle),
            brake=float(p.brake),
            steer=float(p.steerAngle),
            wheel_pressure=_wheel_tuple(p.wheelPressure),
            tyre_core_temp=_wheel_tuple(p.tyreCoreTemp),
            brake_temp=_wheel_tuple(p.brakeTemp),
            fuel=float(p.fuel),
            current_max_rpm=int(p.currentMaxRPM),
            g_long=g_long,
            g_lat=g_lat,
            world_x=world_x,
            world_y=world_y,
            lap_time_ms=int(g.iCurrentTime),
            last_lap_ms=_lap_ms(g.iLastTime),
            completed_laps=int(g.completedLaps),
            sector_index=int(g.currentSectorIndex),
            last_sector_ms=int(g.lastSectorTime),
            split_ms=int(g.iSplit),
            progress=float(g.normalizedCarPosition),
            valid_lap=bool(g.isValidLap),
            in_pit_lane=bool(g.isInPitLane),
            in_pit=bool(g.isInPit),
            status=int(g.status),
            heading=heading,
            session_type=session_type_name(getattr(g, "session", -1)),
            # 温度在 physics 块，降雨 / 抓地力在 graphics 块
            air_temp=float(getattr(p, "airTemp", 0.0)),
            road_temp=float(getattr(p, "roadTemp", 0.0)),
            rain_intensity=int(getattr(g, "rainIntensity", 0)),
            track_grip=int(getattr(g, "trackGripStatus", 0)),
            # ---- 主题状态契约扩展字段（原样搬运，不做换算）----
            best_lap_ms=_lap_ms(getattr(g, "iBestTime", 0)),
            estimated_lap_ms=_lap_ms(getattr(g, "iEstimatedLapTime", 0)),
            penalty_ms=int(_lap_ms(getattr(g, "penaltyTime", 0))),
            position=int(getattr(g, "position", 0)),
            distance_m=float(getattr(g, "distanceTraveled", 0.0)),
            session_time_left_ms=float(getattr(g, "sessionTimeLeft", 0.0)),
            laps_total=int(getattr(g, "numberOfLaps", 0)),
            session_index=int(getattr(g, "sessionIndex", 0)),
            tyre_compound=str(getattr(g, "tyreCompound", "")).strip(),
            rain_tyres=bool(getattr(g, "rainTyres", 0)),
            tc_level=int(getattr(g, "tcLevel", 0)),
            abs_level=int(getattr(g, "absLevel", 0)),
            engine_map=int(getattr(g, "engineMap", 0)),
            fuel_per_lap=float(getattr(g, "fuelXLap", 0.0)),
            fuel_laps_left=float(getattr(g, "fuelEstimatedLaps", 0.0)),
            fuel_used=float(getattr(g, "usedFuel", 0.0)),
            rain_in_10min=int(getattr(g, "rainIntensityIn10min", 0)),
            rain_in_30min=int(getattr(g, "rainIntensityIn30min", 0)),
            surface_grip=float(getattr(g, "surfaceGrip", 0.0)),
            wind_speed=float(getattr(g, "windSpeed", 0.0)),
            wind_direction=math.radians(float(getattr(g, "windDirection", 0.0))),
            time_of_day=float(getattr(g, "timeOfDay", 0.0)),
            flag_yellow=int(getattr(g, "globalYellow", 0)),
            flag_white=int(getattr(g, "globalWhite", 0)),
            flag_green=int(getattr(g, "globalGreen", 0)),
            flag_chequered=int(getattr(g, "globalChequered", 0)),
            flag_red=int(getattr(g, "globalRed", 0)),
            track_status=str(getattr(g, "trackStatus", "")).strip(),
            mandatory_pits_missing=int(getattr(g, "missingMandatoryPits", 0)),
            clutch=float(getattr(p, "clutch", 0.0)),
            brake_bias=float(getattr(p, "brakeBias", 0.0)),
            tc_active=float(getattr(p, "tcActive", 0.0)),
            abs_active=float(getattr(p, "absActive", 0.0)),
            pit_limiter_on=bool(getattr(p, "pitLimiterOn", 0)),
            drs_active=bool(getattr(p, "drsActive", 0.0)),
            auto_shifter_on=bool(getattr(p, "autoShifterOn", 0)),
            ai_controlled=bool(getattr(p, "isAIControlled", 0)),
            air_density=float(getattr(p, "airDensity", 0.0)),
            wheel_slip=_wheel_tuple(getattr(p, "wheelSlip", (0.0, 0.0, 0.0, 0.0))),
            slip_ratio=_wheel_tuple(getattr(p, "slipRatio", (0.0, 0.0, 0.0, 0.0))),
            slip_angle=_wheel_tuple(getattr(p, "slipAngle", (0.0, 0.0, 0.0, 0.0))),
            suspension_travel=_wheel_tuple(
                getattr(p, "suspensionTravel", (0.0, 0.0, 0.0, 0.0))
            ),
            tyre_wear=_wheel_tuple(getattr(p, "tyreWear", (0.0, 0.0, 0.0, 0.0))),
        )

    def read_static(self) -> StaticInfo:
        if self._static is None:
            raise GameNotRunning("静态共享内存尚未打开")
        self._static.update()
        s = self._static.data

        # 练习 → 排位 → 正赛会在同一次游戏运行内切换，session 类型来自 graphics 块
        session_type = "unknown"
        if self._gfx is not None:
            try:
                self._gfx.update()
                session_type = session_type_name(getattr(self._gfx.data, "session", -1))
            except Exception:
                session_type = "unknown"

        return StaticInfo(
            track_name=str(s.trackName).strip(),
            track_configuration=str(getattr(s, "trackConfiguration", "")).strip(),
            car_model=str(s.carModel).strip(),
            max_rpm=int(s.maxRpm),
            max_fuel=float(s.maxFuel),
            sector_count=int(s.sectorCount),
            session_type=session_type,
        )
