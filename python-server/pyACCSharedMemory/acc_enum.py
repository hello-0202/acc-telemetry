"""
ACC API Enums mapping, with fast dict lookup function
"""

from __future__ import annotations

import enum

from ._common import enum_map, map_int_to_str, map_str_to_float, map_str_to_str


# Sharedmemory API Enum
class ACCFlagType(enum.Enum):
    """Flag type"""

    ACC_NO_FLAG = 0
    ACC_BLUE_FLAG = 1
    ACC_YELLOW_FLAG = 2
    ACC_BLACK_FLAG = 3
    ACC_WHITE_FLAG = 4
    ACC_CHECKERED_FLAG = 5
    ACC_PENALTY_FLAG = 6
    ACC_GREEN_FLAG = 7
    ACC_ORANGE_FLAG = 8


class ACCPenaltyType(enum.Enum):
    """Penalty type"""

    ACC_None = 0
    ACC_DriveThrough_Cutting = 1
    ACC_StopAndGo_10_Cutting = 2
    ACC_StopAndGo_20_Cutting = 3
    ACC_StopAndGo_30_Cutting = 4
    ACC_Disqualified_Cutting = 5
    ACC_RemoveBestLaptime_Cutting = 6
    ACC_DriveThrough_PitSpeeding = 7
    ACC_StopAndGo_10_PitSpeeding = 8
    ACC_StopAndGo_20_PitSpeeding = 9
    ACC_StopAndGo_30_PitSpeeding = 10
    ACC_Disqualified_PitSpeeding = 11
    ACC_RemoveBestLaptime_PitSpeeding = 12
    ACC_Disqualified_IgnoredMandatoryPit = 13
    ACC_PostRaceTime = 14
    ACC_Disqualified_Trolling = 15
    ACC_Disqualified_PitEntry = 16
    ACC_Disqualified_PitExit = 17
    ACC_Disqualified_Wrongway = 18
    ACC_DriveThrough_IgnoredDriverStint = 19
    ACC_Disqualified_IgnoredDriverStint = 20
    ACC_Disqualified_ExceededDriverStintLimit = 21


class ACCSessionType(enum.Enum):
    """Session type"""

    ACC_UNKNOWN = -1
    ACC_PRACTICE = 0
    ACC_QUALIFY = 1
    ACC_RACE = 2
    ACC_HOTLAP = 3
    ACC_TIMEATTACK = 4
    ACC_DRIFT = 5
    ACC_DRAG = 6
    ACC_HOTSTINT = 7
    ACC_HOTSTINTSUPERPOLE = 8


class ACCStatus(enum.Enum):
    """Status"""

    ACC_OFF = 0
    ACC_REPLAY = 1
    ACC_LIVE = 2
    ACC_PAUSE = 3


class ACCWheelsType(enum.Enum):
    """Wheels type"""

    ACC_FRONTLEFT = 0
    ACC_FRONTRIGHT = 1
    ACC_REARLEFT = 2
    ACC_REARRIGHT = 3


class ACCTrackGripStatus(enum.Enum):
    """Track grip status"""

    ACC_GREEN = 0
    ACC_FAST = 1
    ACC_OPTIMUM = 2
    ACC_GREASY = 3
    ACC_DAMP = 4
    ACC_WET = 5
    ACC_FLOODED = 6


class ACCRainIntensity(enum.Enum):
    """Rain intensity"""

    ACC_NO_RAIN = 0
    ACC_DRIZZLE = 1
    ACC_LIGHT_RAIN = 2
    ACC_MEDIUM_RAIN = 3
    ACC_HEAVY_RAIN = 4
    ACC_THUNDERSTORM = 5


# Broadcasting API (UDP) Enum
class DriverCategory(enum.Enum):
    """Driver category"""

    Platinum = 3
    Gold = 2
    Silver = 1
    Bronze = 0
    Error = 255


class CupCategory(enum.Enum):
    """Cup category"""

    Pro = 0
    ProAm = 1
    Am = 2
    Silver = 3
    National = 4
    Error = 255


class LapType(enum.Enum):
    """Lap type"""

    ERROR = 0
    Outlap = 1
    Regular = 2
    Inlap = 3


class CarLocation(enum.Enum):
    """Car location"""

    NONE = 0
    Track = 1
    Pitlane = 2
    PitEntry = 3
    PitExit = 4


class SessionPhase(enum.Enum):
    """Session phase"""

    NONE = 0
    Starting = 1
    PreFormation = 2
    FormationLap = 3
    PreSession = 4
    Session = 5
    SessionOver = 6
    PostSession = 7
    ResultUI = 8


class RaceSessionType(enum.Enum):
    """Race session type"""

    Practice = 0
    Qualifying = 4
    Superpole = 9
    Race = 10
    Hotlap = 11
    Hotstint = 12
    HotlapSuperpole = 13
    Replay = 14


class BroadcastingCarEventType(enum.Enum):
    """Broadcasting car event type"""

    NONE = 0
    GreenFlag = 1
    SessionOver = 2
    PenaltyCommMsg = 3
    Accident = 4
    LapCompleted = 5
    BestSessionLap = 6
    BestPersonalLap = 7


class NationalityEnum(enum.Enum):
    """Nationality"""

    Any = 0
    Italy = 1
    Germany = 2
    France = 3
    Spain = 4
    GreatBritain = 5
    Hungary = 6
    Belgium = 7
    Switzerland = 8
    Austria = 9
    Russia = 10
    Thailand = 11
    Netherlands = 12
    Poland = 13
    Argentina = 14
    Monaco = 15
    Ireland = 16
    Brazil = 17
    SouthAfrica = 18
    PuertoRico = 19
    Slovakia = 20
    Oman = 21
    Greece = 22
    SaudiArabia = 23
    Norway = 24
    Turkey = 25
    SouthKorea = 26
    Lebanon = 27
    Armenia = 28
    Mexico = 29
    Sweden = 30
    Finland = 31
    Denmark = 32
    Croatia = 33
    Canada = 34
    China = 35
    Portugal = 36
    Singapore = 37
    Indonesia = 38
    USA = 39
    NewZealand = 40
    Australia = 41
    SanMarino = 42
    UAE = 43
    Luxembourg = 44
    Kuwait = 45
    HongKong = 46
    Colombia = 47
    Japan = 48
    Andorra = 49
    Azerbaijan = 50
    Bulgaria = 51
    Cuba = 52
    CzechRepublic = 53
    Estonia = 54
    Georgia = 55
    India = 56
    Israel = 57
    Jamaica = 58
    Latvia = 59
    Lithuania = 60
    Macau = 61
    Malaysia = 62
    Nepal = 63
    NewCaledonia = 64
    Nigeria = 65
    NorthernIreland = 66
    PapuaNewGuinea = 67
    Philippines = 68
    Qatar = 69
    Romania = 70
    Scotland = 71
    Serbia = 72
    Slovenia = 73
    Taiwan = 74
    Ukraine = 75
    Venezuela = 76
    Wales = 77
    Iran = 78
    Bahrain = 79
    Zimbabwe = 80
    ChineseTaipei = 81
    Chile = 82
    Uruguay = 83
    Madagascar = 84


# Reference map
ACC_TRACK_NAME = map_str_to_str({  # Added year suffix according to release date
    # Base
    "Barcelona": "Circuit de Barcelona-Catalunya",
    "brands_hatch": "Brands Hatch Circuit",
    "Hungaroring": "Hungaroring",
    "misano": "Misano World Circuit",
    "monza": "Monza Circuit",
    "nurburgring": "Nürburgring",
    "Paul_Ricard": "Circuit Paul Ricard",
    "Silverstone": "Silverstone",
    "Spa": "Circuit de Spa-Francorchamps",
    "Zandvoort": "Circuit Zandvoort",
    "Zolder": "Circuit Zolder",
    # Intercontinental GT Pack
    "Kyalami": "Kyalami Grand Prix Circuit",
    "Suzuka": "Suzuka Circuit",
    "Laguna_Seca": "WeatherTech Raceway Laguna Seca",
    "mount_panorama": "Mount Panorama Circuit",
    # 2020 GT World Challenge Pack
    "Imola": "Autodromo Enzo e Dino Ferrari",
    # British GT Pack
    "donington": "Donington Park",
    "oulton_park": "Oulton Park",
    "snetterton": "Snetterton Circuit",
    # American Track Pack
    "cota": "Circuit of the Americas",
    "indianapolis": "Indianapolis Motor Speedway",
    "watkins_glen": "Watkins Glen International",
    # 2023 GT World Challenge Pack
    "Valencia": "Circuit Ricardo Tormo Valencia",
    # GT2 Pack
    "red_bull_ring": "Red Bull Ring",
    # 24H Nurburgring Pack
    "nurburgring_24h": "24H Nürburgring",
}, default="")

ACC_TRACK_YEAR = map_str_to_str({  # year according to track's release date
    # Base
    "Barcelona": "2018",
    "brands_hatch": "2018",
    "Hungaroring": "2018",
    "misano": "2018",
    "monza": "2018",
    "nurburgring": "2018",
    "Paul_Ricard": "2018",
    "Silverstone": "2018",
    "Spa": "2018",
    "Zandvoort": "2018",
    "Zolder": "2018",
    # Intercontinental GT Pack
    "Kyalami": "2019",
    "Suzuka": "2019",
    "Laguna_Seca": "2019",
    "mount_panorama": "2019",
    # 2020 GT World Challenge Pack
    "Imola": "2020",
    # British GT Pack
    "donington": "2021",
    "oulton_park": "2021",
    "snetterton": "2021",
    # American Track Pack
    "cota": "2022",
    "indianapolis": "2022",
    "watkins_glen": "2022",
    # 2023 GT World Challenge Pack
    "Valencia": "2023",
    # GT2 Pack
    "red_bull_ring": "2024",
    # 24H Nurburgring Pack
    "nurburgring_24h": "2024",
}, default="")

ACC_TRACK_LENGTH = map_str_to_float({
    # Base
    "Barcelona": 4655.0,
    "brands_hatch": 3908.0,
    "Hungaroring": 4381.0,
    "misano": 4226.0,
    "monza": 5793.0,
    "nurburgring": 5137.0,
    "Paul_Ricard": 5770.0,
    "Silverstone": 5891.0,
    "Spa": 7004.0,
    "Zandvoort": 4252.0,
    "Zolder": 4011.0,
    # Intercontinental GT Pack
    "Kyalami": 4522.0,
    "Suzuka": 5807.0,
    "Laguna_Seca": 3602.0,
    "mount_panorama": 6213.0,
    # 2020 GT World Challenge Pack
    "Imola": 4959.0,
    # British GT Pack
    "donington": 4020.0,
    "oulton_park": 4307.0,
    "snetterton": 4779.0,
    # American Track Pack
    "cota": 5513.0,
    "indianapolis": 4167.0,
    "watkins_glen": 5552.0,
    # 2023 GT World Challenge Pack
    "Valencia": 4005.0,
    # GT2 Pack
    "red_bull_ring": 4318.0,
    # 24H Nurburgring Pack
    "nurburgring_24h": 25300.0,
}, default=0.0)

ACC_CAR_MODEL = map_str_to_str({
    # GT3 - 2018
    "amr_v12_vantage_gt3": "Aston Martin Vantage V12 GT3 2013",
    "audi_r8_lms": "Audi R8 LMS 2015",
    "bentley_continental_gt3_2016": "Bentley Continental GT3 2015",
    "bentley_continental_gt3_2018": "Bentley Continental GT3 2018",
    "bmw_m6_gt3": "BMW M6 GT3 2017",
    "jaguar_g3": "Emil Frey Jaguar G3 2012",
    "ferrari_488_gt3": "Ferrari 488 GT3 2018",
    "honda_nsx_gt3": "Honda NSX GT3 2017",
    "lamborghini_gallardo_rex": "Lamborghini Gallardo G3 Reiter 2017",
    "lamborghini_huracan_gt3": "Lamborghini Huracan GT3 2015",
    "lamborghini_huracan_st": "Lamborghini Huracan ST 2015",
    "lexus_rc_f_gt3": "Lexus RCF GT3 2016",
    "mclaren_650s_gt3": "McLaren 650S GT3 2015",
    "mercedes_amg_gt3": "Mercedes-AMG GT3 2015",
    "nissan_gt_r_gt3_2017": "Nissan GTR Nismo GT3 2015",
    "nissan_gt_r_gt3_2018": "Nissan GTR Nismo GT3 2018",
    "porsche_991_gt3_r": "Porsche 991 GT3 R 2018",
    # CUP - 2017
    "porsche_991ii_gt3_cup": "Porsche 991 II GT3 Cup 2017",
    # GT3 - 2019
    "amr_v8_vantage_gt3": "Aston Martin V8 Vantage GT3 2019",
    "audi_r8_lms_evo": "Audi R8 LMS Evo 2019",
    "honda_nsx_gt3_evo": "Honda NSX GT3 Evo 2019",
    "lamborghini_huracan_gt3_evo": "Lamborghini Huracan GT3 EVO 2019",
    "mclaren_720s_gt3": "McLaren 720S GT3 2019",
    "porsche_991ii_gt3_r": "Porsche 911 II GT3 R 2019",
    # GT4
    "alpine_a110_gt4": "Alpine A110 GT4 2018",
    "amr_v8_vantage_gt4": "Aston Martin Vantage AMR GT4 2018",
    "audi_r8_gt4": "Audi R8 LMS GT4 2018",
    "bmw_m4_gt4": "BMW M4 GT4 2018",
    "chevrolet_camaro_gt4r": "Chevrolet Camaro GT4.R 2017",
    "ginetta_g55_gt4": "Ginetta G55 GT4 2012",
    "ktm_xbow_gt4": "KTM X-BOW GT4 2016",
    "maserati_mc_gt4": "Maserati GranTurismo MC GT4 2016",
    "mclaren_570s_gt4": "McLaren 570S GT4 2016",
    "mercedes_amg_gt4": "Mercedes-AMG GT4 2016",
    "porsche_718_cayman_gt4_mr": "Porsche 718 Cayman GT4 Clubsport 2019",
    # GT3 - 2020
    "ferrari_488_gt3_evo": "Ferrari 488 GT3 Evo 2020",
    "mercedes_amg_gt3_evo": "Mercedes-AMG GT3 Evo 2020",
    # GT3 - 2021
    "bmw_m4_gt3": "BMW M4 GT3 2021",
    # Challengers Pack - 2022
    "audi_r8_lms_evo_ii": "Audi R8 LMS Evo II 2022",
    "bmw_m2_cs_racing": "BMW M2 Cup 2020",
    "ferrari_488_challenge_evo": "Ferrari 488 Challenge Evo 2020",
    "lamborghini_huracan_st_evo2": "Lamborghini Huracan ST Evo2 2021",
    "porsche_992_gt3_cup": "Porsche 992 GT3 Cup 2021",
    # GT3 - 2023-24
    "ferrari_296_gt3": "Ferrari 296 GT3 2023",
    "ford_mustang_gt3": "Ford Mustang GT3 2024",
    "lamborghini_huracan_gt3_evo2": "Lamborghini Huracan EVO2 GT3 2023",
    "mclaren_720s_gt3_evo": "McLaren 720S Evo GT3 2023",
    "porsche_992_gt3_r": "Porsche 992 GT3 R 2023",
    # GT2 - 2019-23
    "audi_r8_lms_gt2": "Audi R8 LMS GT2 2021",
    "ktm_xbow_gt2": "KTM X-BOW GT2 2021",
    "maserati_mc20_gt2": "Maserati GT2 2023",
    "mercedes_amg_gt2": "Mercedes-AMG GT2 2023",
    "porsche_935": "Porsche 935 GT2 2019",
    "porsche_991_gt2_rs_mr": "Porsche 991 II GT2 RS CS Evo 2023",
}, default="")

ACC_CAR_MODEL_ID = map_int_to_str({  # only used by UDP API
    # GT3 - 2018
    12: "amr_v12_vantage_gt3",
    3: "audi_r8_lms",
    11: "bentley_continental_gt3_2016",
    8: "bentley_continental_gt3_2018",
    7: "bmw_m6_gt3",
    14: "jaguar_g3",
    2: "ferrari_488_gt3",
    17: "honda_nsx_gt3",
    13: "lamborghini_gallardo_rex",
    4: "lamborghini_huracan_gt3",
    18: "lamborghini_huracan_st",
    15: "lexus_rc_f_gt3",
    5: "mclaren_650s_gt3",
    1: "mercedes_amg_gt3",
    10: "nissan_gt_r_gt3_2017",
    6: "nissan_gt_r_gt3_2018",
    0: "porsche_991_gt3_r",
    # CUP - 2017
    9: "porsche_991ii_gt3_cup",
    # GT3 - 2019
    20: "amr_v8_vantage_gt3",
    19: "audi_r8_lms_evo",
    21: "honda_nsx_gt3_evo",
    16: "lamborghini_huracan_gt3_evo",
    22: "mclaren_720s_gt3",
    23: "porsche_991ii_gt3_r",
    # GT4
    50: "alpine_a110_gt4",
    51: "amr_v8_vantage_gt4",
    52: "audi_r8_gt4",
    53: "bmw_m4_gt4",
    55: "chevrolet_camaro_gt4r",
    56: "ginetta_g55_gt4",
    57: "ktm_xbow_gt4",
    58: "maserati_mc_gt4",
    59: "mclaren_570s_gt4",
    60: "mercedes_amg_gt4",
    61: "porsche_718_cayman_gt4_mr",
    # GT3 - 2020
    24: "ferrari_488_gt3_evo",
    25: "mercedes_amg_gt3_evo",
    # GT3 - 2021
    30: "bmw_m4_gt3",
    # Challengers Pack - 2022
    31: "audi_r8_lms_evo_ii",
    27: "bmw_m2_cs_racing",
    26: "ferrari_488_challenge_evo",
    29: "lamborghini_huracan_st_evo2",
    28: "porsche_992_gt3_cup",
    # GT3 - 2023-24
    32: "ferrari_296_gt3",
    36: "ford_mustang_gt3",
    33: "lamborghini_huracan_gt3_evo2",
    35: "mclaren_720s_gt3_evo",
    34: "porsche_992_gt3_r",
    # GT2 - 2019-23
    80: "audi_r8_lms_gt2",
    82: "ktm_xbow_gt2",
    83: "maserati_mc20_gt2",
    84: "mercedes_amg_gt2",
    86: "porsche_935",
    85: "porsche_991_gt2_rs_mr",
}, default="")

ACC_CAR_CLASS = map_str_to_str({  # according to in-game class name
    # GT3 - 2018
    "amr_v12_vantage_gt3": "GT3",
    "audi_r8_lms": "GT3",
    "bentley_continental_gt3_2016": "GT3",
    "bentley_continental_gt3_2018": "GT3",
    "bmw_m6_gt3": "GT3",
    "jaguar_g3": "GT3",
    "ferrari_488_gt3": "GT3",
    "honda_nsx_gt3": "GT3",
    "lamborghini_gallardo_rex": "GT3",
    "lamborghini_huracan_gt3": "GT3",
    "lamborghini_huracan_st": "ST",
    "lexus_rc_f_gt3": "GT3",
    "mclaren_650s_gt3": "GT3",
    "mercedes_amg_gt3": "GT3",
    "nissan_gt_r_gt3_2017": "GT3",
    "nissan_gt_r_gt3_2018": "GT3",
    "porsche_991_gt3_r": "GT3",
    # CUP - 2017
    "porsche_991ii_gt3_cup": "CUP",
    # GT3 - 2019
    "amr_v8_vantage_gt3": "GT3",
    "audi_r8_lms_evo": "GT3",
    "honda_nsx_gt3_evo": "GT3",
    "lamborghini_huracan_gt3_evo": "GT3",
    "mclaren_720s_gt3": "GT3",
    "porsche_991ii_gt3_r": "GT3",
    # GT4
    "alpine_a110_gt4": "GT4",
    "amr_v8_vantage_gt4": "GT4",
    "audi_r8_gt4": "GT4",
    "bmw_m4_gt4": "GT4",
    "chevrolet_camaro_gt4r": "GT4",
    "ginetta_g55_gt4": "GT4",
    "ktm_xbow_gt4": "GT4",
    "maserati_mc_gt4": "GT4",
    "mclaren_570s_gt4": "GT4",
    "mercedes_amg_gt4": "GT4",
    "porsche_718_cayman_gt4_mr": "GT4",
    # GT3 - 2020
    "ferrari_488_gt3_evo": "GT3",
    "mercedes_amg_gt3_evo": "GT3",
    # GT3 - 2021
    "bmw_m4_gt3": "GT3",
    # Challengers Pack - 2022
    "audi_r8_lms_evo_ii": "GT3",
    "bmw_m2_cs_racing": "TCX",
    "ferrari_488_challenge_evo": "CHL",
    "lamborghini_huracan_st_evo2": "ST",
    "porsche_992_gt3_cup": "GTC",
    # GT3 - 2023-24
    "ferrari_296_gt3": "GT3",
    "ford_mustang_gt3": "GT3",
    "lamborghini_huracan_gt3_evo2": "GT3",
    "mclaren_720s_gt3_evo": "GT3",
    "porsche_992_gt3_r": "GT3",
    # GT2 - 2019-23
    "audi_r8_lms_gt2": "GT2",
    "ktm_xbow_gt2": "GT2",
    "maserati_mc20_gt2": "GT2",
    "mercedes_amg_gt2": "GT2",
    "porsche_935": "GT2",
    "porsche_991_gt2_rs_mr": "GT2",
}, default="NONE")

ACC_BRAKEBIAS_OFFSET = map_str_to_float({  # 1 offset = 0.2% bias
    # GT3 - 2018
    "amr_v12_vantage_gt3": -7,
    "audi_r8_lms": -14,
    "bentley_continental_gt3_2016": -7,
    "bentley_continental_gt3_2018": -7,
    "bmw_m6_gt3": -15,
    "jaguar_g3": -7,
    "ferrari_488_gt3": -17,
    "honda_nsx_gt3": -14,
    "lamborghini_gallardo_rex": -14,
    "lamborghini_huracan_gt3": -14,
    "lamborghini_huracan_st": -14,
    "lexus_rc_f_gt3": -14,
    "mclaren_650s_gt3": -17,
    "mercedes_amg_gt3": -14,
    "nissan_gt_r_gt3_2017": -15,
    "nissan_gt_r_gt3_2018": -15,
    "porsche_991_gt3_r": -21,
    # CUP - 2017
    "porsche_991ii_gt3_cup": -5,
    # GT3 - 2019
    "amr_v8_vantage_gt3": -7,
    "audi_r8_lms_evo": -14,
    "honda_nsx_gt3_evo": -14,
    "lamborghini_huracan_gt3_evo": -14,
    "mclaren_720s_gt3": -17,
    "porsche_991ii_gt3_r": -21,
    # GT4
    "alpine_a110_gt4": -15,
    "amr_v8_vantage_gt4": -20,
    "audi_r8_gt4": -15,
    "bmw_m4_gt4": -22,
    "chevrolet_camaro_gt4r": -18,
    "ginetta_g55_gt4": -18,
    "ktm_xbow_gt4": -20,
    "maserati_mc_gt4": -15,
    "mclaren_570s_gt4": -9,
    "mercedes_amg_gt4": -20,
    "porsche_718_cayman_gt4_mr": -20,
    # GT3 - 2020
    "ferrari_488_gt3_evo": -17,
    "mercedes_amg_gt3_evo": -14,
    # GT3 - 2021
    "bmw_m4_gt3": -14,
    # Challengers Pack - 2022
    "audi_r8_lms_evo_ii": -14,
    "bmw_m2_cs_racing": -17,
    "ferrari_488_challenge_evo": -13,
    "lamborghini_huracan_st_evo2": -14,
    "porsche_992_gt3_cup": -5,
    # GT3 - 2023-24
    "ferrari_296_gt3": -5,
    "ford_mustang_gt3": -14,
    "lamborghini_huracan_gt3_evo2": -14,
    "mclaren_720s_gt3_evo": -17,
    "porsche_992_gt3_r": -21,
    # GT2 - 2019-23
    "audi_r8_lms_gt2": -15,
    "ktm_xbow_gt2": -20,
    "maserati_mc20_gt2": -5,
    "mercedes_amg_gt2": -10,
    "porsche_935": -5,
    "porsche_991_gt2_rs_mr": -5,
}, default=0)

ACC_MAX_STEERING_RANGE = map_str_to_float({  # verified via motec data
    # GT3 - 2018
    "amr_v12_vantage_gt3": 320 * 2,
    "audi_r8_lms": 360 * 2,
    "bentley_continental_gt3_2016": 320 * 2,
    "bentley_continental_gt3_2018": 320 * 2,
    "bmw_m6_gt3": 283 * 2,
    "jaguar_g3": 360 * 2,
    "ferrari_488_gt3": 240 * 2,
    "honda_nsx_gt3": 310 * 2,
    "lamborghini_gallardo_rex": 360 * 2,
    "lamborghini_huracan_gt3": 310 * 2,
    "lamborghini_huracan_st": 310 * 2,
    "lexus_rc_f_gt3": 320 * 2,
    "mclaren_650s_gt3": 240 * 2,
    "mercedes_amg_gt3": 320 * 2,
    "nissan_gt_r_gt3_2017": 320 * 2,
    "nissan_gt_r_gt3_2018": 320 * 2,
    "porsche_991_gt3_r": 400 * 2,
    # CUP - 2017
    "porsche_991ii_gt3_cup": 400 * 2,
    # GT3 - 2019
    "amr_v8_vantage_gt3": 320 * 2,
    "audi_r8_lms_evo": 360 * 2,
    "honda_nsx_gt3_evo": 310 * 2,
    "lamborghini_huracan_gt3_evo": 310 * 2,
    "mclaren_720s_gt3": 240 * 2,
    "porsche_991ii_gt3_r": 400 * 2,
    # GT4
    "alpine_a110_gt4": 360 * 2,
    "amr_v8_vantage_gt4": 320 * 2,
    "audi_r8_gt4": 360 * 2,
    "bmw_m4_gt4": 246 * 2,
    "chevrolet_camaro_gt4r": 360 * 2,
    "ginetta_g55_gt4": 360 * 2,
    "ktm_xbow_gt4": 290 * 2,
    "maserati_mc_gt4": 450 * 2,
    "mclaren_570s_gt4": 240 * 2,
    "mercedes_amg_gt4": 246 * 2,
    "porsche_718_cayman_gt4_mr": 400 * 2,
    # GT3 - 2020
    "ferrari_488_gt3_evo": 240 * 2,
    "mercedes_amg_gt3_evo": 320 * 2,
    # GT3 - 2021
    "bmw_m4_gt3": 270 * 2,
    # Challengers Pack - 2022
    "audi_r8_lms_evo_ii": 360 * 2,
    "bmw_m2_cs_racing": 180 * 2,
    "ferrari_488_challenge_evo": 240 * 2,
    "lamborghini_huracan_st_evo2": 310 * 2,
    "porsche_992_gt3_cup": 270 * 2,
    # GT3 - 2023-24
    "ferrari_296_gt3": 400 * 2,
    "ford_mustang_gt3": 258 * 2,
    "lamborghini_huracan_gt3_evo2": 310 * 2,
    "mclaren_720s_gt3_evo": 240 * 2,
    "porsche_992_gt3_r": 400 * 2,
    # GT2 - 2019-23
    "audi_r8_lms_gt2": 360 * 2,
    "ktm_xbow_gt2": 291 * 2,
    "maserati_mc20_gt2": 240 * 2,
    "mercedes_amg_gt2": 246 * 2,
    "porsche_935": 360 * 2,
    "porsche_991_gt2_rs_mr": 360 * 2,
}, default=360 * 2)
