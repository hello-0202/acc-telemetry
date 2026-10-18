"""实时遥测 WebSocket。

协议：
  客户端 → {"cmd":"start"} / {"cmd":"stop"}
  服务端 → session / frame / lap / error / warning / stopped
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    service = websocket.app.state.telemetry
    stop_event = asyncio.Event()
    send_lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with send_lock:
            await websocket.send_json(message)

    async def send_error(code: str, message: str) -> None:
        await send({"type": "error", "code": code, "message": message, "error": message})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                command = json.loads(raw)
            except (TypeError, ValueError):
                await send_error("BAD_COMMAND", "消息不是合法 JSON")
                continue
            cmd = str(command.get("cmd", "")).lower() if isinstance(command, dict) else ""

            if cmd == "start":
                stop_event = asyncio.Event()
                await service.start(send, stop_event)
            elif cmd == "stop":
                await service.stop()
                await send({"type": "stopped"})
            else:
                await send_error("BAD_COMMAND", "仅支持 {\"cmd\":\"start\"} / {\"cmd\":\"stop\"}")
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 连接已关闭时继续 send 会抛 RuntimeError
        pass
    finally:
        await service.stop()
