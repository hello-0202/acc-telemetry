"""广播数据源适配器。

**唯一**允许接触 ``UDPCarInfo`` / ``UDPLapInfo`` / ``UDPBroadcastOutput`` 的地方：
把 vendored 解析器（``pyACCSharedMemory.acc_udp``）填好的结构体翻译成领域模型
``StandingsSnapshot`` / ``StandingRow``。

本模块的特性（也都是刻意为之）：

- **纯同步、纯内存**：不建 socket、不起线程、不写 bus、不 import backend。
  谁来喂包、多久喂一次，由上层决定（探针 / 下一轮的广播 Task）。
- **失败不抛**：字段缺失、解不出来、结构体还是默认值，一律退化成 0 / ``None`` / ``""``，
  绝不因为一辆车的脏数据让整份榜单消失。
- **不猜玩家是谁**：玩家身份的真源是共享内存 ``graphics.playerCarID``，
  广播的 ``focusedCarId`` 只是摄像机焦点，切镜头就变（见 ADR-0001 §2.3）。

踩坑提醒：``UDPEntryList.entryListCars`` 是 vendored 代码里的**类级共享 defaultdict**，
同进程所有 ``UDPBroadcastOutput`` 共用同一个字典。所以本类在构造和 ``reset()`` 时都会
清空它 —— **同进程只允许存在一个适配器实例**。
"""

from __future__ import annotations

import struct
import time
from typing import Any

from pyACCSharedMemory.acc_udp import (
    InboundMessageTypes,
    OutboundMessageTypes,
    UDPBroadcastOutput,
    UDPEntryList,
    UDPSessionInfo,
    UDPTrackData,
    parse_udp_stream,
    set_message,
)

from ..domain.model import (
    CAR_LOCATIONS,
    SESSION_PHASES,
    SESSION_TYPES,
    SOURCE_BROADCASTING,
    StandingRow,
    StandingsSnapshot,
)

# 某辆车超过这么久没收到实时更新 → 位置类字段（spline / 坐标 / delta）置空。
# 位置保留（名次是历史结论，不该随刷新率消失），并打上 stale 标记交给上层决定怎么展示。
DEFAULT_STALE_AFTER_S = 5.0

# ACC RaceSessionType（UDP 版）与共享内存的 graphics.session **不是一套枚举**：
# 0=Practice 4=Qualifying 9=Superpole 10=Race 11=Hotlap 12=Hotstint
# 13=HotlapSuperpole 14=Replay。绝不能复用共享内存的 session_type_name()，
# 否则正赛(10)会变成 unknown、排位(4)会变成 timeattack。
_SESSION_TYPE_NAMES = {
    0: "practice",
    4: "qualify",
    9: "superpole",
    10: "race",
    11: "hotlap",
    12: "hotstint",
    13: "hotstintsuperpole",
    14: "unknown",  # Replay 不是实时会话
}

# ACC SessionPhase（UDP 版）：0=NONE 1=STARTING 2=PRE_FORMATION 3=FORMATION_LAP
# 4=PRE_SESSION 5=SESSION 6=SESSION_OVER 7=POST_SESSION 8=RESULT_UI
_SESSION_PHASE_NAMES = {
    0: "none",
    1: "starting",
    2: "pre_formation",
    3: "formation_lap",
    4: "pre_session",
    5: "session",
    6: "session_over",
    7: "post_session",
    8: "result_ui",
}

# ACC CarLocation：0=None 1=Track 2=Pitlane 3=PitEntry 4=PitExit
_CAR_LOCATION_NAMES = {
    0: "unknown",
    1: "track",
    2: "pitlane",
    3: "pit_entry",
    4: "pit_exit",
}

# ACC 用哨兵值表示「暂无成绩」；与 acc_provider._lap_ms 同一口径
_LAP_SENTINELS = (99999999, 2147483647)
# deltaBest 接近 int32 上界的值视为「无」。0 也按「无有效参考圈」处理：
# 社区实测里出场圈 / 尚无有效圈 / 换会话后都是 0，并不代表「正好等于本场最佳」。
# 真机探针会用玩家广播 delta 与共享内存 iDeltaLapTime 对照，若证明 0 合法再放开。
_DELTA_INVALID_MIN = 2_000_000_000

# 报文固定前缀：[u8 messageType][u16 carId] —— 协议定义，位置固定。
# 这是本模块唯一直接读原始字节的地方，目的是把「哪个 carId 刚被刷新」记下来做新鲜度。
_CAR_ID_PREFIX = struct.Struct("<H")

# TRACK_DATA 的真实布局： [u8 type][i32 connectionId][u16 nameLen][name]
#                           [i32 trackId][i32 trackMeters][相机/HUD 列表...]
# 已用真机报文确认：Monza 的 trackMeters 字节是 a1 16 00 00 = 5793（int32）。
# Kunos C# SDK 把它暴露成 float，但 UDP 线上是整数；vendored 解析器按 int32 读是对的。
# 这里仍从原始报文独立解码，方便做范围校验，也不依赖 c_float 容器。
_TRACK_DATA_HEADER = struct.Struct("<iH")
_TRACK_METERS_RANGE = (500.0, 50_000.0)  # ACC 赛道长度的合理范围，超范围视为脏数据


def _lap_ms(value: Any) -> int:
    """过滤 ACC 用哨兵值表示的「暂无成绩」。"""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return 0
    if ms <= 0 or ms in _LAP_SENTINELS:
        return 0
    return ms


def _delta_ms(value: Any) -> int | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    # 0 = 还没有有效的参考圈（不是「正好等于最佳」）
    if ms == 0 or ms <= -_DELTA_INVALID_MIN or ms >= _DELTA_INVALID_MIN or ms in _LAP_SENTINELS:
        return None
    return ms


def session_type_name(raw: Any) -> str:
    """广播 RaceSessionType → 语义字符串（与共享内存枚举不同，见上方映射表）。"""
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    return _SESSION_TYPE_NAMES.get(code, "unknown")


def _track_meters_from_payload(payload: bytes) -> float | None:
    """从原始 TRACK_DATA 报文按 int32 解出赛道长度（米），失败/脏数据返回 None。"""
    try:
        _, name_len = _TRACK_DATA_HEADER.unpack_from(payload, 1)
        offset = 1 + _TRACK_DATA_HEADER.size + int(name_len) + 4  # 跳过 trackId
        (meters,) = struct.unpack_from("<i", payload, offset)
    except (struct.error, TypeError, ValueError, BufferError):
        return None
    if not (_TRACK_METERS_RANGE[0] <= meters <= _TRACK_METERS_RANGE[1]):
        return None
    return float(meters)


def decode_text(raw: Any) -> str:
    """EntryList 里的裸字节 → str，永不抛。

    多数客户端写 UTF-8；混排 / 老版本可能是 Latin-1 甚至半截多字节。解不出来就给空串，
    一个车手名不能拖垮整份榜单。
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    try:
        data = bytes(raw)
    except (TypeError, ValueError):
        return ""
    data = data.split(b"\x00", 1)[0]
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return ""


def driver_display_name(info: Any) -> str:
    """优先 "First Last"，其次 shortName，最后空串。"""
    driver = getattr(info, "currentDriverInfo", None)
    if driver is None:
        return ""
    first = decode_text(getattr(driver, "firstName", b""))
    last = decode_text(getattr(driver, "lastName", b""))
    full = " ".join(part for part in (first, last) if part).strip()
    if full:
        return full
    return decode_text(getattr(driver, "shortName", b""))


def car_location_name(raw: Any, *, in_garage: bool = False) -> str:
    """把 carLocation 映射成语义字符串。

    车库判定只用 ``carLocation == 0 且 inGarage``：vendored 解析器的 ``inGarage`` 是
    「进站 + 位移 > 20m」的启发式，一辆车的**第一帧**上一位置默认 (0,0)，停在远离原点的
    维修区就会被误判成瞬移 → 所以不能让它覆盖明确的 carLocation。
    """
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    name = _CAR_LOCATION_NAMES.get(code, "unknown")
    if name == "unknown" and in_garage:
        return "garage"
    return name


def session_phase_name(raw: Any) -> str:
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    return _SESSION_PHASE_NAMES.get(code, "unknown")


def _sort_key(row: StandingRow) -> tuple[int, int, int]:
    """官方名次优先；没有名次的按赛道位置跟在后面；都没有的按 carId 排在最后。"""
    if row.position > 0:
        return (0, row.position, row.car_id)
    if row.track_position > 0:
        return (1, row.track_position, row.car_id)
    return (2, 0, row.car_id)


class BroadcastAdapter:
    """vendored UDP 结构体 → ``StandingsSnapshot``。

    用法::

        adapter = BroadcastAdapter()
        adapter.feed(payload)              # 每收到一个 UDP 包喂一次
        snap = adapter.snapshot()          # 上层要的时候再组装

    换会话 / 重连时必须 ``adapter.reset()``。
    """

    def __init__(self, *, stale_after_s: float = DEFAULT_STALE_AFTER_S) -> None:
        self._stale_after_s = float(stale_after_s)
        self._out = UDPBroadcastOutput()
        self._live_at: dict[int, float] = {}
        # 注册结果是**连接级**信息：会话 reset() 不能清掉它
        self._registration_seen = False
        self.packets = 0
        self.message_counts: dict[int, int] = {}
        self.last_packet_at: float | None = None
        self.reset()

    # ------------------------------------------------------------------ 状态
    @property
    def raw(self) -> UDPBroadcastOutput:
        """原始结构体，**仅供诊断**（探针 / 排查日志）。

        业务代码不要碰：一旦上层读 ``carCoordinates`` / ``worldPosX`` 这类字段，
        ADR-0001 §2.1 的「ACC 词汇止步于适配器」就破了。
        """
        return self._out

    def reset(self) -> None:
        """清空累积状态（换会话 / 重连 / 测试之间都必须调）。

        ``entryListCars`` 是类级共享字典，不显式 clear 的话下一次会话会直接继承上一次的
        车辆列表 —— 这是 vendored 代码里最容易被忽略的坑。
        连统计一起清：一份「本次连接」的观测值，跨会话累计没有意义。
        """
        UDPEntryList.entryListCars.clear()
        self._out.entryList.carEntryCount = 0
        self._out.entryList.syncEntryList = False
        # 换会话 / 换赛道后，旧的 TrackData / SessionInfo 不能继续当当前值。
        # 重置成默认结构后再把会话枚举置 -1，避免「还没收到 REALTIME_UPDATE 就报 practice」。
        # registration 是连接级信息，保留；重连会新建适配器。
        self._out.trackData = UDPTrackData()
        self._out.sessionInfo = UDPSessionInfo()
        self._out.sessionInfo.sessionType = -1
        self._out.sessionInfo.sessionPhase = -1
        self._out.sessionInfo.sessionIndex = -1
        self._track_meters_value = None
        self._live_at.clear()
        self.packets = 0
        self.message_counts.clear()
        self.last_packet_at = None

    @property
    def connection_id(self) -> int:
        return int(self._out.registration.connectionId)

    @property
    def registration_seen(self) -> bool:
        """是否已经收到过 REGISTRATION_RESULT（成功或失败都算）。

        失败时 ``errorMessage`` 可能是空串，所以不能用「成功 / 有错误文本」判断
        注册阶段是否结束，否则会把「注册被拒」误判成超时。
        """
        return self._registration_seen

    @property
    def registration_succeeded(self) -> bool:
        return bool(self._out.registration.connectionSuccess)

    @property
    def is_read_only(self) -> bool:
        return bool(self._out.registration.isReadOnly)

    @property
    def registration_error(self) -> str:
        return decode_text(self._out.registration.errorMessage)

    @property
    def cars_expected(self) -> int:
        return int(self._out.entryList.carEntryCount)

    @property
    def cars_known(self) -> int:
        """真正拿到名册信息的车数（entryId == carId 且 carId > 0）。"""
        known = 0
        for car_id, info in self._out.entryList.entryListCars.items():
            if int(car_id) > 0 and int(info.entryId) == int(car_id):
                known += 1
        return known

    @property
    def entry_list_complete(self) -> bool:
        expected = self.cars_expected
        return expected > 0 and self.cars_known >= expected

    @property
    def needs_entry_list_sync(self) -> bool:
        """vendored 解析器发现名册过期（driverCount / driverIndex / entryId 不一致）。"""
        return bool(self._out.entryList.syncEntryList)

    def entry_list_synced(self) -> None:
        self._out.entryList.syncEntryList = False

    @property
    def session_index(self) -> int:
        return int(self._out.sessionInfo.sessionIndex)

    @property
    def focused_car_id(self) -> int:
        """摄像机焦点车。**不要**拿它判断玩家（见模块 docstring）。"""
        return int(self._out.sessionInfo.focusedCarId)

    @property
    def track_name(self) -> str:
        return decode_text(self._out.trackData.trackName)

    @property
    def session_type(self) -> str:
        """语义化会话类型（词表见 ``domain.model.SESSION_TYPES``）。"""
        return self._session_type()

    # ------------------------------------------------------- 上行请求报文构造
    def entry_list_request(self) -> bytes | None:
        if self.connection_id <= 0:
            return None
        return set_message(OutboundMessageTypes.REQUEST_ENTRY_LIST, self.connection_id)

    def track_data_request(self) -> bytes | None:
        if self.connection_id <= 0:
            return None
        return set_message(OutboundMessageTypes.REQUEST_TRACK_DATA, self.connection_id)

    # ------------------------------------------------------------------ 喂包
    def feed(self, payload: bytes, *, now: float | None = None) -> int:
        """喂一个 UDP 包，返回解析出的消息类型（无法识别时 ``-1``）。"""
        if now is None:
            now = time.monotonic()
        message_type = parse_udp_stream(payload, self._out)
        self.packets += 1
        self.last_packet_at = now
        if message_type >= 0:
            self.message_counts[message_type] = self.message_counts.get(message_type, 0) + 1
        if message_type == InboundMessageTypes.REGISTRATION_RESULT:
            self._registration_seen = True
        if message_type == InboundMessageTypes.TRACK_DATA:
            # vendored 解析器把 float 读成了 int，这里用原始报文纠正。
            meters = _track_meters_from_payload(payload)
            if meters is not None:
                self._track_meters_value = meters
        if message_type in (InboundMessageTypes.REALTIME_CAR_UPDATE,):
            car_id = _payload_car_id(payload)
            if car_id is not None:
                self._live_at[car_id] = now
        return message_type

    def last_update_at(self, car_id: int) -> float | None:
        return self._live_at.get(int(car_id))

    # ------------------------------------------------------------------ 组装
    def snapshot(self, *, now: float | None = None) -> StandingsSnapshot:
        if now is None:
            now = time.monotonic()
        # 只收合法 carId：BROADCASTING_EVENT 的解析可能给 defaultdict 塞入 0 / 超大 key。
        cars = [
            (int(car_id), info)
            for car_id, info in self._out.entryList.entryListCars.items()
            if int(car_id) > 0
        ]
        rows = [self._row(car_id, info, now) for car_id, info in cars]
        rows.sort(key=_sort_key)
        return StandingsSnapshot(
            received_at=now,
            source=SOURCE_BROADCASTING,
            track_meters=self._track_meters(),
            session_type=self.session_type,
            session_phase=session_phase_name(self._out.sessionInfo.sessionPhase),
            rows=tuple(rows),
            session_index=self.session_index,
            cars_expected=self.cars_expected,
            entry_list_complete=self.entry_list_complete,
        )

    def _track_meters(self) -> float | None:
        # 只认 feed() 从原始报文按 float 解出来的值；raw.trackData.trackMeters 是
        # vendored 解析器按 int32 读出来的脏值，仅供诊断。
        return self._track_meters_value

    @property
    def track_meters(self) -> float | None:
        """赛道长度（米），未知为 None。"""
        return self._track_meters_value

    def _session_type(self) -> str:
        name = session_type_name(self._out.sessionInfo.sessionType)
        return name if name in SESSION_TYPES else "unknown"

    def _row(self, car_id: int, info: Any, now: float) -> StandingRow:
        live_at = self._live_at.get(car_id)
        fresh = live_at is not None and (now - live_at) <= self._stale_after_s

        spline: float | None = None
        world_x: float | None = None
        world_y: float | None = None
        delta_best: int | None = None
        if fresh:
            try:
                spline = float(info.splinePosition)
            except (TypeError, ValueError):
                spline = None
            try:
                wx, wy = float(info.worldPosX), float(info.worldPosY)
            except (TypeError, ValueError):
                wx, wy = 0.0, 0.0
            if wx or wy:
                world_x, world_y = wx, wy
            delta_best = _delta_ms(getattr(info, "deltaBest", 0))

        race_number = _int_or_none(getattr(info, "raceNumber", 0))
        team_name = decode_text(getattr(info, "teamName", b"")) or None
        location = car_location_name(
            getattr(info, "carLocation", 0), in_garage=bool(getattr(info, "inGarage", False))
        )
        return StandingRow(
            car_id=car_id,
            race_number=race_number,
            driver_name=driver_display_name(info),
            team_name=team_name,
            position=_non_negative(getattr(info, "position", 0)),
            cup_position=_non_negative(getattr(info, "cupPosition", 0)),
            track_position=_non_negative(getattr(info, "trackPosition", 0)),
            spline_position=spline,
            completed_laps=_non_negative(getattr(info, "completedLaps", 0)),
            last_lap_ms=_lap_ms(getattr(getattr(info, "lastLap", None), "laptimeMS", 0)),
            best_lap_ms=_lap_ms(getattr(getattr(info, "bestSessionLap", None), "laptimeMS", 0)),
            current_lap_ms=_lap_ms(getattr(getattr(info, "currentLap", None), "laptimeMS", 0)),
            delta_best_ms=delta_best,
            car_location=location,
            world_x=world_x,
            world_y=world_y,
            speed_kmh=_non_negative(getattr(info, "speedKmh", 0)),
            in_pit=bool(getattr(info, "inPitLane", False)),
            stale=not fresh,
        )


def _payload_car_id(payload: bytes) -> int | None:
    try:
        (car_id,) = _CAR_ID_PREFIX.unpack_from(payload, 1)
    except (struct.error, TypeError, BufferError):
        return None
    return int(car_id)


def _non_negative(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# 词表校验：映射表产出的值必须都在领域层声明的词表里（改词表时这里会第一时间炸）
assert set(_CAR_LOCATION_NAMES.values()) <= set(CAR_LOCATIONS), "carLocation 映射表与领域层词表不一致"
assert set(_SESSION_PHASE_NAMES.values()) <= set(SESSION_PHASES), "sessionPhase 映射表与领域层词表不一致"
assert set(_SESSION_TYPE_NAMES.values()) <= set(SESSION_TYPES), "sessionType 映射表与领域层词表不一致"
