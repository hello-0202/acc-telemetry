"""
Python mapping of ACC's Shared Memory Interface

This library is based on "ACC Shared Memory Documentation":
https://www.assettocorsa.net/forum/index.php?threads/acc-shared-memory-documentation.59965/

Type hints & annotation:
- Annotate "ctypes type" as "Python type" according to table from:
  https://docs.python.org/3/library/ctypes.html#fundamental-data-types
- Annotate array object as list[type].
"""

from __future__ import annotations

import ctypes
import mmap
import os

from ._common import _t, typedstruct


# Constants
class ACCConstants:
    """ACC constants

    Note, manually append 'Local\\' prefix to mmap file name on windows,
    or use 'MMapControl' to set prefix.
    """

    MM_PHYSICS_FILE_NAME = "acpmf_physics"
    MM_GRAPHICS_FILE_NAME = "acpmf_graphics"
    MM_STATIC_FILE_NAME = "acpmf_static"

    MAX_MAPPED_VEHICLES: int = 60
    INVALID_CAR_INDEX: int = -1

    LOG_PATH: str = f"{os.getenv('LOCALAPPDATA', '.')}\\AC2\\Saved\\Logs\\AC2.log"


@typedstruct(pack=4)
class ACCVec3(ctypes.Structure):
    """Vector coordinates"""

    __slots__ = ()

    x: float = _t(ctypes.c_float)
    y: float = _t(ctypes.c_float)
    z: float = _t(ctypes.c_float)


@typedstruct(pack=4)
class ACCPhysics(ctypes.Structure):
    """Mapping of 'SPageFileGraphic'

    Attributes:
        packetId: Current step index
        throttle: Gas pedal input value (from -0 to 1.0)
        brake: Brake pedal input value (from -0 to 1.0)
        fuel: Amount of fuel remaining (liters)
        gear: Current gear, 0=reverse, 1=neutral, 2+=forward
        rpm: Engine revolutions per minute
        steerAngle: Steering input value (from -1.0 to 1.0)
        speedKmh: Car speed in km/h
        velocity: Car velocity vector in global coordinates
        acceleration: Car acceleration (G force) vector in global coordinates
        wheelSlip: Tyre slip for each tyre [FL, FR, RL, RR]
        wheelLoad: (unused) Wheel load for each tyre [FL, FR, RL, RR]
        wheelPressure: Tyre pressure [FL, FR, RL, RR]
        wheelAngularSpeed: Wheel angular speed in rad/s  [FL, FR, RL, RR]
        tyreWear: (unused) Tyre wear [FL, FR, RL, RR]
        tyreDirtyLevel: (unused) Dirt accumulated on tyre surface [FL, FR, RL, RR]
        tyreCoreTemp: Tyre rubber core temperature (Celsius) [FL, FR, RL, RR]
        camberAngle: (unused) Wheels camber angle in radians [FL, FR, RL, RR]
        suspensionTravel: Suspension travel (meters) [FL, FR, RL, RR]
        drsActive: (unused) DRS on
        tcActive: is TC active
        heading: Car yaw orientation
        pitch: Car pitch orientation
        roll: Car roll orientation
        cgHeight: (unused) Centre of gravity height
        carDamage: Car damage: front 0, rear 1, left 2, right 3, centre 4 (total)
        numberOfTyresOut: (unused) Number of tyres out of track
        pitLimiterOn: Pit limiter is on
        absActive: is ABS active
        kersCharge: Not used in ACC
        kersInput: Not used in ACC
        autoShifterOn: Automatic transmission on
        rideHeight: (unused) Ride height: 0 front, 1 rear
        turboBoost: Car turbo level
        ballast: (unused) Car ballast in kg / Not implemented
        airDensity: (unused) Air density
        airTemp: Air temperature (Celsius)
        roadTemp: Road temperature (Celsius)
        localAngularVel: Car angular velocity vector in local coordinates
        forceFeedback: Force feedback signal
        performanceMeter: Not used in ACC
        engineBrake: Not used in ACC
        ersRecoveryLevel: Not used in ACC
        ersPowerLevel: Not used in ACC
        ersHeatCharging: Not used in ACC
        ersIsCharging: Not used in ACC
        kersCurrentKJ: Not used in ACC
        drsAvailable: Not used in ACC
        drsEnabled: Not used in ACC
        brakeTemp: Brake discs temperatures (Celsius)
        clutch: Clutch pedal input value (from 1.0=min to -0=max)
        tyreTempI: Not shown in ACC (Celsius)
        tyreTempM: Not shown in ACC (Celsius)
        tyreTempO: Not shown in ACC (Celsius)
        isAIControlled: Car is controlled by the AI
        tyreContactPoint: Tyre contact point global coordinates [FL, FR, RL, RR]
        tyreContactNormal: Tyre contact normal  [FL, FR, RL, RR] [x,y,z]
        tyreContactHeading: Tyre contact heading  [FL, FR, RL, RR] [x,y,z]
        brakeBias: Front brake bias, see Appendix 4
        localVelocity: Car velocity vector in local coordinates
        pushToPassActivations: Not used in ACC
        pushToPassStatus: Not used in ACC
        currentMaxRPM: Maximum engine rpm
        mz: Not shown in ACC
        fx: Not shown in ACC
        fy: Not shown in ACC
        slipRatio: Tyre slip ratio [FL, FR, RL, RR]
        slipAngle: Tyre slip angle [FL, FR, RL, RR] in radians
        tcInAction: (unused) TC in action
        absInAction: (unused) ABS in action
        suspensionDamage: (unused) Suspensions damage levels [FL, FR, RL, RR]
        tyreTemp: Tyres core temperatures (Celsius) [FL, FR, RL, RR]
        waterTemp: Water temperature (Celsius)
        brakePressure: brake pressure (fraction)
        frontBrakeCompound: Brake pad compund front (+1 offset)
        rearBrakeCompound: Brake pad compund rear (+1 offset)
        padLife: Brake pad wear [FL, FR, RL, RR]
        discLife: Brake disk wear [FL, FR, RL, RR]
        ignitionOn: Ignition switch set to on?
        starterEngineOn: Starter Switch set to on?
        isEngineRunning: Engine running?
        kerbVibration: vibrations sent to the FFB, could be used for motion rigs
        slipVibrations: vibrations sent to the FFB, could be used for motion rigs
        gVibrations: vibrations sent to the FFB, could be used for motion rigs
        absVibrations: vibrations sent to the FFB, could be used for motion rigs
    """

    __slots__ = ()

    packetId: int = _t(ctypes.c_int)
    throttle: float = _t(ctypes.c_float)
    brake: float = _t(ctypes.c_float)
    fuel: float = _t(ctypes.c_float)
    gear: int = _t(ctypes.c_int)
    rpm: int = _t(ctypes.c_int)
    steerAngle: float = _t(ctypes.c_float)
    speedKmh: float = _t(ctypes.c_float)
    velocity: ACCVec3 = _t(ACCVec3)
    acceleration: ACCVec3 = _t(ACCVec3)
    wheelSlip: list[float] = _t(ctypes.c_float * 4)
    wheelLoad: list[float] = _t(ctypes.c_float * 4)
    wheelPressure: list[float] = _t(ctypes.c_float * 4)
    wheelAngularSpeed: list[float] = _t(ctypes.c_float * 4)
    tyreWear: list[float] = _t(ctypes.c_float * 4)
    tyreDirtyLevel: list[float] = _t(ctypes.c_float * 4)
    tyreCoreTemp: list[float] = _t(ctypes.c_float * 4)
    camberAngle: list[float] = _t(ctypes.c_float * 4)
    suspensionTravel: list[float] = _t(ctypes.c_float * 4)
    drsActive: float = _t(ctypes.c_float)
    tcActive: float = _t(ctypes.c_float)
    heading: float = _t(ctypes.c_float)
    pitch: float = _t(ctypes.c_float)
    roll: float = _t(ctypes.c_float)
    cgHeight: float = _t(ctypes.c_float)
    carDamage: list[float] = _t(ctypes.c_float * 5)
    numberOfTyresOut: int = _t(ctypes.c_int)
    pitLimiterOn: int = _t(ctypes.c_int)
    absActive: float = _t(ctypes.c_float)
    kersCharge: float = _t(ctypes.c_float)
    kersInput: float = _t(ctypes.c_float)
    autoShifterOn: int = _t(ctypes.c_int)
    rideHeight: list[float] = _t(ctypes.c_float * 2)
    turboBoost: float = _t(ctypes.c_float)
    ballast: float = _t(ctypes.c_float)
    airDensity: float = _t(ctypes.c_float)
    airTemp: float = _t(ctypes.c_float)
    roadTemp: float = _t(ctypes.c_float)
    localAngularVel: ACCVec3 = _t(ACCVec3)
    forceFeedback: float = _t(ctypes.c_float)
    performanceMeter: float = _t(ctypes.c_float)
    engineBrake: int = _t(ctypes.c_int)
    ersRecoveryLevel: int = _t(ctypes.c_int)
    ersPowerLevel: int = _t(ctypes.c_int)
    ersHeatCharging: int = _t(ctypes.c_int)
    ersIsCharging: int = _t(ctypes.c_int)
    kersCurrentKJ: float = _t(ctypes.c_float)
    drsAvailable: int = _t(ctypes.c_int)
    drsEnabled: int = _t(ctypes.c_int)
    brakeTemp: list[float] = _t(ctypes.c_float * 4)
    clutch: float = _t(ctypes.c_float)
    tyreTempI: list[float] = _t(ctypes.c_float * 4)
    tyreTempM: list[float] = _t(ctypes.c_float * 4)
    tyreTempO: list[float] = _t(ctypes.c_float * 4)
    isAIControlled: int = _t(ctypes.c_int)
    tyreContactPoint: list[ACCVec3] = _t(ACCVec3 * 4)
    tyreContactNormal: list[ACCVec3] = _t(ACCVec3 * 4)
    tyreContactHeading: list[ACCVec3] = _t(ACCVec3 * 4)
    brakeBias: float = _t(ctypes.c_float)
    localVelocity: ACCVec3 = _t(ACCVec3)
    pushToPassActivations: int = _t(ctypes.c_int)
    pushToPassStatus: int = _t(ctypes.c_int)
    currentMaxRPM: int = _t(ctypes.c_int)
    mz: list[float] = _t(ctypes.c_float * 4)
    fx: list[float] = _t(ctypes.c_float * 4)
    fy: list[float] = _t(ctypes.c_float * 4)
    slipRatio: list[float] = _t(ctypes.c_float * 4)
    slipAngle: list[float] = _t(ctypes.c_float * 4)
    tcInAction: int = _t(ctypes.c_int)
    absInAction: int = _t(ctypes.c_int)
    suspensionDamage: list[float] = _t(ctypes.c_float * 4)
    tyreTemp: list[float] = _t(ctypes.c_float * 4)
    waterTemp: float = _t(ctypes.c_float)
    brakePressure: list[float] = _t(ctypes.c_float * 4)
    frontBrakeCompound: int = _t(ctypes.c_int)
    rearBrakeCompound: int = _t(ctypes.c_int)
    padLife: list[float] = _t(ctypes.c_float * 4)
    discLife: list[float] = _t(ctypes.c_float * 4)
    ignitionOn: int = _t(ctypes.c_int)
    starterEngineOn: int = _t(ctypes.c_int)
    isEngineRunning: int = _t(ctypes.c_int)
    kerbVibration: float = _t(ctypes.c_float)
    slipVibrations: float = _t(ctypes.c_float)
    gVibrations: float = _t(ctypes.c_float)
    absVibrations: float = _t(ctypes.c_float)


@typedstruct(pack=4)
class ACCGraphics(ctypes.Structure):
    """Mapping of 'SPageFileGraphic'

    Attributes:
        packetId: Current step index
        status: 0=off, 1=replay, 2=live, 3=pause, see enums ACCStatus
        session: -1=unknown, 0=practice, 1=qualify, 2=race, 3=hotlap, 4=timeattack, 5=drift, 6=drag, 7=hotstint, 8=hotstintsuperpole, see enums ACCSessionType
        currentTime: Current lap time in wide character
        lastTime: Last lap time in wide character
        bestTime: Best lap time in wide character
        split: Last split time in wide character
        completedLaps: Number of completed laps
        position: Current player position
        iCurrentTime: Current lap time (ms)
        iLastTime: Last lap time (ms)
        iBestTime: Best lap time (ms)
        sessionTimeLeft: Session time left (ms)
        distanceTraveled: Distance travelled in the current stint
        isInPit: Car is in pit stall (stopped for service/penalty)
        currentSectorIndex: Current track sector
        lastSectorTime: Last sector time (ms)
        numberOfLaps: (unused) Number of completed laps
        tyreCompound: Tyre compound name
        replayTimeMultiplier: Not used in ACC
        normalizedCarPosition: Car position on track spline (0.0 start to 1.0 finish)
        activeCars: Number of cars on track
        carCoordinates: Coordinates list of cars on track, ordered by unique Car ID of each car
        carIDs: unique Car ID list of cars on track
        playerCarID: Local player's Car ID
        penaltyTime: Penalty time to wait
        flag: 0=none, 1=blue, 2=yellow, 3=black, 4=white, 5=checkered, 6=penalty, 7=green, 8=orange, see enums ACCFlagType
        penalty: 0=none, 1/7/19=DT, 2/3/4/8/9/10=SG, 5/11/13/15/16/17/18/20/21=DSQ, 6/12=deletelaptime, See enums ACCPenaltyType
        idealLineOn: Ideal line on
        isInPitLane: Car is in pit lane
        surfaceGrip: Ideal line friction coefficient
        mandatoryPitDone: Number of mandatory pit completed
        windSpeed: Wind speed in km/h
        windDirection: Wind direction in degrees
        isSetupMenuVisible: Car is working on setup
        mainDisplayIndex: Current car main display index, see Appendix 1
        secondaryDisplyIndex: Current car secondary display index
        tcLevel: Traction control level
        tcCutLevel: Traction control cut level (for some car, this is TC slip level)
        engineMap: Current engine map
        absLevel: ABS level
        fuelXLap: Average fuel consumed per lap in liters
        rainLights: Rain lights on
        flashingLights: Flashing lights on
        lightsStage: Current lights stage
        exhaustTemperature: Exhaust temperature (Celsius)
        wiperLV: Current wiper stage
        driverStintTotalTimeLeft: Time the driver is allowed to drive/race (ms)
        driverStintTimeLeft: Time the driver is allowed to drive/stint (ms)
        rainTyres: Are rain tyres equipped
        sessionIndex: Session index
        usedFuel: Used fuel since last time refueling
        deltaLapTime: Delta time in wide character
        iDeltaLapTime: Delta time time (ms)
        estimatedLapTime: Estimated lap time in wide character
        iEstimatedLapTime: Estimated lap time (ms)
        isDeltaPositive: Delta positive (1) or negative (0)
        iSplit: Last split time (ms)
        isValidLap: Check if current Lap is valid for timing (doesn't report invalidation in race)
        fuelEstimatedLaps: Laps possible with current fuel level
        trackStatus: Status of track
        missingMandatoryPits: Number of mandatory pitstops the player still has to do
        timeOfDay: Time of day in seconds
        directionLightsLeft: Is Blinker left on
        directionLightsRight: Is Blinker right on
        globalYellow: Yellow Flag is out (any sectors)?
        globalYellow1: Yellow Flag in Sector 1 is out?
        globalYellow2: Yellow Flag in Sector 2 is out?
        globalYellow3: Yellow Flag in Sector 3 is out?
        globalWhite: White Flag is out?
        globalGreen: Green Flag is out?
        globalChequered: Checkered Flag is out?
        globalRed: Red Flag is out?
        mfdTyreSet: Number of tyre set on the MFD
        mfdFuelToAdd: How much fuel to add on the MFD
        mfdTyrePressureLF: Tyre pressure left front on the MFD
        mfdTyrePressureRF: Tyre pressure right front on the MFD
        mfdTyrePressureLR: Tyre pressure left rear on the MFD
        mfdTyrePressureRR: Tyre pressure right rear on the MFD
        trackGripStatus: 0=green, 1=fast, 2=optium, 3=greasy, 4=damp, 5=wet, 6=flooded, see enums ACCTrackGripStatus
        rainIntensity: 0=none, 1=drizzle, 2=light rain, 3=medium rain, 4=heavy rain, 5=thunderstorm, see enums ACCRainIntensity
        rainIntensityIn10min: 0=no rain, 1=drizzle, 2=light rain, 3=medium rain, 4=heavy rain, 5=thunderstorm, see enums ACCRainIntensity
        rainIntensityIn30min: 0=no rain, 1=drizzle, 2=light rain, 3=medium rain, 4=heavy rain, 5=thunderstorm, see enums ACCRainIntensity
        currentTyreSet: Tyre Set currently in use
        strategyTyreSet: Next Tyre set per strategy
        gapAhead: (unused) Distance in ms to car in front
        gapBehind: (unused) Distance in ms to car behind
    """

    __slots__ = ()

    packetId: int = _t(ctypes.c_int)
    status: int = _t(ctypes.c_int)
    session: int = _t(ctypes.c_int)
    currentTime: str = _t(ctypes.c_wchar * 15)
    lastTime: str = _t(ctypes.c_wchar * 15)
    bestTime: str = _t(ctypes.c_wchar * 15)
    split: str = _t(ctypes.c_wchar * 15)
    completedLaps: int = _t(ctypes.c_int)
    position: int = _t(ctypes.c_int)
    iCurrentTime: int = _t(ctypes.c_int)
    iLastTime: int = _t(ctypes.c_int)
    iBestTime: int = _t(ctypes.c_int)
    sessionTimeLeft: float = _t(ctypes.c_float)
    distanceTraveled: float = _t(ctypes.c_float)
    isInPit: int = _t(ctypes.c_int)
    currentSectorIndex: int = _t(ctypes.c_int)
    lastSectorTime: int = _t(ctypes.c_int)
    numberOfLaps: int = _t(ctypes.c_int)
    tyreCompound: str = _t(ctypes.c_wchar * 33)
    replayTimeMultiplier: float = _t(ctypes.c_float)
    normalizedCarPosition: float = _t(ctypes.c_float)
    activeCars: int = _t(ctypes.c_int)
    carCoordinates: list[ACCVec3] = _t(ACCVec3 * ACCConstants.MAX_MAPPED_VEHICLES)
    carIDs: list[int] = _t(ctypes.c_int * ACCConstants.MAX_MAPPED_VEHICLES)
    playerCarID: int = _t(ctypes.c_int)
    penaltyTime: float = _t(ctypes.c_float)
    flag: int = _t(ctypes.c_int)
    penalty: int = _t(ctypes.c_int)
    idealLineOn: int = _t(ctypes.c_int)
    isInPitLane: int = _t(ctypes.c_int)
    surfaceGrip: float = _t(ctypes.c_float)
    mandatoryPitDone: int = _t(ctypes.c_int)
    windSpeed: float = _t(ctypes.c_float)
    windDirection: float = _t(ctypes.c_float)
    isSetupMenuVisible: int = _t(ctypes.c_int)
    mainDisplayIndex: int = _t(ctypes.c_int)
    secondaryDisplyIndex: int = _t(ctypes.c_int)
    tcLevel: int = _t(ctypes.c_int)
    tcCutLevel: int = _t(ctypes.c_int)
    engineMap: int = _t(ctypes.c_int)
    absLevel: int = _t(ctypes.c_int)
    fuelXLap: float = _t(ctypes.c_float)
    rainLights: int = _t(ctypes.c_int)
    flashingLights: int = _t(ctypes.c_int)
    lightsStage: int = _t(ctypes.c_int)
    exhaustTemperature: float = _t(ctypes.c_float)
    wiperLV: int = _t(ctypes.c_int)
    driverStintTotalTimeLeft: int = _t(ctypes.c_int)
    driverStintTimeLeft: int = _t(ctypes.c_int)
    rainTyres: int = _t(ctypes.c_int)
    sessionIndex: int = _t(ctypes.c_int)
    usedFuel: float = _t(ctypes.c_float)
    deltaLapTime: str = _t(ctypes.c_wchar * 15)
    iDeltaLapTime: int = _t(ctypes.c_int)
    estimatedLapTime: str = _t(ctypes.c_wchar * 15)
    iEstimatedLapTime: int = _t(ctypes.c_int)
    isDeltaPositive: int = _t(ctypes.c_int)
    iSplit: int = _t(ctypes.c_int)
    isValidLap: int = _t(ctypes.c_int)
    fuelEstimatedLaps: float = _t(ctypes.c_float)
    trackStatus: str = _t(ctypes.c_wchar * 33)
    missingMandatoryPits: int = _t(ctypes.c_int)
    timeOfDay: float = _t(ctypes.c_float)
    directionLightsLeft: int = _t(ctypes.c_int)
    directionLightsRight: int = _t(ctypes.c_int)
    globalYellow: int = _t(ctypes.c_int)
    globalYellow1: int = _t(ctypes.c_int)
    globalYellow2: int = _t(ctypes.c_int)
    globalYellow3: int = _t(ctypes.c_int)
    globalWhite: int = _t(ctypes.c_int)
    globalGreen: int = _t(ctypes.c_int)
    globalChequered: int = _t(ctypes.c_int)
    globalRed: int = _t(ctypes.c_int)
    mfdTyreSet: int = _t(ctypes.c_int)
    mfdFuelToAdd: float = _t(ctypes.c_float)
    mfdTyrePressureLF: float = _t(ctypes.c_float)
    mfdTyrePressureRF: float = _t(ctypes.c_float)
    mfdTyrePressureLR: float = _t(ctypes.c_float)
    mfdTyrePressureRR: float = _t(ctypes.c_float)
    trackGripStatus: int = _t(ctypes.c_int)
    rainIntensity: int = _t(ctypes.c_int)
    rainIntensityIn10min: int = _t(ctypes.c_int)
    rainIntensityIn30min: int = _t(ctypes.c_int)
    currentTyreSet: int = _t(ctypes.c_int)
    strategyTyreSet: int = _t(ctypes.c_int)
    gapAhead: int = _t(ctypes.c_int)
    gapBehind: int = _t(ctypes.c_int)


@typedstruct(pack=4)
class ACCStatic(ctypes.Structure):
    """Mapping of 'SPageFileStatic'

    Attributes:
        smVersion: Shared memory version
        acVersion: Assetto Corsa version
        numberOfSessions: Number of sessions
        numberOfCars: Number of total cars
        carModel: Player car model (unformatted), see Appendix 2
        trackName: Track name (unformatted)
        playerName: Player name (first name)
        playerSurname: Player surname (last name)
        playerNick: Player nickname
        sectorCount: Number of sectors
        maxTorque: Not shown in ACC
        maxPower: Not shown in ACC
        maxRpm: Maximum rpm
        maxFuel: Maximum fuel tank capacity (liters)
        suspensionMaxTravel: Not shown in ACC
        tyreRadius: Not shown in ACC
        maxTurboBoost: (unused) Maximum turbo boost
        deprecated_1: Not used in ACC
        deprecated_2: Not used in ACC
        penaltiesEnabled: Penalties enabled
        aidFuelRate: Fuel consumption rate
        aidTireRate: Tyre wear rate
        aidMechanicalDamage: Mechanical damage rate
        allowTyreBlankets: Not allowed in Blancpain endurance series
        aidStability: Stability control used
        aidAutoClutch: Auto clutch used
        aidAutoBlip: Always true in ACC
        hasDRS: Not used in ACC
        hasERS: Not used in ACC
        hasKERS: Not used in ACC
        kersMaxJ: Not used in ACC
        engineBrakeSettingsCount: Not used in ACC
        ersPowerControllerCount: Not used in ACC
        trackSplineLength: Not used in ACC
        trackConfiguration: Not used in ACC
        ersMaxJ: Not used in ACC
        isTimedRace: Not used in ACC
        hasExtraLap: Not used in ACC
        carSkin: Not used in ACC
        reversedGridPositions: Not used in ACC
        pitWindowStart: Pit window opening time (ms) since 0 second. Note, pit window is unavailable for the session if pitWindowStart greater than pitWindowEnd.
        pitWindowEnd: Pit windows closing time (ms) since 0 second.
        isOnline: If is a multiplayer session
        dryTyresName: Name of the dry tyres
        wetTyresName: Name of the wet tyres
    """

    __slots__ = ()

    smVersion: str = _t(ctypes.c_wchar * 15)
    acVersion: str = _t(ctypes.c_wchar * 15)
    numberOfSessions: int = _t(ctypes.c_int)
    numberOfCars: int = _t(ctypes.c_int)
    carModel: str = _t(ctypes.c_wchar * 33)
    trackName: str = _t(ctypes.c_wchar * 33)
    playerName: str = _t(ctypes.c_wchar * 33)
    playerSurname: str = _t(ctypes.c_wchar * 33)
    playerNick: str = _t(ctypes.c_wchar * 33)
    sectorCount: int = _t(ctypes.c_int)
    maxTorque: float = _t(ctypes.c_float)
    maxPower: float = _t(ctypes.c_float)
    maxRpm: int = _t(ctypes.c_int)
    maxFuel: float = _t(ctypes.c_float)
    suspensionMaxTravel: list[float] = _t(ctypes.c_float * 4)
    tyreRadius: list[float] = _t(ctypes.c_float * 4)
    maxTurboBoost: float = _t(ctypes.c_float)
    deprecated_1: float = _t(ctypes.c_float)
    deprecated_2: float = _t(ctypes.c_float)
    penaltiesEnabled: int = _t(ctypes.c_int)
    aidFuelRate: float = _t(ctypes.c_float)
    aidTireRate: float = _t(ctypes.c_float)
    aidMechanicalDamage: float = _t(ctypes.c_float)
    allowTyreBlankets: int = _t(ctypes.c_int)
    aidStability: float = _t(ctypes.c_float)
    aidAutoClutch: int = _t(ctypes.c_int)
    aidAutoBlip: int = _t(ctypes.c_int)
    hasDRS: int = _t(ctypes.c_int)
    hasERS: int = _t(ctypes.c_int)
    hasKERS: int = _t(ctypes.c_int)
    kersMaxJ: float = _t(ctypes.c_float)
    engineBrakeSettingsCount: int = _t(ctypes.c_int)
    ersPowerControllerCount: int = _t(ctypes.c_int)
    trackSplineLength: float = _t(ctypes.c_float)
    trackConfiguration: str = _t(ctypes.c_wchar * 33)
    ersMaxJ: float = _t(ctypes.c_float)
    isTimedRace: int = _t(ctypes.c_int)
    hasExtraLap: int = _t(ctypes.c_int)
    carSkin: str = _t(ctypes.c_wchar * 33)
    reversedGridPositions: int = _t(ctypes.c_int)
    pitWindowStart: int = _t(ctypes.c_int)
    pitWindowEnd: int = _t(ctypes.c_int)
    isOnline: int = _t(ctypes.c_int)
    dryTyresName: str = _t(ctypes.c_wchar * 33)
    wetTyresName: str = _t(ctypes.c_wchar * 33)


# Memory map
class SimInfo:
    """Simulation info from shared memory"""

    def __init__(self, mmap_prefix: str = "Local\\"):
        self._acc_phys = mmap.mmap(
            fileno=0,
            length=ctypes.sizeof(ACCPhysics),
            tagname=f"{mmap_prefix}{ACCConstants.MM_PHYSICS_FILE_NAME}",
        )
        self.ACCPhys = ACCPhysics.from_buffer(self._acc_phys)

        self._acc_gfx = mmap.mmap(
            fileno=0,
            length=ctypes.sizeof(ACCGraphics),
            tagname=f"{mmap_prefix}{ACCConstants.MM_GRAPHICS_FILE_NAME}",
        )
        self.ACCGfx = ACCGraphics.from_buffer(self._acc_gfx)

        self._acc_stat = mmap.mmap(
            fileno=0,
            length=ctypes.sizeof(ACCStatic),
            tagname=f"{mmap_prefix}{ACCConstants.MM_STATIC_FILE_NAME}",
        )
        self.ACCStat = ACCStatic.from_buffer(self._acc_stat)

    def close(self):
        try:
            self.ACCPhys = None
            self.ACCGfx = None
            self.ACCStat = None
            self._acc_phys.close()
            self._acc_gfx.close()
            self._acc_stat.close()
            print("ACC MMap closed")
        except BufferError:
            pass

    def __del__(self):
        self.close()
