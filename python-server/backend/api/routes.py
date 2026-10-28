"""HTTP / WebSocket 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from acc_telemetry.config import VERSION

from .. import bus
from .broadcaster import connections
from .state import build_state

logger = logging.getLogger(__name__)

router = APIRouter()

# 进程 CPU 估算：用两次 /health 之间的 CPU 时间差 / 墙钟时间差
_cpu_mark = {"cpu": time.process_time(), "wall": time.monotonic()}

# 契约 JSON 只读一次（PyInstaller 里它在临时解包目录）
_contract_cache: dict | None = None


def _contract_path() -> Path:
    """定位 `contracts/telemetry-state.json`。

    - 源码运行：`python-server/contracts/...`（本文件在 backend/api/ 下，上溯两级）
    - PyInstaller：spec 里以 `contracts` 目录为 datas 打进 `sys._MEIPASS`
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "contracts" / "telemetry-state.json")
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "contracts" / "telemetry-state.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="telemetry-state contract not found")


def _cpu_percent() -> float:
    now_cpu = time.process_time()
    now_wall = time.monotonic()
    wall = now_wall - _cpu_mark["wall"]
    cpu = now_cpu - _cpu_mark["cpu"]
    _cpu_mark["cpu"] = now_cpu
    _cpu_mark["wall"] = now_wall
    if wall <= 0:
        return 0.0
    return round(min(100.0, max(0.0, cpu / wall * 100.0)), 1)


@router.get("/health")
async def health(request: Request) -> dict:
    db = request.app.state.db
    recorder = request.app.state.recorder
    provider = request.app.state.provider
    try:
        game_running = bool(provider.available())
        print(f"game_running: {game_running}")
    except Exception:  # noqa: BLE001
        game_running = False

    return {
        "ok": True,
        "gameRunning": game_running,
        "dbOk": bool(db.is_ok),
        "dbPath": str(db.path),
        "streaming": recorder.is_streaming,
        "trackId": recorder.current_track_id,
        "sessionId": recorder.current_session_id,
        "version": VERSION,
        # 总线诊断
        "connections": len(connections),
        "queueSize": bus.frame_queue.qsize(),
        "queueMax": bus.frame_queue.maxsize,
        "bufferSize": len(bus.latest_buffer),
        "producedFrames": bus.stats["produced"],
        "droppedFrames": bus.stats["dropped"],
        "writtenFrames": bus.stats["written"],
        "lapsRecorded": bus.stats["laps"],
        # 实时采集/推送参数与性能估算
        "sampleHz": int(bus.runtime.get("sample_hz", 100)),
        "pushHz": int(bus.runtime.get("push_hz", 50)),
        "cpuPercent": _cpu_percent(),
    }


@router.get("/api/contract")
async def telemetry_contract() -> dict:
    """返回主题状态契约（字段名 / 单位 / 取值 / 枚举 / 示例）。

    前端与主题作者都从这里取，不要在仓库里再抄一份副本。
    """
    global _contract_cache
    if _contract_cache is None:
        path = _contract_path()
        try:
            _contract_cache = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取状态契约失败：%s", path)
            raise HTTPException(
                status_code=500, detail=f"contract unreadable: {exc}"
            ) from exc
    return _contract_cache


@router.get("/api/state")
async def telemetry_state() -> dict:
    """当前主题状态快照（与 WS 里的 `{"type":"state"}` 完全一致）。

    主题调试、问题排查都用它：不必连 WS 就能看到后端此刻认为的数据。
    """
    return build_state()


@router.get("/api/tracks/{track_id}/geometry")
async def track_geometry(track_id: str, request: Request) -> dict:
    """返回 fastlane.ai 解析出的真实赛道几何（含左右边界数据）。

    库里还没有时按需解析一次：首次进入某条赛道（tracks 行还没建）或
    历史库缺 geometry 时，不能直接 404，否则前端只能画示意形状的默认地图。
    """
    track = await asyncio.to_thread(
        request.app.state.history.tracks.get, track_id
    )
    geometry = track.get("geometry") if track else None
    if not geometry:
        service = getattr(request.app.state, "geometry", None)
        if service is not None:
            try:
                geometry = await asyncio.to_thread(
                    service.get_or_parse, track_id, track_id
                )
            except Exception:  # noqa: BLE001
                geometry = None
    if not geometry:
        raise HTTPException(status_code=404, detail="track geometry not found")
    return geometry


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    with suppress(Exception):
        await websocket.send_json(
            {"type": "error", "code": code, "message": message, "error": message}
        )


@router.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    connections.add(websocket)
    recorder = websocket.app.state.recorder

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                command = json.loads(raw)
            except (TypeError, ValueError):
                await _send_error(websocket, "BAD_COMMAND", "消息不是合法 JSON")
                continue

            cmd = (
                str(command.get("cmd", "")).lower()
                if isinstance(command, dict)
                else ""
            )
            if cmd == "start":
                await recorder.start()
            elif cmd == "stop":
                await recorder.stop()
            else:
                await _send_error(
                    websocket,
                    "BAD_COMMAND",
                    '仅支持 {"cmd":"start"} / {"cmd":"stop"}',
                )
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 连接已经关闭时继续 receive/send 会抛 RuntimeError
        pass
    finally:
        connections.discard(websocket)
        # 最后一个客户端断开时结束记录，避免后台继续写库
        if not connections and recorder.is_streaming:
            await recorder.stop()
        with suppress(Exception):
            await websocket.close()
