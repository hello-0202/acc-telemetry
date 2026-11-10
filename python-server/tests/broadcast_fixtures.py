"""合成 ACC 广播 UDP 报文（离线、无真实车手姓名）。

这里是**测试专用**的编码器：按 ``pyACCSharedMemory/acc_udp.py`` 里各 ``read_*``
的字节顺序手工打包，用来验证「解析器 + 适配器」这条链路本身，不需要游戏、不需要网络、
不需要落盘夹具。

刻意用假名字（``TEST DRIVER``），保证任何写进仓库的东西都不含真实车手信息。
"""

from __future__ import annotations

import struct

# InboundMessageTypes
REGISTRATION_RESULT = 1
REALTIME_UPDATE = 2
REALTIME_CAR_UPDATE = 3
ENTRY_LIST = 4
TRACK_DATA = 5
ENTRY_LIST_CAR = 6
BROADCASTING_EVENT = 7


def _s(text: str) -> bytes:
    """[u16 len][bytes]，与 acc_udp.read_string 对应。"""
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def _raw_s(raw: bytes) -> bytes:
    return struct.pack("<H", len(raw)) + raw


def lap(
    *,
    laptime_ms: int = 0,
    car_id: int = 0,
    driver_index: int = 0,
    splits: tuple[int, ...] = (),
    invalid: bool = False,
    valid_for_best: bool = False,
    outlap: bool = False,
    inlap: bool = False,
) -> bytes:
    """UDPLapInfo。注意 splitCount 后面紧跟 splitCount*4 字节的分段历史。"""
    body = struct.pack("<iHH", laptime_ms, car_id, driver_index)
    body += struct.pack("<B", len(splits))
    body += b"".join(struct.pack("<i", value) for value in splits)
    body += bytes([int(invalid), int(valid_for_best), int(outlap), int(inlap)])
    return body


def registration_result(
    connection_id: int = 1,
    *,
    success: bool = True,
    read_only: bool = False,
    error: str = "",
) -> bytes:
    """REGISTRATION_RESULT。

    坑：vendored 解析器把 isReadOnly 读成 ``byte == 0``（与游戏 SDK 的语义相反），
    所以这里 ``read_only=True`` 要写 0。
    """
    return (
        bytes([REGISTRATION_RESULT])
        + struct.pack("<i", connection_id)
        + bytes([int(success), 0 if read_only else 1])
        + _s(error)
    )


def entry_list(connection_id: int = 1, car_count: int = 0) -> bytes:
    return bytes([ENTRY_LIST]) + struct.pack("<iH", connection_id, car_count)


def entry_list_car(
    car_id: int,
    *,
    car_model_type: int = 1,
    team_name: str = "TEST TEAM",
    race_number: int = 100,
    cup_category: int = 0,
    current_driver_index: int = 0,
    nationality: int = 0,
    drivers: tuple[tuple[str, str, str], ...] = (("Test", "Driver", "T.DRIVER"),),
) -> bytes:
    """ENTRY_LIST_CAR。

    解析器在「driverIndex == index」时立即返回，所以后面的车手不会被读完 ——
    这也是 EntryList 要按车手顺序重发的原因。
    """
    body = struct.pack("<H", car_id)
    body += struct.pack("<B", car_model_type)
    body += _s(team_name)
    body += struct.pack("<i", race_number)
    body += struct.pack("<B", cup_category)
    body += struct.pack("<B", current_driver_index)
    body += struct.pack("<H", nationality)
    body += struct.pack("<B", len(drivers))
    for first, last, short in drivers:
        body += _s(first) + _s(last) + _s(short)
        body += struct.pack("<BH", 0, nationality)
    return bytes([ENTRY_LIST_CAR]) + body


def entry_list_car_raw_names(
    car_id: int,
    *,
    drivers: tuple[tuple[bytes, bytes, bytes], ...],
    current_driver_index: int = 0,
) -> bytes:
    """同 ``entry_list_car``，但姓名是裸字节（用来测解码兜底）。"""
    body = struct.pack("<H", car_id)
    body += struct.pack("<B", 1)
    body += _s("TEAM")
    body += struct.pack("<i", 77)
    body += struct.pack("<B", 0)
    body += struct.pack("<B", current_driver_index)
    body += struct.pack("<H", 0)
    body += struct.pack("<B", len(drivers))
    for first, last, short in drivers:
        body += _raw_s(first) + _raw_s(last) + _raw_s(short)
        body += struct.pack("<BH", 0, 0)
    return bytes([ENTRY_LIST_CAR]) + body


def realtime_update(
    *,
    session_index: int = 0,
    # 广播 RaceSessionType：10 = Race（与共享内存枚举不同，别填 2）
    session_type: int = 10,
    session_phase: int = 5,
    session_time: float = 0.0,
    session_end_time: float = 0.0,
    focused_car_id: int = 0,
    is_replay: bool = False,
    track_temp: int = 30,
    ambient_temp: int = 24,
    clouds: float = 0.0,
    rain_level: float = 0.0,
    wetness: float = 0.0,
    best_session_lap_ms: int = 0,
) -> bytes:
    body = struct.pack("<HH", 0, session_index)
    body += bytes([session_type, session_phase])
    body += struct.pack("<ff", session_time, session_end_time)
    body += struct.pack("<i", focused_car_id)
    body += _s("") + _s("") + _s("")
    body += bytes([int(is_replay)])
    if is_replay:
        body += struct.pack("<ff", 0.0, 0.0)
    body += struct.pack("<f", 12.0)
    body += bytes([ambient_temp & 0xFF, track_temp & 0xFF])
    # clouds / rainLevel / wetness 在解析器里是 byte / 10
    body += bytes([int(clouds * 10) & 0xFF, int(rain_level * 10) & 0xFF, int(wetness * 10) & 0xFF])
    body += lap(laptime_ms=best_session_lap_ms)
    return bytes([REALTIME_UPDATE]) + body


def car_update(
    car_id: int,
    *,
    driver_index: int = 0,
    driver_count: int = 1,
    gear: int = 3,
    world_x: float = 0.0,
    world_y: float = 0.0,
    yaw: float = 0.0,
    car_location: int = 1,
    speed_kmh: int = 0,
    position: int = 0,
    cup_position: int = 0,
    track_position: int = 0,
    spline_position: float = 0.0,
    completed_laps: int = 0,
    delta_best: int = 0,
    best_lap_ms: int = 0,
    last_lap_ms: int = 0,
    current_lap_ms: int = 0,
) -> bytes:
    body = struct.pack("<HH", car_id, driver_index)
    body += struct.pack("<B", driver_count)
    # 解析器 gear = byte - 1
    body += struct.pack("<B", (gear + 1) & 0xFF)
    body += struct.pack("<fff", world_x, world_y, yaw)
    body += struct.pack("<B", car_location)
    body += struct.pack("<HHHH", speed_kmh, position, cup_position, track_position)
    body += struct.pack("<f", spline_position)
    body += struct.pack("<H", completed_laps)
    body += struct.pack("<i", delta_best)
    body += lap(laptime_ms=best_lap_ms, car_id=car_id)
    body += lap(laptime_ms=last_lap_ms, car_id=car_id)
    body += lap(laptime_ms=current_lap_ms, car_id=car_id)
    return bytes([REALTIME_CAR_UPDATE]) + body


def track_data(
    *, connection_id: int = 1, track_name: str = "test_track", track_id: int = 7, meters: int = 5793
) -> bytes:
    """TRACK_DATA。

    已用真机报文确认：``trackMeters`` 在 UDP 线上是 int32（Monza 的字节
    ``a1 16 00 00`` = 5793），C# SDK 只是把它暴露成 float。
    """
    return (
        bytes([TRACK_DATA])
        + struct.pack("<i", connection_id)
        + _s(track_name)
        + struct.pack("<ii", track_id, meters)
    )


def car_event(event_type: int = 4, message: str = "accident", timestamp: int = 0, car_id: int = 1) -> bytes:
    return (
        bytes([BROADCASTING_EVENT])
        + bytes([event_type])
        + _s(message)
        + struct.pack("<ii", timestamp, car_id)
    )
