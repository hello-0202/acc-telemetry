"""
Python mapping of ACC's Broadcasting Network Protocol (UDP)

Based on ACC Broadcasting SDK, found in game's "sdk\\broadcasting" folder.
"""

from __future__ import annotations

import ctypes
import io
import logging
import math
import os
import socket
import struct
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Callable, Sequence

from ._common import _t, get_root_logger_name, typedstruct

logger = logging.getLogger(get_root_logger_name())


# Constants
class OutboundMessageTypes:
    """Outbound message types"""

    REGISTER_COMMAND_APPLICATION = 1
    UNREGISTER_COMMAND_APPLICATION = 9
    REQUEST_ENTRY_LIST = 10
    REQUEST_TRACK_DATA = 11
    CHANGE_HUD_PAGE = 49
    CHANGE_FOCUS = 50
    INSTANT_REPLAY_REQUEST = 51
    PLAY_MANUAL_REPLAY_HIGHLIGHT = 52
    SAVE_MANUAL_REPLAY_HIGHLIGHT = 60


class InboundMessageTypes:
    """Inbound message types"""

    NONE = -1
    REGISTRATION_RESULT = 1
    REALTIME_UPDATE = 2
    REALTIME_CAR_UPDATE = 3
    ENTRY_LIST = 4
    ENTRY_LIST_CAR = 6
    TRACK_DATA = 5
    BROADCASTING_EVENT = 7


class BroadcastingNetworkProtocol:
    """Broadcasting network protocol"""

    BROADCASTING_PROTOCOL_VERSION = 4
    BUFFER_SIZE = 4096  # 2 ** 14


# UDP API data
@typedstruct(pack=4)
class UDPDriverInfo(ctypes.Structure):
    """Driver info

    Attributes:
        firstName: first name
        lastName: last name
        shortName: short name
        category: Platinum = 3, Gold = 2, Silver = 1, Bronze = 0
        nationality: nationality, see NationalityEnum enum
    """

    __slots__ = ()

    firstName: bytes = _t(ctypes.c_char * 32)
    lastName: bytes = _t(ctypes.c_char * 32)
    shortName: bytes = _t(ctypes.c_char * 32)
    category: int = _t(ctypes.c_byte)
    nationality: int = _t(ctypes.c_int16)


@typedstruct(pack=4)
class UDPLapInfo(ctypes.Structure):
    """Lap info

    Attributes:
        laptimeMS: lap time in milliseconds
        carId: car ID
        driverIndex: driver index
        splitCount: number of lap time records
        isInvalid: is invalid lap time
        isValidForBest: is valid for best lap time
        isOutlap: is out lap
        isInlap: is in lap
        lapType: lap type, 0=error, 1=out lap, 2=regular, 3=in lap, see LapType enum
    """

    __slots__ = ()

    laptimeMS: int = _t(ctypes.c_int32)
    carId: int = _t(ctypes.c_int16)
    driverIndex: int = _t(ctypes.c_int16)
    splitCount: int = _t(ctypes.c_byte)
    # splits: list[int] = _t(ctypes.c_int32 * 1000)  # (unmapped) list of lap time records
    isInvalid: bool = _t(ctypes.c_bool)
    isValidForBest: bool = _t(ctypes.c_bool)
    isOutlap: bool = _t(ctypes.c_bool)
    isInlap: bool = _t(ctypes.c_bool)
    lapType: int = _t(ctypes.c_byte)


@typedstruct(pack=4)
class UDPCarInfo(ctypes.Structure):
    """Car info

    Attributes:
        entryId: car ID from ENTRY_LIST_CAR, which matches carId from sharedmemory API
        carId: car ID from REALTIME_CAR_UPDATE, which matches carId from sharedmemory API
        carModelType: car model type
        teamName: team name
        raceNumber: race number
        cupCategory: Cup: Overall/Pro = 0, ProAm = 1, Am = 2, Silver = 3, National = 4, see CupCategory enum
        currentDriverIndex: current driver index (in team)
        currentDriverInfo: current driver info from this car (team)
        driverCount: number of drivers in team for this car (shared with REALTIME_CAR_UPDATE)
        nationality: nationality
        driverIndex: who is driving in team, driver swap will make this change
        gear: -1=reverse, 0=neutral, 1+=forward
        worldPosX: world position X
        worldPosY: world position Y
        yaw: yaw angle in radians (slow)
        carLocation: 0=None, 1=Track, 2=Pitlane, 3=PitEntry, 4=PitExit, see CarLocation enum
        speedKmh: speed in kilometers per hour
        position: official P/Q/R position (1 based)
        cupPosition: official P/Q/R position (1 based)
        trackPosition: position on track (1 based)
        splinePosition: track position between 0.0 and 1.0
        completedLaps: number of completed laps
        deltaBest: realtime delta to best session lap
        bestSessionLap: session best lap data
        lastLap: last lap time data
        currentLap: current lap time data
        inPitLane: whether in pit lane
        inGarage: whether in garage stall
        finished: whether finished final lap
        eventType: car event type, 0=none, 1=green flag, 2=session over, 3=penalty message, 4=accident, 5=lap completed, 6=Best Session Lap, 7=best personal lap, see BroadcastingCarEventType enum
        eventMessage: car event message text
        eventTimestamp: car event timestamp
        lastPitState: last in pit lane state (carLocation == 2)
        trackCuts: number of track cuts (unofficial)
        accidents: number of accidents (eventType == 4)
        pitStops: number of pit stops
    """

    __slots__ = ()

    entryId: int = _t(ctypes.c_int16)
    carId: int = _t(ctypes.c_int16)
    carModelType: int = _t(ctypes.c_byte)
    teamName: bytes = _t(ctypes.c_char * 64)
    raceNumber: int = _t(ctypes.c_int32)
    cupCategory: int = _t(ctypes.c_byte)
    currentDriverIndex: int = _t(ctypes.c_byte)
    currentDriverInfo: UDPDriverInfo = _t(UDPDriverInfo)
    driverCount: int = _t(ctypes.c_byte)
    #drivers: list of driver info from this car (team)
    #drivers: list[UDPDriverInfo] = _t(UDPDriverInfo * 10)
    nationality: int = _t(ctypes.c_int16)
    # REALTIME_CAR_UPDATE = 3
    driverIndex: int = _t(ctypes.c_int16)
    gear: int = _t(ctypes.c_byte)
    worldPosX: float = _t(ctypes.c_float)
    worldPosY: float = _t(ctypes.c_float)
    yaw: float = _t(ctypes.c_float)
    carLocation: int = _t(ctypes.c_byte)
    speedKmh: int = _t(ctypes.c_int16)
    position: int = _t(ctypes.c_int16)
    cupPosition: int = _t(ctypes.c_int16)
    trackPosition: int = _t(ctypes.c_int16)
    splinePosition: float = _t(ctypes.c_float)
    completedLaps: int = _t(ctypes.c_int16)
    deltaBest: int = _t(ctypes.c_int32)
    bestSessionLap: UDPLapInfo = _t(UDPLapInfo)
    lastLap: UDPLapInfo = _t(UDPLapInfo)
    currentLap: UDPLapInfo = _t(UDPLapInfo)
    # Extra
    inPitLane: bool = _t(ctypes.c_bool)
    inGarage: bool = _t(ctypes.c_bool)
    finished: bool = _t(ctypes.c_bool)
    # BROADCASTING_EVENT = 7
    eventType: int = _t(ctypes.c_byte)
    eventMessage: bytes = _t(ctypes.c_char * 64)
    eventTimestamp: int = _t(ctypes.c_int32)
    # Counter
    lastPitState: int = _t(ctypes.c_byte)
    trackCuts: int = _t(ctypes.c_int16)
    accidents: int = _t(ctypes.c_int16)
    pitStops: int = _t(ctypes.c_int16)


@typedstruct(pack=4)
class UDPTrackData(ctypes.Structure):
    """TRACK_DATA = 5

    Attributes:
        connectionId: connection ID
        trackName: track name
        trackId: track ID
        trackMeters: track length in meters
    """

    __slots__ = ()

    connectionId: int = _t(ctypes.c_int)
    trackName: bytes = _t(ctypes.c_char * 64)
    trackId: int = _t(ctypes.c_byte)
    trackMeters: int = _t(ctypes.c_float)
    # (unmapped) CameraSets
    # (unmapped) HUDPages


@typedstruct(pack=4)
class UDPRegistrationResult(ctypes.Structure):
    """REGISTRATION_RESULT = 1

    Attributes:
        connectionId: connection ID
        connectionSuccess: whether connection success
        isReadOnly: is read only connection
        errorMessage: error message
    """

    __slots__ = ()

    connectionId: int = _t(ctypes.c_int)
    connectionSuccess: bool = _t(ctypes.c_bool)
    isReadOnly: bool = _t(ctypes.c_bool)
    errorMessage: bytes = _t(ctypes.c_char * 64)


@typedstruct(pack=4)
class UDPEntryList(ctypes.Structure):
    """ENTRY_LIST = 4, ENTRY_LIST_CAR = 6

    Attributes:
        entryListCars: car info dataset stored in dict (key=carId), use entryListCars.clear() to cleanup data after session ends
        connectionId: connection ID
        carEntryCount: car entry count
        leaderFinished: whether leader finished (crossed line)
        syncEntryList: whether to sync entry list
    """

    __slots__ = ()

    entryListCars = defaultdict(UDPCarInfo)
    connectionId: int = _t(ctypes.c_int32)
    carEntryCount: int = _t(ctypes.c_int16)
    leaderFinished: bool = _t(ctypes.c_bool)
    syncEntryList: bool = _t(ctypes.c_bool)


@typedstruct(pack=4)
class UDPSessionInfo(ctypes.Structure):
    """REALTIME_UPDATE = 2

    Attributes:
        eventIndex: event index, see BroadcastingCarEventType enum
        sessionIndex: session index
        sessionType: session type, see RaceSessionType enum
        sessionPhase: session phase, see SessionPhase enum
        sessionTime: session time (elapsed) (ms)
        sessionEndTime: session end (remaining) time (ms)
        focusedCarId: focused car ID
        activeCameraSet: active camera set name
        activeCamera: active camera name
        currentHudPage: current hud page
        isReplayPlaying: is replay playing
        replaySessionTime: replay session time
        replayRemainingTime: replay remaining time
        timeOfDay: time of day
        ambientTemp: ambient temperature
        trackTemp: track temperature
        clouds: clouds
        rainLevel: rain level
        wetness: track wetness
        bestSessionLap: session best lap data
    """

    __slots__ = ()

    eventIndex: int = _t(ctypes.c_int16)
    sessionIndex: int = _t(ctypes.c_int16)
    sessionType: int = _t(ctypes.c_byte)
    sessionPhase: int = _t(ctypes.c_byte)
    sessionTime: float = _t(ctypes.c_float)
    sessionEndTime: float = _t(ctypes.c_float)
    focusedCarId: int = _t(ctypes.c_int32)
    activeCameraSet: bytes = _t(ctypes.c_char * 32)
    activeCamera: bytes = _t(ctypes.c_char * 32)
    currentHudPage: bytes = _t(ctypes.c_char * 32)
    isReplayPlaying: bool = _t(ctypes.c_bool)
    replaySessionTime: float = _t(ctypes.c_float)
    replayRemainingTime: float = _t(ctypes.c_float)
    timeOfDay: float = _t(ctypes.c_float)
    ambientTemp: int = _t(ctypes.c_byte)
    trackTemp: int = _t(ctypes.c_byte)
    clouds: float = _t(ctypes.c_float)
    rainLevel: float = _t(ctypes.c_float)
    wetness: float = _t(ctypes.c_float)
    bestSessionLap: UDPLapInfo = _t(UDPLapInfo)


@typedstruct(pack=4)
class UDPBroadcastOutput(ctypes.Structure):
    """Broadcast data output

    Attributes:
        registration: registration result info
        sessionInfo: session info
        entryList: car entry list
        trackData: track data
    """

    __slots__ = ()

    registration: UDPRegistrationResult = _t(UDPRegistrationResult)
    sessionInfo: UDPSessionInfo = _t(UDPSessionInfo)
    entryList: UDPEntryList = _t(UDPEntryList)
    trackData: UDPTrackData = _t(UDPTrackData)

    def __del__(self):
        logger.info("UDP: GC: UDPBroadcastOutput")

    @classmethod
    def size(cls) -> int:
        """Return data structure size"""
        return ctypes.sizeof(cls)


# Function
bytes_to_int = (
    lambda func=int.from_bytes:  # assign func locally to reduce lookups (~30% faster)
    lambda bytes: func(bytes, "little")
)()
bytes_to_float = (
    lambda func=struct.unpack:
    lambda bytes: func("<f", bytes)[0]
)()


def write_string(string: str, data: bytearray):
    """Write string to data bytes"""
    bytestring = string.encode()
    data.extend(len(bytestring).to_bytes(2, "little"))
    data.extend(bytestring)


def read_string(stream_reader: Callable[[int], bytes], size: int) -> bytes:
    """Read string to data bytes"""
    return stream_reader(bytes_to_int(stream_reader(size)))


# Set message
def set_register_message(
    display_name: str = "",
    connection_password: str = "",
    command_password: str = "",
    realtime_update_interval: int = 250,
    register_command_application: int = OutboundMessageTypes.REGISTER_COMMAND_APPLICATION,
    broadcasting_protocol_version: int = BroadcastingNetworkProtocol.BROADCASTING_PROTOCOL_VERSION,
) -> bytearray:
    """Set message for registering connection for current client

    Args:
        display_name: display name (optional).
        connection_password: connection password (optional) matches broadcasting.json 'connectionPassword' value; wrong password will result connection failure.
        command_password: command password (optional) matches broadcasting.json 'command_password' value; wrong password will grant read-only access.
        realtime_update_interval: UDP data realtime update interval (milliseconds).
    """
    message = bytearray()
    message.extend(register_command_application.to_bytes(1, "little"))
    message.extend(broadcasting_protocol_version.to_bytes(1, "little"))
    write_string(display_name, message)
    write_string(connection_password, message)
    message.extend(int(max(realtime_update_interval, 10)).to_bytes(4, "little"))
    write_string(command_password, message)
    return message


def set_message(message_type: int, connection_id: int) -> bytes:
    """Set message: message type, connection id"""
    # Connection ID can be negative (ex. -1 returned by API), use signed int
    return struct.pack("<bi", message_type, connection_id)


# Read stream
def read_registration_result(stream_reader: Callable[[int], bytes], output: UDPRegistrationResult):
    """Read stream - registration result"""
    output.connectionId = bytes_to_int(stream_reader(4))  # Int32
    output.connectionSuccess = bytes_to_int(stream_reader(1)) > 0  # byte
    output.isReadOnly = bytes_to_int(stream_reader(1)) == 0  # byte
    output.errorMessage = read_string(stream_reader, 2)[:64]  # bytestring


def read_lap_info(stream_reader: Callable[[int], bytes], output: UDPLapInfo):
    """Read stream - lap info"""
    output.laptimeMS = bytes_to_int(stream_reader(4))  # Int32
    output.carId = bytes_to_int(stream_reader(2))  # UInt16
    output.driverIndex = bytes_to_int(stream_reader(2))  # UInt16
    output.splitCount = bytes_to_int(stream_reader(1))  # byte
    stream_reader(output.splitCount * 4)  # skip lap history (save memory)
    #for i in range(min(output.splitCount, 1000)):
    #    output.splits[i] = bytes_to_int(stream_reader(4))  # list Int32
    output.isInvalid = bytes_to_int(stream_reader(1)) > 0  # bool
    output.isValidForBest = bytes_to_int(stream_reader(1)) > 0  # bool
    output.isOutlap = bytes_to_int(stream_reader(1)) > 0  # bool
    output.isInlap = bytes_to_int(stream_reader(1)) > 0  # bool
    if output.isOutlap:
        output.lapType = 1
    elif output.isInlap:
        output.lapType = 3
    else:
        output.lapType = 2


def read_realtime_update(stream_reader: Callable[[int], bytes], output: UDPSessionInfo):
    """Read stream - realtime update"""
    output.eventIndex = bytes_to_int(stream_reader(2))  # UInt16
    output.sessionIndex = bytes_to_int(stream_reader(2))  # UInt16
    output.sessionType = bytes_to_int(stream_reader(1))  # byte
    output.sessionPhase = bytes_to_int(stream_reader(1))  # byte
    output.sessionTime = bytes_to_float(stream_reader(4))  # float
    output.sessionEndTime = bytes_to_float(stream_reader(4))  # float
    output.focusedCarId = bytes_to_int(stream_reader(4))  # Int32
    output.activeCameraSet = read_string(stream_reader, 2)  # bytestring
    output.activeCamera = read_string(stream_reader, 2)  # bytestring
    output.currentHudPage = read_string(stream_reader, 2)  # bytestring
    output.isReplayPlaying = bytes_to_int(stream_reader(1)) > 0  # byte
    if output.isReplayPlaying:
        output.replaySessionTime = bytes_to_float(stream_reader(4))  # float
        output.replayRemainingTime = bytes_to_float(stream_reader(4))  # float
    output.timeOfDay = bytes_to_float(stream_reader(4))  # float
    output.ambientTemp = bytes_to_int(stream_reader(1))  # byte
    output.trackTemp = bytes_to_int(stream_reader(1))  # byte
    output.clouds = bytes_to_int(stream_reader(1)) / 10.0  # byte to float
    output.rainLevel = bytes_to_int(stream_reader(1)) / 10.0  # byte to float
    output.wetness = bytes_to_int(stream_reader(1)) / 10.0  # byte to float
    read_lap_info(stream_reader, output.bestSessionLap)


def read_realtime_car_update(stream_reader: Callable[[int], bytes], output: UDPEntryList, session_ended: bool):
    """Read stream - realtime car update"""
    car_id = bytes_to_int(stream_reader(2))  # UInt16
    driver_index = bytes_to_int(stream_reader(2))  # UInt16
    driver_count = bytes_to_int(stream_reader(1))  # byte
    car_info = output.entryListCars[car_id]

    last_world_pos_x = car_info.worldPosX
    last_world_pos_y = car_info.worldPosY
    last_completed_laps = car_info.completedLaps
    last_lap_invalid = car_info.currentLap.isInvalid

    # Update realtime car info
    car_info.carId = car_id
    car_info.driverIndex = driver_index
    car_info.driverCount = driver_count
    car_info.gear = bytes_to_int(stream_reader(1)) - 1  # byte
    car_info.worldPosX = bytes_to_float(stream_reader(4))  # float
    car_info.worldPosY = bytes_to_float(stream_reader(4))  # float
    car_info.yaw = bytes_to_float(stream_reader(4))  # float
    car_info.carLocation = bytes_to_int(stream_reader(1))  # byte
    car_info.speedKmh = bytes_to_int(stream_reader(2))  # UInt16
    car_info.position = bytes_to_int(stream_reader(2))  # UInt16
    car_info.cupPosition = bytes_to_int(stream_reader(2))  # UInt16
    car_info.trackPosition = bytes_to_int(stream_reader(2))  # UInt16
    car_info.splinePosition = bytes_to_float(stream_reader(4))  # float
    car_info.completedLaps = bytes_to_int(stream_reader(2))  # UInt16
    car_info.deltaBest = bytes_to_int(stream_reader(4))  # Int32
    car_info.inPitLane = (car_info.carLocation == 2)

    read_lap_info(stream_reader, car_info.bestSessionLap)
    read_lap_info(stream_reader, car_info.lastLap)
    read_lap_info(stream_reader, car_info.currentLap)

    # Count pit stops
    if not car_info.inPitLane:
        car_info.lastPitState = car_info.carLocation
    elif 1 > car_info.speedKmh and car_info.carLocation != car_info.lastPitState != 0:
        car_info.pitStops += 1
        car_info.lastPitState = car_info.carLocation

    # Pit & garage state check
    if car_info.inGarage:
        if car_info.gear >= 1 < car_info.speedKmh:
            car_info.inGarage = False
    elif car_info.inPitLane and math.dist(
        (last_world_pos_x, last_world_pos_y), (car_info.worldPosX, car_info.worldPosY)
    ) > 20:  # detect vehicle teleport distance (meters)
        car_info.inGarage = True

    # Finish check
    if session_ended:
        # check if leader finished final lap
        if not output.leaderFinished:
            if car_info.position == 1 and car_info.completedLaps - last_completed_laps == 1:
                output.leaderFinished = True
                car_info.finished = True
        # Check if other driver finished after leader crossed line
        elif not car_info.finished and car_info.completedLaps - last_completed_laps == 1:
            car_info.finished = True

    # Count track cuts
    if last_lap_invalid != car_info.currentLap.isInvalid == True:
        car_info.trackCuts += 1

    # Check if entry list outdated
    if (
        car_info.entryId != car_id
        or car_info.driverCount != driver_count
        # Driver index is only sync after the first lap (after out lap)
        # Only send request at beginning of new lap
        or (
            car_info.currentDriverIndex != driver_index
            and car_info.currentLap.laptimeMS < 1000  # < 1 second of new lap
        )
    ):
        output.syncEntryList = True


def read_entry_list(stream_reader: Callable[[int], bytes], output: UDPEntryList):
    """Read stream - entry list"""
    output.connectionId = bytes_to_int(stream_reader(4))  # Int32
    output.carEntryCount = bytes_to_int(stream_reader(2))  # UInt16


def read_entry_list_car(stream_reader: Callable[[int], bytes], output: UDPEntryList):
    """Read stream - entry list car info"""
    car_id = bytes_to_int(stream_reader(2))  # UInt16
    car_info = output.entryListCars[car_id]
    car_info.entryId = car_id
    car_info.carModelType = bytes_to_int(stream_reader(1))  # byte
    car_info.teamName = read_string(stream_reader, 2)  # bytestring
    car_info.raceNumber = bytes_to_int(stream_reader(4))  # Int32
    car_info.cupCategory = bytes_to_int(stream_reader(1))  # byte
    car_info.currentDriverIndex = bytes_to_int(stream_reader(1))  # byte
    car_info.nationality = bytes_to_int(stream_reader(2))  # UInt16
    car_info.driverCount = bytes_to_int(stream_reader(1))  # byte
    for index in range(car_info.driverCount):
        first_name = read_string(stream_reader, 2)  # bytestring
        if first_name == b'':  # detected EOL
            return
        last_name = read_string(stream_reader, 2)  # bytestring
        short_name = read_string(stream_reader, 2)  # bytestring
        category = bytes_to_int(stream_reader(1))  # byte
        nationality = bytes_to_int(stream_reader(2))  # UInt16
        if car_info.driverIndex == index:
            current_driver = car_info.currentDriverInfo
            current_driver.firstName = first_name
            current_driver.lastName = last_name
            current_driver.shortName = short_name
            current_driver.category = category
            current_driver.nationality = nationality
            return


def read_track_data(stream_reader: Callable[[int], bytes], output: UDPTrackData):
    """Read stream - track data"""
    output.connectionId = bytes_to_int(stream_reader(4))  # Int32
    output.trackName = read_string(stream_reader, 2)  # bytestring
    output.trackId = bytes_to_int(stream_reader(4))  # Int32
    output.trackMeters = bytes_to_int(stream_reader(4))  # Int32


def read_car_event(stream_reader: Callable[[int], bytes], output: UDPEntryList):
    """Read stream - car event"""
    event_type = bytes_to_int(stream_reader(1))  # byte
    message = read_string(stream_reader, 2)  # bytestring
    timestamp = bytes_to_int(stream_reader(4))  # Int32
    car_id = bytes_to_int(stream_reader(4))  # Int32
    car_info = output.entryListCars[car_id]

    # Count accidents
    if 4 == event_type != car_info.eventType:
        car_info.accidents += 1

    car_info.eventType = event_type
    car_info.eventMessage = message
    car_info.eventTimestamp = timestamp


# Parse data
def parse_udp_stream(response: bytes, output: UDPBroadcastOutput) -> int:
    """Parse ACC UDP data stream, return message type that matches InboundMessageTypes"""
    message_type = -1
    if not response:
        return message_type
    with io.BytesIO(response) as data_stream:
        stream_reader = data_stream.read  # pass stream data reader to reduce lookups
        try:
            message_type = bytes_to_int(stream_reader(1))
            # Ordered by most frequent accessed message type
            if message_type == 3:  # InboundMessageTypes.REALTIME_CAR_UPDATE
                read_realtime_car_update(stream_reader, output.entryList, output.sessionInfo.sessionPhase >= 6)
            elif message_type == 2:  # InboundMessageTypes.REALTIME_UPDATE
                read_realtime_update(stream_reader, output.sessionInfo)
            elif message_type == 6:  # InboundMessageTypes.ENTRY_LIST_CAR
                read_entry_list_car(stream_reader, output.entryList)
            elif message_type == 4:  # InboundMessageTypes.ENTRY_LIST
                read_entry_list(stream_reader, output.entryList)
            elif message_type == 7:  # InboundMessageTypes.BROADCASTING_EVENT
                read_car_event(stream_reader, output.entryList)
            elif message_type == 5:  # InboundMessageTypes.TRACK_DATA
                read_track_data(stream_reader, output.trackData)
            elif message_type == 1:  # InboundMessageTypes.REGISTRATION_RESULT
                read_registration_result(stream_reader, output.registration)
        except (TypeError, struct.error):
            pass
    return message_type


# UDP connection function
@contextmanager
def acc_udp_connect(
    udp_host: str,
    udp_port: int,
    udp_output: UDPBroadcastOutput,
    connection_message: bytes | bytearray = b"",
    connection_timeout: float = 60.0,
    blocking: bool = True,
    event: threading.Event | None = None,
    callback_function: Callable[[int], None] | None = None,
):
    """Connect client to ACC UDP API

    Args:
        udp_host: UDP host name.
        udp_port: UDP port matches broadcasting.json 'updListenerPort' value.
        udp_output: UDP broadcast output data.
        connection_message: set register connection message, see 'set_register_message' function.
        connection_timeout: UDP connection timeout, default 60 seconds.
        blocking: set blocking or non-blocking mode.
    """
    server_address = (udp_host, udp_port)
    connection_id = -999
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to broadcasting server
        if blocking or event is None:
            sock.settimeout(connection_timeout)
        else:
            sock.setblocking(False)
        sock.connect(server_address)

        # Send register message
        logger.info("UDP: REQUESTED: REGISTER_COMMAND_APPLICATION")
        sock.send(connection_message)

        # Get response
        if blocking:
            response = sock.recv(512)
        else:
            while not event.wait(0.5) and connection_timeout > 0:
                try:
                    response = sock.recv(512)
                    break
                except BlockingIOError:
                    connection_timeout -= 0.5
            else:
                logger.error("UDP: ERROR: connection timed out")
                raise TimeoutError

        parse_udp_stream(response, udp_output)

        # Update connection id
        connection_id = udp_output.registration.connectionId
        error_message = udp_output.registration.errorMessage

        # Failed (usually due to wrong connection password)
        if not udp_output.registration.connectionSuccess:
            logger.error("UDP: ERROR: %s", error_message.decode())
            raise OSError

        # Connection info
        logger.info("UDP: CONNECTED: ACC Broadcasting Protocol (v%s)", BroadcastingNetworkProtocol.BROADCASTING_PROTOCOL_VERSION)
        logger.info("UDP: CLIENT: #%s (%s:%s)", connection_id, udp_host, udp_port)
        if udp_output.registration.errorMessage:
            logger.info("UDP: CLIENT: %s", error_message.decode())
        yield sock

    finally:
        # Disconnect current client & close socket
        if connection_id != -999:
            logger.info("UDP: REQUESTED: UNREGISTER_COMMAND_APPLICATION")
            # Disconnect current client, 9=OutboundMessageTypes.UNREGISTER_COMMAND_APPLICATION
            sock.send(set_message(9, connection_id))
            logger.info("UDP: DISCONNECTING: ACC Broadcasting Protocol (v%s)", BroadcastingNetworkProtocol.BROADCASTING_PROTOCOL_VERSION)
            logger.info("UDP: CLIENT UNREGISTERED: #%s (%s:%s)", connection_id, udp_host, udp_port)
        sock.close()
        # Run callback function
        if callable(callback_function):
            callback_function(connection_id)


def acc_udp_disconnect(
    udp_host: str,
    udp_port: int,
    connection_id: Sequence[int],
    connection_timeout: float = 1.0,
):
    """Unregister & disconnect specific list of clients from ACC UDP API

    Args:
        udp_host: UDP host name.
        udp_port: UDP port matches broadcasting.json 'updListenerPort' value.
        connection_id: disconnect all clients from connection ID list.
        connection_timeout: UDP connection timeout (seconds).
    """
    if not connection_id:
        return
    server_address = (udp_host, udp_port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(connection_timeout)
        sock.connect(server_address)
        # Disconnect all clients, 9=OutboundMessageTypes.UNREGISTER_COMMAND_APPLICATION
        logger.info("UDP: REQUESTED: UNREGISTER_COMMAND_APPLICATION")
        for client_id in set(connection_id):
            sock.send(set_message(9, client_id))
            logger.info("UDP: CLIENT UNREGISTERED: #%s (%s:%s)", client_id, udp_host, udp_port)


def get_client_id(line: str) -> int:
    """Get client id from game log"""
    # Example log line:
    # [2026.09.26-12.41.30:243][534]LogKsRacing: BroadcastingClient: Added new client 1 for 'tester' with command mode 0 and realtime interval 250
    pos_beg = line.find("client")
    if pos_beg > 0:
        pos_beg += 6
        pos_end = line.find("for")
        value = line[pos_beg:pos_end].strip()
        if value.isdigit():
            return int(value)
    return -1


def get_purged_id(line: str) -> int:
    """Get purged client id from game log"""
    # Example log line:
    # [2026.09.26-12.42.08:790][899]LogKsPhysics: Purged broadcasting clients for connection_id 1, now 0 clients connected
    pos_beg = line.find("connection_id")
    if pos_beg > 0:
        pos_beg += 13
        pos_end = line.find(",")
        value = line[pos_beg:pos_end].strip()
        if value.isdigit():
            return int(value)
    return -1


def clean_obsolete_client(udp_host: str, udp_port: int, client_name: str, log_path: str) -> bool:
    """Clean obsolete client from specified client name (usually due to unexpected closing)

    Args:
        udp_host: UDP host name used for specified client name.
        udp_port: UDP port used for specified client name.
        client_name: display name as set via 'set_register_message' function during previous connections.
        log_path: ACC game log path. See ACCConstants.LOG_PATH in acc_data.py.

    Returns:
        True: if any obsolete clients found and cleaned.
        False: if no obsolete clients found, or log not found.
    """
    if not os.path.exists(log_path):
        logger.info("UDP: ACC log not found: %s", log_path)
        return False

    client_name = f"'{client_name}'"  # wrap name in '' to detect empty client name
    connected_id_set = set()  # unique client ID list
    purged_id_set = set()  # unique purged client ID list
    last_connected_id = -1

    with open(log_path, "r", encoding="utf-8") as log:
        for line in log:
            # Only record from specified client name
            if "Added new client" in line and client_name in line:
                found_id = get_client_id(line)
                # Game recounts ID from 1 in new session, so reset
                if last_connected_id > found_id:
                    connected_id_set.clear()
                    purged_id_set.clear()
                last_connected_id = found_id
                connected_id_set.add(found_id)
            elif "Purged broadcasting clients" in line:
                purged_id_set.add(get_purged_id(line))

    # Get obsolete ID list under this client name
    obsolete_id_set = connected_id_set.difference(purged_id_set)
    if not obsolete_id_set:
        return False
    # Unregister & disconnect obsolete ID
    logger.info("UDP: Found obsolete client: #%s", ",".join(str(n) for n in obsolete_id_set))
    acc_udp_disconnect(udp_host, udp_port, obsolete_id_set)
    return True
