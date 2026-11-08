"""全局配置。

存储位置由桌面壳（Tauri）在启动时通过环境变量指定：
    ACC_TELEMETRY_DB      完整数据库文件路径
    ACC_TELEMETRY_CONFIG  完整 config.json 文件路径
两者默认都在 <安装目录>\\data\\ 与 <安装目录>\\（见 src-tauri/src/main.rs 的 PathConfig）。
直接跑源码 / 测试时没有这些变量，回退到 %LOCALAPPDATA%\\acc-telemetry\\ 这套旧布局。

注意：DB_PATH / CONFIG_PATH 都是模块导入时求值的常量，运行中改路径必须重启后端。
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
# CONFIG_PATH 是「没有显式指定」时的默认值；真正落盘的位置由 config_path_for() 决定，
# 见 config_repo.py：不能用这个全局常量覆盖仓储传入的临时数据库路径。
CONFIG_PATH = Path(os.getenv("ACC_TELEMETRY_CONFIG") or (DB_PATH.parent / "config.json"))


def config_path_for(db_path: str | Path) -> Path:
    """按实际使用的数据库路径解析 config.json 的位置。

    桌面壳会显式设置 ACC_TELEMETRY_CONFIG（配置文件可以单独放安装目录根）；
    没有显式设置时跟随**传进来的** db 路径，保证测试里用临时库时配置也落在临时目录，
    不会误写用户真实配置（造测试数据前只设 ACC_TELEMETRY_DB 的老约定依然成立）。
    """
    explicit = os.getenv("ACC_TELEMETRY_CONFIG")
    if explicit:
        return Path(explicit)
    return Path(db_path).parent / "config.json"

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
