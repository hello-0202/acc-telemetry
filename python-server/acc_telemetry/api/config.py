"""配置读写。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import VERSION
from .deps import get_config_repo

router = APIRouter(prefix="/api", tags=["config"])

# 只接受 UI 提供的档位，避免把后端跑进无效频率
SAMPLE_HZ_CHOICES = (100, 50, 20)
PUSH_HZ_CHOICES = (60, 50, 30)


def _values_to_payload(request: Request, values: dict) -> dict:
    return {
        "sampleHz": int(values.get("sampleHz", 100)),
        "pushHz": int(values.get("pushHz", 50)),
        "autoStart": bool(values.get("autoStart", True)),
        "backgroundResident": bool(values.get("backgroundResident", False)),
        "dbPath": str(request.app.state.db.path),
        "configPath": str(get_config_repo(request).path),
        "version": VERSION,
    }


def _coerce_choice(value, choices: tuple[int, ...]) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number in choices else None


@router.get("/config")
def get_config(request: Request) -> dict:
    values = get_config_repo(request).get_all()
    return _values_to_payload(request, values)


@router.post("/config")
def save_config(payload: dict, request: Request) -> dict:
    clean: dict = {}

    if "sampleHz" in payload:
        sample_hz = _coerce_choice(payload["sampleHz"], SAMPLE_HZ_CHOICES)
        if sample_hz is not None:
            clean["sampleHz"] = sample_hz

    if "pushHz" in payload:
        push_hz = _coerce_choice(payload["pushHz"], PUSH_HZ_CHOICES)
        if push_hz is not None:
            clean["pushHz"] = push_hz

    if "autoStart" in payload:
        clean["autoStart"] = bool(payload["autoStart"])
    if "backgroundResident" in payload:
        clean["backgroundResident"] = bool(payload["backgroundResident"])

    if clean:
        get_config_repo(request).set_many(clean)
        # 采集 / 广播 Task 每轮都会读取 bus.runtime，因此无需重启即可生效
        apply_runtime = getattr(request.app.state, "apply_runtime_config", None)
        if apply_runtime is not None:
            apply_runtime(clean)

    return get_config(request)
