"""领域模型（纯数据，不依赖 ACC / ctypes / backend）。"""

from __future__ import annotations

from .model import (
    CAR_LOCATIONS,
    SESSION_PHASES,
    SESSION_TYPES,
    SOURCE_BROADCASTING,
    StandingRow,
    StandingsSnapshot,
)

__all__ = [
    "CAR_LOCATIONS",
    "SESSION_PHASES",
    "SESSION_TYPES",
    "SOURCE_BROADCASTING",
    "StandingRow",
    "StandingsSnapshot",
]
