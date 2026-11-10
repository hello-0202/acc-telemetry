"""``Documents/Assetto Corsa Competizione/Config/broadcasting.json`` 的发现与解析。

这个文件是 ACC 广播协议的**唯一开关**：``updListenerPort`` 为 0 时游戏根本不监听
任何端口（默认就是 0），改完还必须重启 ACC 才生效。之前这段逻辑写在
``tools/broadcast_probe.py`` 里，现在抽出来给后端服务复用 —— 工具目录不能当库 import。

两个真机实测的坑（ADR-0001 §6.1）：

1. 文件是 **UTF-16LE 且无 BOM**。``raw.decode("utf-8")`` 能「成功」解码
   （NUL 是合法 UTF-8 字节），但 ``json.loads`` 必炸 —— 很容易误判成文件损坏，
   所以必须先按字节形态判断编码。
2. ``updListenerPort`` 默认是 **0**，等于广播关闭。0 不是「随便挑个端口」的意思。

本模块只做纯文件读取，不建 socket、不 import backend。
"""

from __future__ import annotations

import codecs
import json
import os
from pathlib import Path
from typing import Any

# Steam 默认文档目录 / OneDrive 重定向 / 本机文档，按顺序找第一个存在的
BROADCASTING_JSON_RELATIVE = Path("Assetto Corsa Competizione") / "Config" / "broadcasting.json"

# 读不到文件时的兜底端口：ACC 社区文档里的默认值，也是探针的默认值
FALLBACK_PORT = 9000


def documents_candidates() -> tuple[Path, ...]:
    """候选「文档」目录，按优先级排序（重复项已去掉）。"""
    candidates: list[Path] = []
    userprofile = os.environ.get("USERPROFILE")
    onedrive = os.environ.get("OneDrive")
    for path in (
        Path(userprofile) / "Documents" if userprofile else None,
        Path(onedrive) / "Documents" if onedrive else None,
        Path.home() / "OneDrive" / "Documents",
        Path.home() / "Documents",
    ):
        if path is not None and path not in candidates:
            candidates.append(path)
    return tuple(candidates)


# 兼容旧名字：探针 / 报告里一直用 DOCUMENTS_CANDIDATES
DOCUMENTS_CANDIDATES = documents_candidates()


def detect_encoding(raw: bytes) -> str:
    """按字节形态判断编码。

    - 带 BOM → 交给 ``utf-8-sig`` / ``utf-16``；
    - 奇/偶位置大量 NUL → UTF-16LE / UTF-16BE（ACC 就是这一档，且**没有 BOM**）；
    - 其余当 UTF-8。
    """
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    head = raw[:128]
    if len(head) >= 4:
        odd = head[1::2].count(0)
        even = head[0::2].count(0)
        if odd > len(head[1::2]) * 0.6:
            return "utf-16-le"
        if even > len(head[0::2]) * 0.6:
            return "utf-16-be"
    return "utf-8-sig"


def read_text_any_encoding(path: Path) -> tuple[str, str]:
    """读文本 + 返回实际命中的编码。永远不抛（解不出来就用替换字符）。"""
    raw = Path(path).read_bytes()
    encoding = detect_encoding(raw)
    try:
        return raw.decode(encoding), encoding
    except (UnicodeDecodeError, UnicodeError):
        return raw.decode("utf-8", "replace"), "utf-8(replace)"


def find_broadcasting_json() -> Path | None:
    """找第一个存在的 ``broadcasting.json``，找不到返回 ``None``。"""
    for documents in documents_candidates():
        path = documents / BROADCASTING_JSON_RELATIVE
        if path.exists():
            return path
    return None


def load_broadcasting_config(path: str | Path | None = None) -> dict[str, Any]:
    """读配置，返回结构化结果（**永不抛**，失败信息放在 ``error`` 里）。

    返回字段：

    - ``path`` / ``exists`` / ``encoding``：来源与编码，排查用；
    - ``port``：``updListenerPort``（int），缺失或非法为 0；
    - ``connection_password`` / ``command_password``：口令原文，只在内存里用来注册；
    - ``max_connections``：游戏允许多少客户端；
    - ``keys``：原始字段名列表，判断「是不是不认识的版本」。

    注意 ``port <= 0`` 是**合法读取结果**，代表「广播被关掉了」，不是错误。
    """
    result: dict[str, Any] = {
        "path": None,
        "exists": False,
        "encoding": None,
        "port": 0,
        "connection_password": "",
        "command_password": "",
        "max_connections": None,
        "keys": [],
        "error": None,
        "candidates": [str(item / BROADCASTING_JSON_RELATIVE) for item in documents_candidates()],
    }

    target = Path(path) if path is not None else find_broadcasting_json()
    # 显式给的路径也可能不存在（用户刚清过配置 / 换过机器），别谎报 exists=True：
    # 上层 ``resolve_port`` 靠这个字段决定「用文件端口」还是「回退默认端口」。
    if target is None or not target.exists():
        return result

    result["path"] = str(target)
    result["exists"] = True
    try:
        text, encoding = read_text_any_encoding(target)
    except OSError as exc:
        result["error"] = f"读取失败：{exc}"
        return result
    result["encoding"] = encoding

    try:
        data = json.loads(text)
    except ValueError as exc:
        result["error"] = f"不是合法 JSON（编码 {encoding}）：{exc}"
        return result
    if not isinstance(data, dict):
        result["error"] = "顶层必须是 JSON 对象"
        return result

    result["port"] = _as_int(data.get("updListenerPort"))
    result["connection_password"] = _as_text(data.get("connectionPassword"))
    result["command_password"] = _as_text(data.get("commandPassword"))
    result["max_connections"] = data.get("maxConnections")
    result["keys"] = sorted(str(key) for key in data)
    return result


def resolve_port(configured: int, config: dict[str, Any] | None) -> int | None:
    """决定本次连接用哪个 UDP 端口；``None`` 表示「配置不可用」（广播被关）。

    规则（任务书 §4.3）：

    - 应用配置里显式给了端口（> 0）→ 用它，忽略文件；
    - 应用配置为 0 → 读文件：文件端口 > 0 用它，文件端口 <= 0 → ``None``（未配置）；
    - 文件读不到 / 读坏了 → 回退 ``FALLBACK_PORT``（9000）。
    """
    try:
        explicit = int(configured)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit

    if not config or not config.get("exists"):
        return FALLBACK_PORT
    if config.get("error"):
        # 文件在但读不明白：大概率是编码 / 手改坏了，回退默认端口让人能连上再看日志
        return FALLBACK_PORT

    port = _as_int(config.get("port"))
    return port if port > 0 else None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
