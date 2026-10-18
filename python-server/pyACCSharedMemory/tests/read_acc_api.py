"""
Test & read data from ACC's built-in Shared Memory Interface
"""

from __future__ import annotations

import ctypes
import sys

sys.path.append(__file__.split("pyACCSharedMemory")[0])
from pyACCSharedMemory import acc_data, acc_enum


def verify_struct_size(s_class: ctypes.Structure, size_origin: int):
    size = ctypes.sizeof(s_class)
    print(f"{s_class.__name__:<24} Size: {size:<10} ORG: {size_origin:<10} Match: {size_origin == size}")
    if size_origin != size:
        raise ValueError("Structure size mismatch.")


def compare_struct_size():
    print("Verify Struct Size:")
    verify_struct_size(acc_data.ACCVec3, 12)
    verify_struct_size(acc_data.ACCPhysics, 800)
    verify_struct_size(acc_data.ACCGraphics, 1588)
    verify_struct_size(acc_data.ACCStatic, 820)


def physics_info(data: acc_data.ACCPhysics):
    print("Physics info:")
    print("packetId:", data.packetId)
    print("throttle:", data.throttle)
    print("brake:", data.brake)
    print("fuel:", data.fuel)
    print("gear:", data.gear - 1)
    print("rpm:", data.rpm)
    print("currentMaxRPM:", data.currentMaxRPM)
    print("steerAngle:", data.steerAngle)
    print("speedKmh:", data.speedKmh)
    print("pitLimiterOn:", data.pitLimiterOn)
    print("slipRatio:", *data.slipRatio)
    print("airTemp:", data.airTemp)
    print("roadTemp:", data.roadTemp)
    print("tyreTemp:", *data.tyreTemp)
    print("waterTemp:", data.waterTemp)
    print("brakeBias:", data.brakeBias)
    print("brakePressure:", *data.brakePressure)
    print("padLife:", *data.padLife)
    print("discLife:", *data.discLife)
    print("ignitionOn:", data.ignitionOn)
    print("starterEngineOn:", data.starterEngineOn)
    print("isEngineRunning:", data.isEngineRunning)
    print("tcActive:", data.tcActive)
    print("absActive:", data.absActive)
    print("carDamage:", *data.carDamage)
    print("frontBrakeCompound:", data.frontBrakeCompound)
    print("rearBrakeCompound:", data.rearBrakeCompound)
    for t in data.tyreContactPoint:
        print("tyreContactPoint:", t.x, t.y, t.z)


def graphics_info(data: acc_data.ACCGraphics):
    print("Graphics info:")
    print("packetId:", data.packetId)
    print("status:", acc_enum.ACCStatus(data.status))
    print("session:", acc_enum.ACCSessionType(data.session))
    print("currentTime:", data.currentTime)
    print("lastTime:", data.lastTime)
    print("bestTime:", data.bestTime)
    print("completedLaps:", data.completedLaps)
    print("position:", data.position)
    print("iCurrentTime:", data.iCurrentTime)
    print("iLastTime:", data.iLastTime)
    print("isValidLap:", data.isValidLap)
    print("estimatedLapTime:", data.estimatedLapTime)
    print("sessionTimeLeft:", data.sessionTimeLeft)
    print("timeOfDay:", data.timeOfDay)
    print("isInPit:", data.isInPit)
    print("isInPitLane:", data.isInPitLane)
    print("trackStatus:", data.trackStatus)
    print("surfaceGrip:", data.surfaceGrip)
    print("trackGripStatus:", data.trackGripStatus)
    print("flag:", acc_enum.ACCFlagType(data.flag))
    print("penalty:", acc_enum.ACCPenaltyType(data.penalty))
    print("normalizedCarPosition:", data.normalizedCarPosition)
    print("sessionIndex:", data.sessionIndex)
    print("tyreCompound:", data.tyreCompound)


def static_info(data: acc_data.ACCStatic):
    print("Static info:")
    print("smVersion:", data.smVersion)
    print("acVersion:", data.acVersion)
    print("numberOfSessions:", data.numberOfSessions)
    print("numberOfCars:", data.numberOfCars)
    print("carModel:", data.carModel)
    print("trackName:", data.trackName)
    print("playerName:", data.playerName)
    print("playerSurname:", data.playerSurname)
    print("playerNick:", data.playerNick)
    print("sectorCount:", data.sectorCount)
    print("maxRpm:", data.maxRpm)
    print("maxFuel", data.maxFuel)
    print("aidAutoClutch", data.aidAutoClutch)
    print("trackSplineLength", data.trackSplineLength)
    print("isOnline", data.isOnline)
    print("dryTyresName", data.dryTyresName)
    print("wetTyresName", data.wetTyresName)


def player_info(data: acc_data.ACCGraphics, player_index):
    print("Selected Player info:")
    print("carCoordinates:", data.carCoordinates[player_index].x, data.carCoordinates[player_index].y, data.carCoordinates[player_index].z)
    print("carIDs:", data.carIDs[player_index])


def list_zero_data(data, source):
    print("List of zero data:", source.__name__)
    for var, _ in source._fields_:
        value = getattr(data, var)
        if not value:
            print(var, value)


def test_data(info: acc_data.SimInfo, player_index, selected_player_index):
    """Example usage"""
    separator = "-" * 40

    print(separator)

    print("Player Index:")
    print("Local player index:", player_index)
    print("Selected player index:", selected_player_index)

    print(separator)

    physics_info(info.ACCPhys)

    print(separator)

    graphics_info(info.ACCGfx)

    print(separator)

    static_info(info.ACCStat)

    print(separator)

    player_info(info.ACCGfx, selected_player_index)


def test_enum():
    separator = "-" * 40

    print(separator)

    ACC_FLAG_TYPE = acc_enum.enum_map(acc_enum.ACCFlagType)
    ACC_PENALTY_TYPE = acc_enum.enum_map(acc_enum.ACCPenaltyType)
    ACC_SESSION_TYPE = acc_enum.enum_map(acc_enum.ACCSessionType)

    print("Enum:")
    print(acc_enum.ACCFlagType(3))
    print(acc_enum.ACCPenaltyType(6))
    print(acc_enum.ACCSessionType(2))

    print(separator)

    print("Dict (fast lookup):")
    print(ACC_FLAG_TYPE(3))
    print(ACC_PENALTY_TYPE(6))
    print(ACC_SESSION_TYPE(2))
    print(acc_enum.ACC_CAR_MODEL("bmw_m4_gt3"))
    print(acc_enum.ACC_CAR_CLASS("audi_r8_lms_evo"))


def verify_data(info: acc_data.SimInfo):
    separator = "-" * 40

    print(separator)

    list_zero_data(info.ACCPhys, acc_data.ACCPhysics)

    print(separator)

    list_zero_data(info.ACCGfx, acc_data.ACCGraphics)

    print(separator)

    list_zero_data(info.ACCStat, acc_data.ACCStatic)


if __name__ == "__main__":
    compare_struct_size()

    info = acc_data.SimInfo(mmap_prefix="Local\\")

    player_index = info.ACCGfx.playerCarID
    selected_player_index = player_index

    test_data(info, player_index, selected_player_index)

    test_enum()

    verify_data(info)
