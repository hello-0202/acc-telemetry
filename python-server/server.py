"""
ACC 遥测后端入口（PyInstaller sidecar）。

职责只保留启动逻辑：
  - 端口预检、崩溃日志、双击运行时保持窗口；
  - FastAPI 应用位于 acc_telemetry 包（routers → services → repositories）。

前置条件：
  pip install -r requirements.txt

用法：
  python server.py
  HTTP:      http://127.0.0.1:8008/health
  WebSocket: ws://127.0.0.1:8008/ws/telemetry
"""

from __future__ import annotations

import multiprocessing
import socket
import sys
import traceback
from pathlib import Path

# ---- 让脚本能找到本地包（acc_telemetry / pyACCSharedMemory）----
LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def _port_in_use(host: str, port: int) -> bool:
    """端口上是否已有服务在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _crash_log_path() -> Path:
    """打包后写到 exe 旁边，源码运行时写到脚本旁边。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else LIB_DIR
    return base / "crash_log.txt"


def main() -> int:
    host = "127.0.0.1"
    ports = range(8008, 8020)
    try:
        import uvicorn

        # 新的生产者-消费者总线架构（backend 包）
        from backend.api.server import app

        # 优先 8008；被占用时自动尝试 8009–8019。
        # uvicorn 绑定失败时抛的是 SystemExit，普通 except Exception 捕获不到，
        # 双击运行时窗口会直接消失，所以两种都接住。
        for port in ports:
            if _port_in_use(host, port):
                continue
            print(f"ACC telemetry server listening on http://{host}:{port}")
            print(f"WebSocket: ws://{host}:{port}/ws/telemetry")
            print("按 Ctrl+C 退出")
            try:
                uvicorn.run(app, host=host, port=port, log_config=None)
                return 0
            except SystemExit as exc:
                code = getattr(exc, "code", 1)
                if code in (0, None):
                    return 0
                print(f"端口 {port} 启动失败，尝试下一个端口…")
                continue
        raise OSError("8008–8019 端口都被占用，无法启动后端")
    except (Exception, SystemExit) as exc:
        message = traceback.format_exc()
        log_path = _crash_log_path()
        try:
            log_path.write_text(message, encoding="utf-8")
        except OSError:
            log_path = None

        print(f"\n启动失败：{exc}")
        if log_path is not None:
            print(f"详细信息已写入：{log_path}")
        try:
            input("按回车键退出...")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
