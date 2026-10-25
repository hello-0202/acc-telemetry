"""启动入口。

应用装配在 backend/api/server.py，默认使用 acc_telemetry 的共享内存 provider
和本机 SQLite。也可以直接运行本文件：

    python -m backend.main
"""

from __future__ import annotations

import uvicorn

from .api.server import create_app

import os


def _setup_debugger() -> None:
    """按需启动 debugpy 监听，供 VS Code attach。"""
    if os.environ.get("PYTHON_DEBUG") != "1":
        return
    import debugpy

    # 注意：调试端口不能和 uvicorn 的 8008 冲突
    debugpy.listen(("127.0.0.1", 5678))
    print("[debugpy] listening on 127.0.0.1:5678, waiting for attach...", flush=True)
    # 如果你希望后端暂停等调试器，取消下面这行注释；
    # 但 Tauri 启动时前端会等后端就绪，可能超时，所以默认不等待。
    # debugpy.wait_for_client()


def run(
    host: str = "127.0.0.1",
    port: int = 8008,
    *,
    collector_hz: float = 100,
    broadcast_hz: float = 50,
    **app_kwargs,
) -> None:
    _setup_debugger()
    app = create_app(
        collector_hz=collector_hz,
        broadcast_hz=broadcast_hz,
        **app_kwargs,
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
