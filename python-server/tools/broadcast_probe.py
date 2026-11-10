#!/usr/bin/env python
"""ACC 广播协议探针（Broadcasting Network Protocol probe）。

它只做四件事，别的什么都不做：

1. 按 ACC 的 UDP 协议注册连接、请求 EntryList / TrackData、持续接收报文；
2. 把**原始报文**逐条落盘成夹具（``raw.bin``，格式见
   ``acc_telemetry/sources/fixture.py``），供离线回放；
3. 同时打开共享内存读玩家自己的数据，用于和广播交叉验证
   （carId 是否一一对应、坐标轴/符号/单位、名次是否一致、赛道长度是否可信）；
4. 把结论写成 ``report.md``（人读）+ ``summary.json``（机读）。

它**不修改**主工程的任何运行链路：不写数据库、不进 bus、不动采集循环。

运行方式（在 python-server 目录下）::

    python tools/broadcast_probe.py --host 127.0.0.1 --port 9000 \\
        --interval 250 --duration 60 --out tests/fixtures/broadcast/capture-01

前提：ACC 正在跑，且 ``Documents/Assetto Corsa Competizione/Config/broadcasting.json``
里 ``"updListenerPort"`` 的端口与 ``--port`` 一致（默认 9000），
``"connectionPassword"`` / ``"commandPassword"`` 为空或与下面参数一致。

其它常用参数：

    --name PROBE-1          注册时显示的客户端名（默认 acc-telemetry-probe）
    --password xxx          对应 connectionPassword（错密码会直接注册失败）
    --command-password xxx  对应 commandPassword（默认故意不匹配 → 只读连接）
    --no-shared-memory      不读共享内存（没有游戏 / 只需要抓报文时用）
    --ignore-game-check     跳过「游戏是否在跑」的预检（调试探针本身时用）
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from acc_telemetry.sources.broadcast import (  # noqa: E402
    BroadcastAdapter,
    decode_text,
    driver_display_name,
)
from acc_telemetry.sources.broadcasting_config import (  # noqa: E402,F401 - 后三个是给测试的转发
    BROADCASTING_JSON_RELATIVE,
    DOCUMENTS_CANDIDATES,
    detect_encoding,
    load_broadcasting_config,
    read_text_any_encoding,
)
from acc_telemetry.sources.fixture import FixtureWriter  # noqa: E402
from pyACCSharedMemory.acc_udp import (  # noqa: E402
    BroadcastingNetworkProtocol,
    InboundMessageTypes,
    set_register_message,
)

# 报文类型 → 中文名，报告里好读
MESSAGE_NAMES = {
    InboundMessageTypes.REGISTRATION_RESULT: "注册结果",
    InboundMessageTypes.REALTIME_UPDATE: "会话实时更新(2)",
    InboundMessageTypes.REALTIME_CAR_UPDATE: "车辆实时更新(3)",
    InboundMessageTypes.ENTRY_LIST: "名册头(4)",
    InboundMessageTypes.ENTRY_LIST_CAR: "名册单车(6)",
    InboundMessageTypes.TRACK_DATA: "赛道数据(5)",
    InboundMessageTypes.BROADCASTING_EVENT: "广播事件(7)",
}

# broadcasting.json 的路径规则 / 编码探测 / 解析都在 acc_telemetry.sources.broadcasting_config
# 里（后端服务要用同一份），这里只保留报告需要的「打码视图」。
# DOCUMENTS_CANDIDATES / BROADCASTING_JSON_RELATIVE / detect_encoding /
# read_text_any_encoding 由上面的 import 转发，测试可以直接从本模块取。

# 默认故意发一个不匹配的 commandPassword，拿到只读连接：探针只需要读数据，
# 不需要切镜头 / 换 HUD 页。若你的 broadcasting.json 恰好用它做密码，用
# --command-password 覆盖。
READ_ONLY_COMMAND_PASSWORD = "__acc_telemetry_read_only__"


class ProbeError(RuntimeError):
    """预检失败 / 连接失败：消息直接给用户看。"""


# --------------------------------------------------------------------------- 数据
@dataclass
class CarTiming:
    """一辆车收到 RealtimeCarUpdate 的时间统计。"""

    first: float
    last: float
    count: int = 1
    gap_sum: float = 0.0
    gap_min: float = float("inf")
    gap_max: float = 0.0

    def add(self, now: float) -> None:
        gap = now - self.last
        self.count += 1
        self.gap_sum += gap
        self.gap_min = min(self.gap_min, gap)
        self.gap_max = max(self.gap_max, gap)
        self.last = now

    @property
    def avg_gap(self) -> float:
        return self.gap_sum / (self.count - 1) if self.count > 1 else 0.0


# ------------------------------------------------------------------- 共享内存读取
class SharedMemoryTap:
    """只读玩家自己的数据，用来和广播对照（失败不影响探针继续跑）。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.error = ""
        self._gfx = None
        self._static = None

    def open(self) -> bool:
        if not self.enabled:
            self.error = "已用 --no-shared-memory 关闭"
            return False
        if sys.platform != "win32":
            self.error = "共享内存只在 Windows 上可用"
            return False
        try:
            from acc_telemetry.providers.acc_provider import game_running

            if not game_running():
                self.error = "未找到 ACC 共享内存段（游戏没开或还没进赛道）"
                return False
        except Exception as exc:  # noqa: BLE001 - 预检失败不该中断探针
            self.error = f"共享内存预检失败：{exc}"
            return False

        try:
            from pyACCSharedMemory.acc_data import ACCConstants, ACCGraphics, ACCStatic
            from pyACCSharedMemory.acc_mmap import MMapControl

            gfx = MMapControl(ACCConstants.MM_GRAPHICS_FILE_NAME, ACCGraphics)
            gfx.create(access_mode=0)
            static = MMapControl(ACCConstants.MM_STATIC_FILE_NAME, ACCStatic)
            static.create(access_mode=0)
        except Exception as exc:  # noqa: BLE001
            self.error = f"打开共享内存失败：{exc}"
            return False

        self._gfx, self._static = gfx, static
        return True

    def close(self) -> None:
        for control in (self._static, self._gfx):
            if control is not None:
                try:
                    control.close()
                except Exception:  # noqa: BLE001
                    pass
        self._gfx = self._static = None

    def sample(self) -> dict[str, Any] | None:
        if self._gfx is None or self._static is None:
            return None
        try:
            from acc_telemetry.providers.base import session_type_name
            from pyACCSharedMemory.acc_data import ACCConstants

            self._gfx.update()
            self._static.update()
            g = self._gfx.data
            s = self._static.data

            player_id = int(getattr(g, "playerCarID", -1))
            coords: dict[int, tuple[float, float]] = {}
            for index in range(ACCConstants.MAX_MAPPED_VEHICLES):
                car_id = int(g.carIDs[index])
                if car_id <= 0:
                    continue
                coord = g.carCoordinates[index]
                coords[car_id] = (float(coord.x), float(coord.z))

            return {
                "player_car_id": player_id,
                "player_xy": coords.get(player_id),
                "car_ids": sorted(coords),
                "car_coords": coords,
                "status": int(getattr(g, "status", -1)),
                "session_type": session_type_name(getattr(g, "session", -1)),
                "session_index": int(getattr(g, "sessionIndex", 0)),
                "position": int(getattr(g, "position", 0)),
                "speed_kmh": float(getattr(g, "speedKmh", 0.0)),
                "completed_laps": int(getattr(g, "completedLaps", 0)),
                "current_lap_ms": int(getattr(g, "iCurrentTime", 0)),
                "last_lap_ms": int(getattr(g, "iLastTime", 0)),
                "best_lap_ms": int(getattr(g, "iBestTime", 0)),
                # 共享内存的 delta 是「大小 + 符号位」，供与广播 deltaBest 对照
                "delta_lap_ms": int(getattr(g, "iDeltaLapTime", 0)),
                "delta_positive": int(getattr(g, "isDeltaPositive", 0)),
                "spline": float(getattr(g, "normalizedCarPosition", 0.0)),
                "distance_m": float(getattr(g, "distanceTraveled", 0.0)),
                "active_cars": int(getattr(g, "activeCars", 0)),
                "track_name": str(s.trackName).strip(),
                "track_configuration": str(getattr(s, "trackConfiguration", "")).strip(),
            }
        except Exception as exc:  # noqa: BLE001
            self.error = f"读取共享内存失败：{exc}"
            return None


# ------------------------------------------------------------------------- 探针
class Probe:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out_dir = Path(args.out)
        self.fixture_path = self.out_dir / "raw.bin"
        self.report_path = self.out_dir / "report.md"
        self.summary_path = self.out_dir / "summary.json"

        self.adapter = BroadcastAdapter()
        self.tap = SharedMemoryTap(enabled=not args.no_shared_memory)
        self.writer: FixtureWriter | None = None
        self.sock: socket.socket | None = None

        self.t0 = time.monotonic()
        self.message_counts: Counter[int] = Counter()
        self.packets = 0
        self.bytes_in = 0
        self.car_timing: dict[int, CarTiming] = {}
        self.entry_seen_at: dict[int, float] = {}
        self.resync_requests = 0
        self.cross_samples: list[dict[str, Any]] = []
        self.session_changes: list[tuple[float, int, int]] = []
        self.warnings: list[str] = []
        self.registration: dict[str, Any] = {}
        self.broadcasting_json: dict[str, Any] = {}
        self._last_session: tuple[int, int] | None = None
        self._next_entry_request = 0.0
        self._next_track_request = 0.0
        self._next_sample = 0.0
        self._next_progress = 0.0
        self._last_track_name = ""

    # ---------------------------------------------------------------- 时间
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    # ---------------------------------------------------------------- 主流程
    def run(self) -> int:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.broadcasting_json = inspect_broadcasting_json()
        print_broadcasting_json(self.broadcasting_json)
        self._preflight()

        if self.tap.open():
            print(f"共享内存：已连接（玩家 carId = {self._player_car_id()}）")
        else:
            print(f"共享内存：不可用 —— {self.tap.error}（交叉验证会缺失）")

        self.writer = FixtureWriter(self.fixture_path)
        self.writer.open()

        try:
            self._connect_and_stream()
        finally:
            if self.writer is not None:
                self.writer.close()
            self.tap.close()
            if self.sock is not None:
                self.sock.close()

        self._write_report()
        self._write_summary()
        print()
        print(f"报文落盘：{self.fixture_path}（{self.packets} 包 / {self.bytes_in} 字节）")
        print(f"探针报告：{self.report_path}")
        print(f"机读摘要：{self.summary_path}")
        return 0

    def _player_car_id(self) -> int:
        sample = self.tap.sample()
        return int(sample["player_car_id"]) if sample else -1

    def _preflight(self) -> None:
        info = self.broadcasting_json
        if info.get("exists") and info.get("keys") is not None:
            port = info.get("keys", {}).get("updListenerPort")
            if not port and not self.args.ignore_game_check:
                raise ProbeError(
                    "broadcasting.json 里 updListenerPort = 0（等于关闭广播），"
                    f"游戏不会监听任何端口。\n文件：{info.get('path')}\n"
                    "把它改成 9000（或任意空闲端口）并**重启 ACC**，例如：\n"
                    '  { "updListenerPort": 9000, "connectionPassword": "", "commandPassword": "" }\n'
                    "改完再用 --port 9000 运行本探针。"
                )
            if port and int(port) != int(self.args.port):
                self.warnings.append(
                    f"broadcasting.json 的 updListenerPort={port}，与 --port={self.args.port} 不一致；"
                    "以游戏配置为准，否则注册会超时。"
                )

        if not self.args.ignore_game_check and sys.platform == "win32":
            try:
                from acc_telemetry.providers.acc_provider import game_running
            except Exception as exc:  # noqa: BLE001
                raise ProbeError(f"无法加载共享内存模块：{exc}") from exc
            if not game_running():
                raise ProbeError(
                    "未检测到 ACC 游戏进程/共享内存段。\n"
                    "请先启动 ACC 并进入赛道（主菜单里广播协议是不工作的）。\n"
                    "只想验证探针本身可以用 --ignore-game-check 跳过这个检查。"
                )

    # ---------------------------------------------------------------- 网络
    def _connect_and_stream(self) -> None:
        args = self.args
        message = set_register_message(
            display_name=args.name,
            connection_password=args.password,
            command_password=args.command_password,
            realtime_update_interval=max(10, int(args.interval)),
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock = sock
        sock.settimeout(max(1.0, float(args.timeout)))

        print(f"注册：{args.host}:{args.port}（名称 {args.name}，间隔 {args.interval}ms）…")
        try:
            sock.connect((args.host, args.port))
            sock.send(message)
            response = sock.recv(65535)
        except socket.timeout as exc:
            raise ProbeError(
                f"{args.timeout}s 内没有收到注册响应。\n"
                "常见原因：游戏没进赛道 / broadcasting.json 的 updListenerPort 与 --port 不一致 / "
                "端口被防火墙拦了。"
            ) from exc
        except ConnectionResetError as exc:
            raise ProbeError(
                f"注册报文被拒绝（{args.host}:{args.port} 无人监听）。\n"
                "检查 broadcasting.json 是否存在、updListenerPort 是否等于 --port。"
            ) from exc
        except OSError as exc:
            raise ProbeError(f"UDP 连接失败：{exc}") from exc

        self._record(response, is_registration=True)

        registration = self.adapter.raw.registration
        self.registration = {
            "connection_id": int(registration.connectionId),
            "success": bool(registration.connectionSuccess),
            "is_read_only": bool(registration.isReadOnly),
            "error_message": decode_text(registration.errorMessage),
            "protocol_version": BroadcastingNetworkProtocol.BROADCASTING_PROTOCOL_VERSION,
            "realtime_interval_ms": int(args.interval),
        }
        print(
            "注册结果：{}  connectionId={}  isReadOnly={}  error={!r}".format(
                "成功" if self.registration["success"] else "失败",
                self.registration["connection_id"],
                self.registration["is_read_only"],
                self.registration["error_message"],
            )
        )
        if not self.registration["success"]:
            raise ProbeError(
                "注册失败：" + (self.registration["error_message"] or "游戏没有给出原因")
            )
        if self.registration["is_read_only"]:
            self.warnings.append(
                "连接是只读的（commandPassword 不匹配）：只能收数据，不能切镜头/换 HUD 页。"
                "本轮探针不需要写权限。"
            )

        # 先要名册和赛道信息，之后按周期补
        self._send(self.adapter.entry_list_request())
        self._send(self.adapter.track_data_request())
        self.adapter.entry_list_synced()

        duration = float(self.args.duration)
        print(f"开始接收，时长 {duration:.0f}s（Ctrl+C 提前结束）…")
        try:
            while True:
                now = self.elapsed()
                if now >= duration:
                    break
                sock.settimeout(min(0.25, max(0.01, duration - now)))
                payload: bytes | None
                try:
                    payload = sock.recv(65535)
                except socket.timeout:
                    payload = None
                except ConnectionResetError:
                    self.warnings.append(
                        "接收时连接被重置（游戏可能已退出赛道 / 关闭）。已按现有数据出报告。"
                    )
                    break
                if payload:
                    self._record(payload)
                self._periodic(now)
        except KeyboardInterrupt:
            print("\n收到 Ctrl+C，提前结束，仍然出报告。")

    def _send(self, payload: bytes | None) -> None:
        if payload is None or self.sock is None:
            return
        try:
            self.sock.send(payload)
        except OSError as exc:  # noqa: BLE001
            self.warnings.append(f"发送请求失败：{exc}")

    # ---------------------------------------------------------------- 收包
    def _record(self, payload: bytes, *, is_registration: bool = False) -> None:
        now = self.elapsed()
        if self.writer is not None:
            self.writer.write(int(now * 1000), payload)

        adapter = self.adapter
        known_before = set(adapter.raw.entryList.entryListCars)
        message_type = adapter.feed(payload, now=now)
        self.packets += 1
        self.bytes_in += len(payload)
        self.message_counts[message_type] += 1

        if is_registration:
            return

        if message_type == InboundMessageTypes.REALTIME_CAR_UPDATE:
            car_id = _u16(payload, 1)
            if car_id is not None:
                timing = self.car_timing.get(car_id)
                if timing is None:
                    self.car_timing[car_id] = CarTiming(first=now, last=now)
                    if self.args.verbose:
                        print(f"  [{now:7.2f}s] 首次收到 carId={car_id} 的实时更新")
                else:
                    timing.add(now)

        for car_id in sorted(set(adapter.raw.entryList.entryListCars) - known_before):
            self.entry_seen_at.setdefault(int(car_id), now)
            if self.args.verbose:
                row = adapter.snapshot(now=now).row_for(int(car_id))
                name = row.driver_name if row else "?"
                print(f"  [{now:7.2f}s] 名册新增 carId={car_id}  {name}")

        # 会话信息只由 REALTIME_UPDATE(2) 刷新；别的报文里这些字段还是默认 0，
        # 拿它们比较会误报「会话切换」。
        if message_type == InboundMessageTypes.REALTIME_UPDATE:
            session_key = (
                int(adapter.raw.sessionInfo.sessionIndex),
                int(adapter.raw.sessionInfo.sessionType),
            )
            if session_key != self._last_session:
                if self._last_session is not None:
                    self.session_changes.append((now, session_key[0], session_key[1]))
                    self.warnings.append(
                        f"检测到会话切换（{now:.1f}s，sessionIndex={session_key[0]}）："
                        "名册会跨会话混在一起，本次报告只看最后一个会话才有意义。"
                    )
                self._last_session = session_key

        track_name = adapter.track_name
        if track_name and track_name != self._last_track_name:
            self._last_track_name = track_name
            if self.args.verbose:
                print(f"  [{now:7.2f}s] 赛道：{track_name}  长度 {adapter.track_meters or '?'}m")

    def _periodic(self, now: float) -> None:
        if now >= self._next_entry_request:
            if self.adapter.needs_entry_list_sync:
                self.resync_requests += 1
                self.adapter.entry_list_synced()
            self._send(self.adapter.entry_list_request())
            self._next_entry_request = now + max(1.0, float(self.args.entry_refresh))

        if now >= self._next_track_request:
            self._send(self.adapter.track_data_request())
            self._next_track_request = now + 5.0

        if now >= self._next_sample:
            self._sample()
            self._next_sample = now + max(0.5, float(self.args.sample_interval))

        if now >= self._next_progress:
            self._next_progress = now + 5.0
            snapshot = self.adapter.snapshot(now=now)
            print(
                f"  [{now:6.1f}s] 包 {self.packets:5d}  名册 {self.adapter.cars_known:2d}/"
                f"{self.adapter.cars_expected:2d}  榜单车 {len(snapshot.rows):2d}  "
                f"赛道 {snapshot.track_meters or '?'}m"
            )

    def _sample(self) -> None:
        sample = self.tap.sample()
        if sample is None:
            return
        player_id = int(sample["player_car_id"])
        info = self.adapter.raw.entryList.entryListCars.get(player_id)
        broadcast = None
        if info is not None:
            broadcast = {
                "world": (float(info.worldPosX), float(info.worldPosY)),
                "position": int(info.position),
                "track_position": int(info.trackPosition),
                "spline": float(info.splinePosition),
                "speed_kmh": int(info.speedKmh),
                "completed_laps": int(info.completedLaps),
                "car_location": int(info.carLocation),
                "last_lap_ms": int(info.lastLap.laptimeMS),
                "best_lap_ms": int(info.bestSessionLap.laptimeMS),
                "current_lap_ms": int(info.currentLap.laptimeMS),
                # 原始 deltaBest（int32 ms）。0 是否为「无数据」由 analyze_deltas 判断。
                "delta_best_ms": int(info.deltaBest),
                "car_id": player_id,
                "driver_name": decode_text(info.currentDriverInfo.shortName),
            }
        self.cross_samples.append({"t": self.elapsed(), "sm": sample, "bc": broadcast})

    # ---------------------------------------------------------------- 报告
    def _write_report(self) -> None:
        report = build_report(self)
        self.report_path.write_text(report, encoding="utf-8")
        print()
        print(_console_digest(self))

    def _write_summary(self) -> None:
        summary = build_summary(self)
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _u16(payload: bytes, offset: int) -> int | None:
    try:
        (value,) = struct.unpack_from("<H", payload, offset)
    except (struct.error, TypeError):
        return None
    return int(value)


# ------------------------------------------------------------------ broadcasting.json
def inspect_broadcasting_json(path: Path | None = None) -> dict[str, Any]:
    """报告用的打码视图：口令只说「已设置 / 空」，不打印原文。

    真正的解析在 ``acc_telemetry.sources.broadcasting_config.load_broadcasting_config``，
    后端服务和探针共用同一份实现（否则两边对「端口 0」的解释迟早会分叉）。
    """
    config = load_broadcasting_config(path)
    found: dict[str, Any] = {
        "candidates": config["candidates"],
        "path": config["path"],
        "exists": config["exists"],
        "encoding": config["encoding"],
        "keys": {},
    }
    if not config["exists"]:
        return found
    if config.get("error"):
        found["error"] = config["error"]
        return found
    found["keys"] = {
        "updListenerPort": config["port"],
        "connectionPassword": "已设置" if config["connection_password"] else "空",
        "commandPassword": "已设置" if config["command_password"] else "空",
        "maxConnections": config["max_connections"],
        "otherKeys": [
            key
            for key in config["keys"]
            if key
            not in {"updListenerPort", "connectionPassword", "commandPassword", "maxConnections"}
        ],
    }
    return found


def print_broadcasting_json(info: dict[str, Any]) -> None:
    if info["exists"]:
        keys = info.get("keys", {})
        print(f"broadcasting.json：{info['path']}（编码 {info.get('encoding', '?')}）")
        if info.get("error"):
            print(f"  {info['error']}")
            return
        print(
            "  updListenerPort={}  connectionPassword={}  commandPassword={}  maxConnections={}".format(
                keys.get("updListenerPort"),
                keys.get("connectionPassword"),
                keys.get("commandPassword"),
                keys.get("maxConnections"),
            )
        )
        if not keys.get("updListenerPort"):
            print("  [!] updListenerPort 是 0/空 -> 游戏没有监听任何端口，广播不工作")
    else:
        print("broadcasting.json：未找到（下面这些位置都没有）")
        for candidate in info["candidates"]:
            print(f"  - {candidate}")


# ---------------------------------------------------------------------- 分析
# 广播坐标 / 共享内存坐标的候选对应关系。名字里的 sm 指共享内存 (x, z)，bc 指广播 (X, Y)。
COORD_HYPOTHESES: tuple[tuple[str, Callable[[float, float], tuple[float, float]]], ...] = (
    ("bcX = +sm.x, bcY = +sm.z", lambda x, z: (x, z)),
    ("bcX = +sm.x, bcY = -sm.z", lambda x, z: (x, -z)),
    ("bcX = -sm.x, bcY = +sm.z", lambda x, z: (-x, z)),
    ("bcX = -sm.x, bcY = -sm.z", lambda x, z: (-x, -z)),
    ("bcX = +sm.z, bcY = +sm.x", lambda x, z: (z, x)),
    ("bcX = +sm.z, bcY = -sm.x", lambda x, z: (z, -x)),
    ("bcX = -sm.z, bcY = +sm.x", lambda x, z: (-z, x)),
    ("bcX = -sm.z, bcY = -sm.x", lambda x, z: (-z, -x)),
)


def analyse_coordinates(samples: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for sample in samples:
        broadcast = sample.get("bc")
        shared = sample["sm"].get("player_xy")
        if not broadcast or not shared:
            continue
        bx, by = broadcast["world"]
        if (bx == 0.0 and by == 0.0) or (shared[0] == 0.0 and shared[1] == 0.0):
            continue
        pairs.append((shared, (bx, by)))

    if not pairs:
        return {"samples": 0, "note": "没有同时拿到共享内存和广播的玩家坐标"}

    scored: list[dict[str, Any]] = []
    for name, transform in COORD_HYPOTHESES:
        residuals = []
        dxs = []
        dys = []
        for (x, z), (bx, by) in pairs:
            cx, cy = transform(x, z)
            residuals.append(((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5)
            dxs.append(bx - cx)
            dys.append(by - cy)
        scored.append(
            {
                "hypothesis": name,
                "mean_residual_m": sum(residuals) / len(residuals),
                "max_residual_m": max(residuals),
                "mean_dx": sum(dxs) / len(dxs),
                "mean_dy": sum(dys) / len(dys),
            }
        )
    scored.sort(key=lambda item: item["mean_residual_m"])
    best = scored[0]
    distances = [((bx**2 + by**2) ** 0.5, (shared[0] ** 2 + shared[1] ** 2) ** 0.5) for shared, (bx, by) in pairs]
    mean_bc = sum(item[0] for item in distances) / len(distances)
    mean_sm = sum(item[1] for item in distances) / len(distances)
    return {
        "samples": len(pairs),
        "best": best,
        "runner_up": scored[1] if len(scored) > 1 else None,
        "mean_distance_bc": mean_bc,
        "mean_distance_sm": mean_sm,
        "mean_distance_gap": abs(mean_bc - mean_sm),
    }


def analyse_track_length(samples: list[dict[str, Any]], track_meters: float | None) -> dict[str, Any]:
    """用「总里程 / (完成圈 + 进度)」反推赛道长度，和 trackMeters 对照。"""
    estimates: list[float] = []
    for sample in samples:
        shared = sample["sm"]
        laps = shared.get("completed_laps") or 0
        distance = shared.get("distance_m") or 0.0
        spline = shared.get("spline") or 0.0
        if laps <= 0 or distance <= 0:
            continue
        denominator = laps + spline
        if denominator <= 0:
            continue
        estimates.append(distance / denominator)
    if not estimates:
        return {"samples": 0, "note": "共享内存里还没有「完成圈 + 进度」可用"}
    mean_estimate = sum(estimates) / len(estimates)
    result: dict[str, Any] = {
        "samples": len(estimates),
        "estimate_from_shared_memory_m": mean_estimate,
        "track_meters": track_meters,
    }
    if track_meters:
        result["relative_error"] = abs(mean_estimate - track_meters) / track_meters
    return result


def analyse_car_ids(probe: Probe) -> dict[str, Any]:
    entry = probe.adapter.raw.entryList.entryListCars
    broadcast_ids = {int(car_id) for car_id in entry if int(car_id) > 0}
    shared_ids: set[int] = set()
    player_id = -1
    for sample in probe.cross_samples:
        shared_ids |= set(sample["sm"].get("car_ids") or [])
        if player_id <= 0:
            player_id = int(sample["sm"].get("player_car_id", -1))
    return {
        "player_car_id": player_id,
        "player_in_broadcast": player_id in broadcast_ids if player_id > 0 else None,
        "broadcast_ids": sorted(broadcast_ids),
        "shared_memory_ids": sorted(shared_ids),
        "only_in_shared_memory": sorted(shared_ids - broadcast_ids),
        "only_in_broadcast": sorted(broadcast_ids - shared_ids),
        "focused_car_id": probe.adapter.focused_car_id,
    }


def analyse_positions(samples: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: Counter[tuple[int, int]] = Counter()
    for sample in samples:
        broadcast = sample.get("bc")
        if not broadcast:
            continue
        shared_position = int(sample["sm"].get("position") or 0)
        if shared_position <= 0:
            continue
        pairs[(shared_position, int(broadcast["position"]))] += 1
    mismatches = sum(count for (shared, bc), count in pairs.items() if shared != bc)
    total = sum(pairs.values())
    return {
        "samples": total,
        "mismatch_samples": mismatches,
        "pairs": {f"{shared}->{bc}": count for (shared, bc), count in sorted(pairs.items())},
    }


def analyse_deltas(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """玩家车的广播 ``deltaBest`` 与共享内存 ``iDeltaLapTime`` 对照。

    共享内存的 ``iDeltaLapTime`` 只是大小，符号由 ``isDeltaPositive`` 给出；
    广播 ``deltaBest`` 是带符号 int32。这里同时统计「广播为 0 但共享内存非 0」的次数，
    用来确认 0 到底是「无有效参考圈」还是「正好等于最佳」。
    """
    pairs: list[tuple[int, int]] = []
    bc_zero_sm_nonzero = 0
    sign_mismatch = 0
    for sample in samples:
        broadcast = sample.get("bc")
        if not broadcast:
            continue
        shared = sample["sm"]
        magnitude = int(shared.get("delta_lap_ms") or 0)
        if magnitude <= 0 or magnitude in (99999999, 2147483647):
            continue
        signed_shared = magnitude if int(shared.get("delta_positive") or 0) else -magnitude
        raw_broadcast = int(broadcast.get("delta_best_ms") or 0)
        if raw_broadcast == 0:
            bc_zero_sm_nonzero += 1
            continue
        pairs.append((signed_shared, raw_broadcast))
        if (signed_shared < 0) != (raw_broadcast < 0):
            sign_mismatch += 1
    if not pairs:
        return {
            "samples": 0,
            "broadcast_zero_while_shared_nonzero": bc_zero_sm_nonzero,
            "note": "没有同时可用的 delta 对照（需要两边都非 0）",
        }
    diffs = [abs(shared - broadcast) for shared, broadcast in pairs]
    return {
        "samples": len(pairs),
        "broadcast_zero_while_shared_nonzero": bc_zero_sm_nonzero,
        "sign_mismatch_samples": sign_mismatch,
        "mean_abs_diff_ms": round(sum(diffs) / len(diffs), 1),
        "max_abs_diff_ms": max(diffs),
        "pairs_preview": [
            {"shared": shared, "broadcast": broadcast} for shared, broadcast in pairs[:10]
        ],
    }


def analyse_update_coverage(probe: Probe, max_elapsed: float) -> dict[str, Any]:
    timings = probe.car_timing
    if not timings:
        return {"cars": 0, "note": "没有收到任何 RealtimeCarUpdate"}
    intervals = [
        gap
        for timing in timings.values()
        for gap in (timing.avg_gap,)
        if timing.count > 1
    ]
    return {
        "cars": len(timings),
        "cars_expected": probe.adapter.cars_expected,
        "time_to_first_update_s": {str(car_id): round(timing.first, 2) for car_id, timing in sorted(timings.items())},
        "last_update_age_s": {
            str(car_id): round(max_elapsed - timing.last, 2) for car_id, timing in sorted(timings.items())
        },
        "updates_per_car": {str(car_id): timing.count for car_id, timing in sorted(timings.items())},
        "avg_interval_s": round(sum(intervals) / len(intervals), 3) if intervals else None,
        "min_interval_s": round(min((t.gap_min for t in timings.values() if t.count > 1), default=0.0), 3),
        "max_interval_s": round(max((t.gap_max for t in timings.values()), default=0.0), 3),
    }


def analyse_entry_list(probe: Probe) -> dict[str, Any]:
    adapter = probe.adapter
    entry = adapter.raw.entryList.entryListCars
    missing: list[int] = []
    incomplete: dict[str, list[int]] = {"no_race_number": [], "no_driver_name": [], "no_team": []}
    for car_id, info in entry.items():
        car_id = int(car_id)
        if car_id <= 0:
            continue
        if int(info.entryId) != car_id:
            missing.append(car_id)
            continue
        if int(info.raceNumber) <= 0:
            incomplete["no_race_number"].append(car_id)
        if not decode_text(info.currentDriverInfo.shortName) and not decode_text(
            info.currentDriverInfo.lastName
        ):
            incomplete["no_driver_name"].append(car_id)
        if not decode_text(info.teamName):
            incomplete["no_team"].append(car_id)
    return {
        "expected": adapter.cars_expected,
        "known": adapter.cars_known,
        "complete": adapter.entry_list_complete,
        "missing_entry_ids": sorted(missing),
        "incomplete": incomplete,
        "resync_requests": probe.resync_requests,
        "arrival_time_s": {str(car_id): round(t, 2) for car_id, t in sorted(probe.entry_seen_at.items())},
        "sync_flag_now": adapter.needs_entry_list_sync,
    }


def build_summary(probe: Probe) -> dict[str, Any]:
    snapshot = probe.adapter.snapshot(now=probe.elapsed())
    duration = round(probe.elapsed(), 2)
    return {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": duration,
        "packets": probe.packets,
        "bytes": probe.bytes_in,
        "message_counts": {MESSAGE_NAMES.get(mt, str(mt)): count for mt, count in probe.message_counts.items()},
        "registration": probe.registration,
        "broadcasting_json": probe.broadcasting_json,
        "track": {
            "name": probe.adapter.track_name,
            "meters": snapshot.track_meters,
            "session_type": snapshot.session_type,
            "session_phase": snapshot.session_phase,
            "session_index": snapshot.session_index,
        },
        "entry_list": analyse_entry_list(probe),
        "car_ids": analyse_car_ids(probe),
        "coordinates": analyse_coordinates(probe.cross_samples),
        "track_length": analyse_track_length(probe.cross_samples, snapshot.track_meters),
        "positions": analyse_positions(probe.cross_samples),
        "deltas": analyse_deltas(probe.cross_samples),
        "update_coverage": analyse_update_coverage(probe, duration),
        "standings_preview": [
            {
                "car_id": row.car_id,
                "position": row.position,
                "driver": row.driver_name,
                "race_number": row.race_number,
                "laps": row.completed_laps,
                "last_lap_ms": row.last_lap_ms,
                "spline": row.spline_position,
                "location": row.car_location,
                "stale": row.stale,
            }
            for row in snapshot.rows[:10]
        ],
        "warnings": probe.warnings,
        "shared_memory_error": probe.tap.error,
    }


# ---------------------------------------------------------------------- 出报告
def _fmt_ms(value: int | None) -> str:
    if not value or value <= 0:
        return "-"
    minutes, rest = divmod(int(value), 60_000)
    seconds, millis = divmod(rest, 1000)
    if minutes:
        return f"{minutes}:{seconds:02d}.{millis:03d}"
    return f"{seconds}.{millis:03d}"


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}s"


def build_report(probe: Probe) -> str:
    adapter = probe.adapter
    now = probe.elapsed()
    snapshot = adapter.snapshot(now=now)
    meters_text = f"{snapshot.track_meters:.0f}m" if snapshot.track_meters else "未知"
    entry = analyse_entry_list(probe)
    car_ids = analyse_car_ids(probe)
    coordinates = analyse_coordinates(probe.cross_samples)
    track_length = analyse_track_length(probe.cross_samples, snapshot.track_meters)
    positions = analyse_positions(probe.cross_samples)
    deltas = analyse_deltas(probe.cross_samples)
    coverage = analyse_update_coverage(probe, now)

    lines: list[str] = []
    add = lines.append

    add("# ACC 广播协议探针报告")
    add("")
    add(f"- 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"- 时长：{now:.1f}s（目标 {probe.args.duration}s）")
    add(f"- 目标：`{probe.args.host}:{probe.args.port}`，注册名 `{probe.args.name}`，"
        f"interval={probe.args.interval}ms，协议版本 v{BroadcastingNetworkProtocol.BROADCASTING_PROTOCOL_VERSION}")
    add(f"- 报文：{probe.packets} 个 / {probe.bytes_in} 字节 → `{probe.fixture_path.name}`")
    add("")

    # ---- 1. broadcasting.json
    add("## 1. broadcasting.json")
    add("")
    info = probe.broadcasting_json
    if info["exists"]:
        keys = info.get("keys", {})
        add(f"- 路径：`{info['path']}`")
        add(f"- 文件编码：{info.get('encoding', '?')}（ACC 实际写的是 UTF-16LE 无 BOM，直接用 utf-8 读会失败）")
        if info.get("error"):
            add(f"- **解析失败**：{info['error']}")
        else:
            add(f"- `updListenerPort` = {keys.get('updListenerPort')}（须与 `--port` 一致）")
            if not keys.get("updListenerPort"):
                add("- **端口为 0 = 广播关闭**，游戏不会监听；改成 9000 并重启游戏才可用。")
            add(f"- `connectionPassword`：{keys.get('connectionPassword')}")
            add(f"- `commandPassword`：{keys.get('commandPassword')}（为空 = 可写；不匹配 = 只读）")
            add(f"- `maxConnections` = {keys.get('maxConnections')}")
            others = keys.get("otherKeys") or []
            if others:
                add(f"- 其它字段：{', '.join(f'`{key}`' for key in others)}")
    else:
        add("- **未找到**，检查过这些位置：")
        for candidate in info["candidates"]:
            add(f"  - `{candidate}`")
        add("- 说明：AC 单机模式下如果文件不存在，游戏会用默认端口 9000；"
            "但建议手动创建，避免端口/口令不明。")
    add("")

    # ---- 2. 注册
    add("## 2. 注册结果")
    add("")
    registration = probe.registration or {}
    add(f"- `connectionId` = {registration.get('connection_id')}")
    add(f"- `connectionSuccess` = {registration.get('success')}")
    add(f"- `isReadOnly` = {registration.get('is_read_only')}")
    add(f"- `errorMessage` = `{registration.get('error_message') or ''}`")
    add("")

    # ---- 3. 报文统计
    add("## 3. 报文统计")
    add("")
    add("| 类型 | 数量 |")
    add("|---|---|")
    for message_type, count in sorted(probe.message_counts.items()):
        add(f"| {MESSAGE_NAMES.get(message_type, f'未知({message_type})')} | {count} |")
    add("")

    # ---- 4. 赛道
    add("## 4. TrackData")
    add("")
    add(f"- `trackName` = `{adapter.track_name}`")
    add(f"- `trackMeters` = {snapshot.track_meters}")
    add(f"- 会话：{snapshot.session_type} / {snapshot.session_phase}（sessionIndex={snapshot.session_index}）")
    if track_length.get("samples"):
        add(f"- 共享内存反推长度：{track_length['estimate_from_shared_memory_m']:.0f}m"
            f"（{track_length['samples']} 个样本，相对误差 {track_length.get('relative_error', 0) * 100:.1f}%）")
    else:
        add(f"- 共享内存反推长度：{track_length.get('note')}")
    add("")

    # ---- 5. EntryList
    add("## 5. EntryList")
    add("")
    add(f"- 游戏报告的车辆数 `carEntryCount` = {entry['expected']}")
    add(f"- 实际拿到名册的车 = {entry['known']}（完整：{'是' if entry['complete'] else '否'}）")
    add(f"- 重同步请求次数 = {entry['resync_requests']}")
    if entry["missing_entry_ids"]:
        add(f"- 有 carId 但名册数据缺失：{entry['missing_entry_ids']}")
    if any(entry["incomplete"].values()):
        add(f"- 字段缺失：{entry['incomplete']}")
    if entry["arrival_time_s"]:
        last_arrival = max(entry["arrival_time_s"].values())
        add(f"- 名册收齐耗时：约 {last_arrival:.1f}s")
    add("")
    add("| carId | 车号 | 车手 | 车队 |")
    add("|---|---|---|---|")
    cars = sorted(adapter.raw.entryList.entryListCars.items())
    for car_id, car in cars[:60]:
        driver = driver_display_name(car) or "-"
        add(
            f"| {int(car_id)} | {int(car.raceNumber) or '-'} | {driver} | "
            f"{decode_text(car.teamName) or '-'} |"
        )
    if len(cars) > 60:
        add(f"| … | 其余 {len(cars) - 60} 辆见 summary.json | | |")
    add("")

    # ---- 6. carId 对照
    add("## 6. carId 与共享内存对照")
    add("")
    add(f"- 共享内存 `playerCarID` = {car_ids['player_car_id']}")
    add(f"- 玩家在广播里出现过：{car_ids['player_in_broadcast']}")
    add(f"- 广播 `focusedCarId` = {car_ids['focused_car_id']}"
        f"（{'与玩家一致' if car_ids['focused_car_id'] == car_ids['player_car_id'] else '与玩家不一致，不要用它判断玩家'}）")
    add(f"- 只在共享内存里出现的 carId：{car_ids['only_in_shared_memory'] or '无'}")
    add(f"- 只在广播里出现的 carId：{car_ids['only_in_broadcast'] or '无'}")
    add("")

    # ---- 7. 坐标
    add("## 7. 世界坐标对照（玩家车）")
    add("")
    if coordinates.get("samples"):
        best = coordinates["best"]
        add(f"- 有效样本：{coordinates['samples']} 个")
        add(f"- 最匹配的假设：**{best['hypothesis']}**")
        add(f"  - 平均残差 {best['mean_residual_m']:.3f}m，最大残差 {best['max_residual_m']:.3f}m")
        add(f"  - 平均偏移 dx={best['mean_dx']:.3f}m，dy={best['mean_dy']:.3f}m")
        if coordinates.get("runner_up"):
            runner = coordinates["runner_up"]
            add(f"- 次优假设：{runner['hypothesis']}（平均残差 {runner['mean_residual_m']:.3f}m）")
        add(f"- 模长对照：广播 {coordinates['mean_distance_bc']:.1f}m vs 共享内存 "
            f"{coordinates['mean_distance_sm']:.1f}m（差 {coordinates['mean_distance_gap']:.1f}m）")
        add("")
        add("> 残差接近 0 说明两条通道的世界坐标**同源同单位**（米），可以直接混用；"
            "残差是常量说明只是原点平移。")
    else:
        add(f"- {coordinates.get('note')}")
        add("- 需要游戏在赛道里、且共享内存可读才能做这项对照。")
    add("")

    # ---- 8. 更新覆盖
    add("## 8. RealtimeCarUpdate 覆盖与频率")
    add("")
    if coverage.get("cars"):
        add(f"- 收到实时更新的车：{coverage['cars']} 辆（游戏报告 {coverage['cars_expected']} 辆）")
        add(f"- 平均间隔 {_fmt_seconds(coverage['avg_interval_s'])}，"
            f"最短 {_fmt_seconds(coverage['min_interval_s'])}，最长 {_fmt_seconds(coverage['max_interval_s'])}")
        arrivals = coverage["time_to_first_update_s"]
        if arrivals:
            add(f"- 首帧最晚在 {max(arrivals.values()):.1f}s 收到")
        ages = coverage["last_update_age_s"]
        stale = {car_id: age for car_id, age in ages.items() if age > 5.0}
        if stale:
            add(f"- 采集结束时已超 5s 未更新的车：{stale}")
        add("")
        add("| carId | 首帧(s) | 更新次数 | 结束前最后更新(s ago) |")
        add("|---|---|---|---|")
        for car_id in sorted(arrivals, key=lambda key: int(key)):
            add(f"| {car_id} | {arrivals[car_id]:.2f} | {coverage['updates_per_car'][car_id]} | "
                f"{ages[car_id]:.2f} |")
    else:
        add(f"- {coverage.get('note')}")
    add("")

    # ---- 9. 名次
    add("## 9. 名次对照（玩家）")
    add("")
    add(f"- 样本 {positions['samples']} 个，不一致 {positions['mismatch_samples']} 个")
    if positions["pairs"]:
        add(f"- 共享内存 -> 广播：{positions['pairs']}")
    add("")
    add("### Delta 对照（玩家）")
    add("")
    if deltas.get("samples"):
        add(f"- 可用样本 {deltas['samples']} 个，平均差 {deltas['mean_abs_diff_ms']}ms，"
            f"最大差 {deltas['max_abs_diff_ms']}ms")
        add(f"- 符号不一致：{deltas['sign_mismatch_samples']} 次")
        add(f"- 广播 `deltaBest=0` 而共享内存 delta≠0："
            f"{deltas['broadcast_zero_while_shared_nonzero']} 次")
        if deltas["broadcast_zero_while_shared_nonzero"]:
            add("- **倾向结论**：广播的 0 更像「无有效参考圈」，适配器按 `None` 处理是合理的。")
    else:
        add(f"- {deltas.get('note')}")
        if deltas.get("broadcast_zero_while_shared_nonzero"):
            add(f"- 广播 `deltaBest=0` 而共享内存 delta≠0："
                f"{deltas['broadcast_zero_while_shared_nonzero']} 次")
    add("")

    # ---- 10. 适配器组装结果
    add("## 10. 适配器组装出的榜单（前 10 行）")
    add("")
    add("| P | carId | 车号 | 车手 | 圈 | 上圈 | 进度 | 位置 | 新鲜 |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in snapshot.rows[:10]:
        spline = "-" if row.spline_position is None else f"{row.spline_position:.3f}"
        freshness = "旧" if row.stale else "新"
        add(
            f"| {row.position or '-'} | {row.car_id} | {row.race_number or '-'} | "
            f"{row.driver_name or '-'} | {row.completed_laps} | {_fmt_ms(row.last_lap_ms)} | "
            f"{spline} | {row.car_location} | {freshness} |"
        )
    add("")

    # ---- 11. 八个问题
    add("## 11. 任务书第 5 节的 8 个问题")
    add("")
    add(f"1. **broadcasting.json 位置/字段/是否需要特殊启动参数** —— "
        f"{'找到 `' + str(info['path']) + '`（' + str(info.get('encoding')) + '）' if info['exists'] else '本机未找到，需要确认游戏文档目录'}；"
        f"`updListenerPort={info.get('keys', {}).get('updListenerPort')}`；字段见第 1 节。"
        f"单机下注册{'成功' if registration.get('success') else '失败'}。")
    add(f"2. **注册结果** —— connectionId={registration.get('connection_id')}，"
        f"isReadOnly={registration.get('is_read_only')}，errorMessage="
        f"`{registration.get('error_message') or ''}`。")
    add(f"3. **EntryList 是否含全部车辆** —— 期望 {entry['expected']} 辆，拿到 {entry['known']} 辆，"
        f"重同步 {entry['resync_requests']} 次；缺失字段见第 5 节。")
    add(f"4. **carId 是否与共享内存一一对应** —— 见第 6 节：只在共享内存 "
        f"{len(car_ids['only_in_shared_memory'])} 个，只在广播 {len(car_ids['only_in_broadcast'])} 个；"
        f"玩家在广播中：{car_ids['player_in_broadcast']}。")
    if coordinates.get("samples"):
        add(f"5. **坐标轴/符号/单位/精度** —— 最匹配：{coordinates['best']['hypothesis']}，"
            f"平均残差 {coordinates['best']['mean_residual_m']:.3f}m（≈0 即同源同单位）。")
    else:
        add("5. **坐标轴/符号/单位/精度** —— 本次没有拿到对照样本（需要共享内存 + 广播同时有玩家数据）。")
    if coverage.get("cars"):
        add(f"6. **RealtimeCarUpdate 覆盖** —— {coverage['cars']}/{coverage['cars_expected']} 辆车，"
            f"平均间隔 {_fmt_seconds(coverage['avg_interval_s'])}，首帧最晚 "
            f"{max(coverage['time_to_first_update_s'].values() or [0]):.1f}s。")
    else:
        add("6. **RealtimeCarUpdate 覆盖** —— 本次没有收到车辆实时更新。")
    if track_length.get("samples"):
        add(f"7. **trackMeters 是否可靠** —— trackMeters={meters_text}，"
            f"共享内存反推 {track_length['estimate_from_shared_memory_m']:.0f}m，"
            f"相对误差 {track_length.get('relative_error', 0) * 100:.2f}%（可用于 "
            "splinePosition × 长度 换算）。")
    else:
        add(f"7. **trackMeters 是否可靠** —— trackMeters={meters_text}；"
            f"缺共享内存里程样本，无法交叉验证（{track_length.get('note')}）。")
    add(f"8. **position 是否等于游戏内名次** —— 见第 9 节：{positions['samples']} 个样本里 "
        f"{positions['mismatch_samples']} 个不一致。")
    add("")

    # ---- 12. 告警
    add("## 12. 告警与注意事项")
    add("")
    if probe.warnings:
        for warning in probe.warnings:
            add(f"- {warning}")
    else:
        add("- 无")
    if probe.tap.error:
        add(f"- 共享内存：{probe.tap.error}")
    add("")
    add("## 13. 需要人眼确认的")
    add("")
    add("- [ ] 第 5 节名册里的车手名/车队/车号与游戏内 UI 是否一致（含中文 / 特殊字符）。")
    add("- [ ] 第 9 节名次在排位 / 正赛 / 多组别（cupPosition）下是否与游戏内一致。")
    add("- [ ] 第 8 节的刷新频率是否符合预期（默认 interval=250ms → 每车约 4Hz）。")
    add("- [ ] 若第 7 节残差不是 0，请确认是否为「同一世界坐标系的不同原点」。")
    add("")
    return "\n".join(lines)


def _console_digest(probe: Probe) -> str:
    adapter = probe.adapter
    snapshot = adapter.snapshot(now=probe.elapsed())
    entry = analyse_entry_list(probe)
    top = ", ".join(f"P{row.position}={row.driver_name or row.car_id}" for row in snapshot.rows[:3])
    lines = [
        "—— 摘要 ——",
        f"注册：connectionId={probe.registration.get('connection_id')} "
        f"isReadOnly={probe.registration.get('is_read_only')}",
        f"报文：{probe.packets} 个，车辆实时更新 "
        f"{probe.message_counts.get(InboundMessageTypes.REALTIME_CAR_UPDATE, 0)} 个",
        f"名册：{entry['known']}/{entry['expected']} 辆（完整={entry['complete']}）",
        f"赛道：{adapter.track_name or '?'}  {snapshot.track_meters or '?'}m  "
        f"{snapshot.session_type}/{snapshot.session_phase}",
        f"榜单：{len(snapshot.rows)} 辆车已组装" + (f"，前 3 名 {top}" if top else ""),
    ]
    return "\n".join(lines)


# -------------------------------------------------------------------------- CLI
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="broadcast_probe.py",
        description="ACC 广播协议探针：注册 / 抓报文 / 交叉验证 / 出报告（不改主工程任何链路）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例（在 python-server 目录下运行）：\n"
            "  python tools/broadcast_probe.py --host 127.0.0.1 --port 9000 \\\n"
            "      --interval 250 --duration 60 --out tests/fixtures/broadcast/capture-01\n"
            "\n"
            "产出：\n"
            "  <out>/raw.bin      原始报文夹具（[u8 type][...]，格式见 acc_telemetry/sources/fixture.py）\n"
            "  <out>/report.md    人读报告（含任务书第 5 节 8 个问题的答案）\n"
            "  <out>/summary.json 机读摘要\n"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="UDP 主机（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=9000, help="UDP 端口，须等于 broadcasting.json 的 updListenerPort（默认 9000）")
    parser.add_argument("--interval", type=int, default=250, help="realtimeUpdateInterval 毫秒（默认 250）")
    parser.add_argument("--duration", type=float, default=60.0, help="采集时长秒（默认 60）")
    parser.add_argument("--out", default="tests/fixtures/broadcast/probe", help="输出目录（默认 tests/fixtures/broadcast/probe）")
    parser.add_argument("--name", default="acc-telemetry-probe", help="注册显示名（默认 acc-telemetry-probe）")
    parser.add_argument("--password", default="", help="connectionPassword（错密码直接注册失败）")
    parser.add_argument(
        "--command-password",
        default=READ_ONLY_COMMAND_PASSWORD,
        help="commandPassword（默认故意不匹配 → 只读连接；需要写权限时显式传值）",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="注册等待秒数（默认 10）")
    parser.add_argument("--entry-refresh", type=float, default=10.0, help="名册补拉间隔秒（默认 10）")
    parser.add_argument("--sample-interval", type=float, default=1.0, help="共享内存采样间隔秒（默认 1）")
    parser.add_argument("--no-shared-memory", action="store_true", help="不读共享内存（缺交叉验证）")
    parser.add_argument("--ignore-game-check", action="store_true", help="跳过游戏在跑预检")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个新出现的车辆 / 名册变化")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return Probe(args).run()
    except ProbeError as exc:
        sys.stdout.flush()
        print(f"\n探针无法继续：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("\n已中止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
