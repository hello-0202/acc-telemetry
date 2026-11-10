"""``broadcasting.json`` 的发现 / 编码 / 端口解析。

这段逻辑原来在探针脚本里，抽到 ``acc_telemetry.sources.broadcasting_config`` 之后
后端服务也要用，**必须有测试兜住**：它踩过两个真机坑（UTF-16LE 无 BOM、
``updListenerPort`` 默认 0 = 广播关闭），弄错任一都会表现为「连不上但看不出原因」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acc_telemetry.sources.broadcasting_config import (  # noqa: E402
    FALLBACK_PORT,
    detect_encoding,
    load_broadcasting_config,
    read_text_any_encoding,
    resolve_port,
)

CONFIG = {
    "updListenerPort": 9000,
    "connectionPassword": "conn-secret",
    "commandPassword": "",
    "maxConnections": 1,
}


def _write(path: Path, data: dict, *, encoding: str = "utf-16-le", bom: bytes = b"") -> Path:
    path.write_bytes(bom + json.dumps(data).encode(encoding))
    return path


# ------------------------------------------------------------------ 编码
def test_detects_utf16le_without_bom() -> None:
    raw = json.dumps(CONFIG).encode("utf-16-le")

    assert detect_encoding(raw) == "utf-16-le"


def test_detects_utf16_with_bom() -> None:
    assert detect_encoding(json.dumps(CONFIG).encode("utf-16")) == "utf-16"


def test_detects_utf8_with_and_without_bom() -> None:
    text = json.dumps(CONFIG)
    assert detect_encoding(text.encode("utf-8")) == "utf-8-sig"
    assert detect_encoding(text.encode("utf-8-sig")) == "utf-8-sig"


def test_read_text_any_encoding_round_trips_utf16le(tmp_path: Path) -> None:
    path = _write(tmp_path / "broadcasting.json", CONFIG)

    text, encoding = read_text_any_encoding(path)

    assert encoding == "utf-16-le"
    assert json.loads(text)["updListenerPort"] == 9000


# ------------------------------------------------------------------ 读取
def test_load_parses_utf16le_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "broadcasting.json", CONFIG)

    config = load_broadcasting_config(path)

    assert config["exists"] is True
    assert config["encoding"] == "utf-16-le"
    assert config["port"] == 9000
    assert config["connection_password"] == "conn-secret"
    assert config["command_password"] == ""
    assert config["max_connections"] == 1
    assert config["error"] is None
    assert config["keys"] == sorted(CONFIG)


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    config = load_broadcasting_config(tmp_path / "nope.json")

    assert config["exists"] is False
    assert config["port"] == 0
    assert config["error"] is None


def test_broken_json_reports_error_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "broadcasting.json"
    path.write_bytes(b"{ not json")

    config = load_broadcasting_config(path)

    assert config["exists"] is True
    assert config["error"] is not None
    assert "JSON" in config["error"]


def test_non_object_top_level_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broadcasting.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert load_broadcasting_config(path)["error"] == "顶层必须是 JSON 对象"


def test_port_zero_means_broadcast_disabled(tmp_path: Path) -> None:
    path = _write(tmp_path / "broadcasting.json", {**CONFIG, "updListenerPort": 0})

    config = load_broadcasting_config(path)

    assert config["port"] == 0
    assert config["error"] is None  # 0 是合法值，不是错误
    assert resolve_port(0, config) is None


# ------------------------------------------------------------------ 端口决策
def test_explicit_port_wins_over_file() -> None:
    config = {"exists": True, "port": 9000, "error": None}

    assert resolve_port(9100, config) == 9100


def test_port_from_file_when_app_config_is_auto() -> None:
    assert resolve_port(0, {"exists": True, "port": 9000, "error": None}) == 9000


def test_falls_back_to_default_port_when_file_missing() -> None:
    assert resolve_port(0, {"exists": False, "port": 0, "error": None}) == FALLBACK_PORT
    assert resolve_port(0, None) == FALLBACK_PORT


def test_falls_back_to_default_port_when_file_unreadable() -> None:
    # 文件在但读不明白（编码坏了 / 手改坏）：给默认端口，让人有机会连上再看日志
    assert resolve_port(0, {"exists": True, "port": 0, "error": "不是合法 JSON"}) == FALLBACK_PORT


@pytest.mark.parametrize("garbage", ["", "abc", None, -1])
def test_non_numeric_configured_port_is_treated_as_auto(garbage) -> None:
    config = {"exists": True, "port": 9000, "error": None}

    assert resolve_port(garbage, config) == 9000
