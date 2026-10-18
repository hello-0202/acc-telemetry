"""ACC carModel → 友好名称 / 档位数。未知车型回退为格式化后的内部名。"""

from __future__ import annotations

from .resolver import prettify

CAR_MAP: dict[str, dict] = {
    "mercedes_amg_gt3_evo": {"name": "Mercedes-AMG GT3 EVO", "gearCount": 6},
    "mercedes_amg_gt3": {"name": "Mercedes-AMG GT3", "gearCount": 6},
    "ferrari_296_gt3": {"name": "Ferrari 296 GT3", "gearCount": 6},
    "ferrari_488_gt3": {"name": "Ferrari 488 GT3", "gearCount": 6},
    "porsche_991ii_gt3_r": {"name": "Porsche 991 II GT3 R", "gearCount": 6},
    "porsche_991_gt3_r": {"name": "Porsche 991 GT3 R", "gearCount": 6},
    "porsche_992_gt3_r": {"name": "Porsche 992 GT3 R", "gearCount": 6},
    "audi_r8_lms_evo_ii": {"name": "Audi R8 LMS EVO II", "gearCount": 6},
    "audi_r8_lms_evo": {"name": "Audi R8 LMS EVO", "gearCount": 6},
    "audi_r8_lms": {"name": "Audi R8 LMS", "gearCount": 6},
    "bmw_m4_gt3": {"name": "BMW M4 GT3", "gearCount": 6},
    "bmw_m6_gt3": {"name": "BMW M6 GT3", "gearCount": 6},
    "mclaren_720s_gt3_evo": {"name": "McLaren 720S GT3 EVO", "gearCount": 6},
    "mclaren_650s_gt3": {"name": "McLaren 650S GT3", "gearCount": 6},
    "lamborghini_huracan_gt3_evo2": {"name": "Lamborghini Huracan GT3 EVO2", "gearCount": 6},
    "lamborghini_huracan_gt3_evo": {"name": "Lamborghini Huracan GT3 EVO", "gearCount": 6},
    "honda_nsx_gt3_evo": {"name": "Honda NSX GT3 EVO", "gearCount": 6},
    "aston_martin_v8_vantage_gt3": {"name": "Aston Martin V8 Vantage GT3", "gearCount": 6},
    "amr_v12_vantage_gt3": {"name": "Aston Martin V12 Vantage GT3", "gearCount": 6},
    "bentley_continental_gt3_2018": {"name": "Bentley Continental GT3 2018", "gearCount": 6},
    "nissan_gt_r_gt3_2018": {"name": "Nissan GT-R Nismo GT3 2018", "gearCount": 6},
    "lexus_rc_f_gt3": {"name": "Lexus RC F GT3", "gearCount": 6},
    "emil_frey_jaguar_g3": {"name": "Emil Frey Jaguar G3", "gearCount": 6},
}


def resolve_car(car_model: str) -> tuple[str, str, int]:
    """返回 (id, 友好名, 档位数)。"""
    car_id = (car_model or "").strip()
    entry = CAR_MAP.get(car_id.lower())
    if entry:
        return car_id, entry["name"], int(entry["gearCount"])
    return car_id or "unknown", prettify(car_id) if car_id else "Unknown Car", 6
