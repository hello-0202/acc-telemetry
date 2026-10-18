"""全局配置。

数据库固定放在用户本地应用数据目录，避免打包到 Program Files 后无写权限：
    %LOCALAPPDATA%\\acc-telemetry\\telemetry.db
测试时可用环境变量 ACC_TELEMETRY_DB 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__

VERSION = __version__


def _local_app_data() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


APP_DIR = _local_app_data() / "acc-telemetry"
DB_PATH = Path(os.getenv("ACC_TELEMETRY_DB") or (APP_DIR / "telemetry.db"))

# 采样 100Hz（ACC 物理频率），推送 50Hz（契约建议 30-60Hz）
SAMPLE_HZ = 100
FRAME_HZ = 50
SAMPLE_INTERVAL = 1.0 / SAMPLE_HZ
FRAME_INTERVAL = 1.0 / FRAME_HZ

# 每圈降采样点数：数值越大曲线越精细，同时增加数据库体积
SAMPLES_PER_LAP = 1200

# 合法单圈时间范围，用于过滤 ACC 的哨兵值 / 异常数据
MIN_LAP_MS = 15_000
MAX_LAP_MS = 1_200_000

# 共享内存里表示「无成绩」的哨兵值
LAP_TIME_SENTINELS = (0, 99999999, 2147483647)


def ensure_db_dir() -> bool:
    """创建数据库目录；失败时返回 False（服务仍可运行，只是不能落库）。"""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False
