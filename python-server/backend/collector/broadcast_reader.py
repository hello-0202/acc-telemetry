"""广播数据采集 Task（Phase 2）：ACC UDP → ``bus.latest_standings``。

职责边界（刻意写死，别往里加东西）：

- **只读**：注册时故意发一个不匹配的 ``commandPassword``，拿只读连接
  （``isReadOnly=True``）。切镜头 / 换 HUD 页不需要，也就不该有写权限。
- **只喂适配器**：UDP 包全部交给 ``BroadcastAdapter.feed()``，本模块不认识任何
  ACC 结构体、不做字段翻译（ADR-0001 §2.1）。
- **节流组装**：包到得再快也只按 ``SNAPSHOT_INTERVAL_S``（4Hz）组装一次快照，
  广播绝不驱动 30Hz 的 state 或 100Hz 的采集。
- **失败隔离**：任何异常都吞在这里、只记日志。广播坏了不能影响录制 / 圈次 / HUD，
  更不能写 ``state_error``。

生命周期（任务书 §6）::

    IDLE（游戏没开 / 广播关闭 / 端口为 0）
      │ 游戏进赛道 && 配置可用
      ▼
    CONNECTING（只读注册，等 REGISTRATION_RESULT）
      │ 成功                    │ 超时 / 被拒
      ▼                        ▼
    CONNECTED ─ 4Hz → bus ──→  BACKOFF(1/2/5/10s)
      │ 3s 无包 / 离开赛道
      └→ 清空 standings，退避重连
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from pyACCSharedMemory.acc_udp import set_register_message

from acc_telemetry.sources.broadcast import BroadcastAdapter
from acc_telemetry.sources.broadcasting_config import (
    load_broadcasting_config,
    resolve_port,
)

from .. import bus

logger = logging.getLogger(__name__)

# ---- 连接参数 -------------------------------------------------------------
BROADCAST_HOST = "127.0.0.1"
CLIENT_NAME = "acc-telemetry"
# 只读连接的「口令」：ACC 只在 commandPassword 不匹配时降级为只读，
# 所以这里必须故意填一个不可能被用户配成一样的值（探针同样用它，已真机验证可收数据）。
READ_ONLY_COMMAND_PASSWORD = "__acc_telemetry_read_only__"

# ---- 节拍 / 超时 ----------------------------------------------------------
SNAPSHOT_INTERVAL_S = 0.25  # 组装快照（≤4Hz，广播本身的刷新率就在这量级）
REGISTER_TIMEOUT_S = 2.0
PACKET_TIMEOUT_S = 3.0  # 超过这么久没包 = 断线
IDLE_POLL_S = 1.0  # 不可用状态下的复查间隔
SESSION_CHECK_S = 1.0  # 复查「还在赛道上吗」的间隔
ENTRY_LIST_RETRY_S = 5.0  # 名册没齐时的补拉间隔
ENTRY_LIST_REFRESH_S = 30.0  # 名册齐了也要定期刷（覆盖迟到加入者）
TRACK_DATA_RETRY_S = 10.0  # 没拿到赛道长度时的重试间隔
BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0)

# ACC graphics.status（与 backend/collector/reader.py 同一套枚举）
ACC_LIVE, ACC_PAUSE = 2, 3


@dataclass(frozen=True, slots=True)
class BroadcastSettings:
    """广播开关（来自 ``config.json``，本轮没有设置页控件）。"""

    enabled: bool = True
    # 0 = 自动读 broadcasting.json；文件里也是 0 视为「未配置」
    port: int = 0
    interval_ms: int = 250

    @classmethod
    def from_config(cls, values: dict[str, Any] | None) -> "BroadcastSettings":
        values = values or {}
        return cls(
            enabled=bool(values.get("broadcastEnabled", True)),
            port=_as_int(values.get("broadcastPort")),
            interval_ms=max(10, _as_int(values.get("broadcastIntervalMs"), 250) or 250),
        )


@dataclass(frozen=True, slots=True)
class _Outcome:
    """一次连接的生命周期结束原因。"""

    reason: str
    # 是否曾经注册成功：成功过的断线才需要「重新开始」退避计数
    connected: bool = False


# --------------------------------------------------------------------- 状态
def set_standings_status(
    reason: str | None, *, port: int | None = None, connected: bool = False
) -> None:
    """更新 ``bus.standings_status``（给 state 组装 reason 用）。"""
    bus.standings_status = {"reason": reason, "port": port, "connected": connected}


def in_session() -> bool:
    """游戏在跑且（大概）在赛道上。

    ``acc_status`` 由采集循环每帧写入；暂停(3) 不算离开赛道 —— 用户随时会继续，
    暂停期间广播会自然进入「无包超时」，不需要在这里额外拆连接。
    ``None``（采集循环还没读到）时按「可能在赛道」处理，靠注册超时兜底。
    """
    if not bus.runtime.get("game_running"):
        return False
    status = bus.runtime.get("acc_status")
    if status is None:
        return True
    try:
        return int(status) in (ACC_LIVE, ACC_PAUSE)
    except (TypeError, ValueError):
        return True


# --------------------------------------------------------------------- UDP
class _DatagramFeed(asyncio.DatagramProtocol):
    """收到的数据报进缓冲区，并用一个 Event 唤醒主循环。

    这里刻意**不用 ``asyncio.Queue``**：``queue.get()`` 一旦被取消（超时那一拍），
    已经出队的那条报文就永久丢了 —— 4Hz 的车辆更新遇上 4Hz 的等待节奏，实测会
    丢掉绝大多数包，榜单全是 stale。缓冲区 + Event 没有这个交接问题：
    Event 只负责叫醒，数据永远留在缓冲区里等 drain。
    """

    def __init__(self, maxsize: int = 1024) -> None:
        self._buffer: deque[bytes] = deque(maxlen=maxsize)
        self.wake = asyncio.Event()
        self.error: str | None = None
        self.closed = False
        self.dropped = 0

    def datagram_received(self, data: bytes, addr: Any) -> None:  # noqa: ARG002
        if len(self._buffer) == self._buffer.maxlen:
            # 积压说明主循环比游戏慢得多，丢最旧的，绝不能阻塞
            self.dropped += 1
        self._buffer.append(bytes(data))
        self.wake.set()

    def pending(self) -> bool:
        return bool(self._buffer)

    def drain(self) -> list[bytes]:
        items = list(self._buffer)
        self._buffer.clear()
        self.wake.clear()
        return items

    def error_received(self, exc: Exception) -> None:
        # 端口没人监听时 Windows 会回 ICMP unreachable → 这里拿到 ConnectionResetError
        self.error = str(exc) or exc.__class__.__name__

    def connection_lost(self, exc: Exception | None) -> None:
        self.closed = True
        if exc is not None:
            self.error = str(exc)


async def _wait_data(
    feed: _DatagramFeed, timeout: float, stop_event: asyncio.Event
) -> bool:
    """等新报文。返回 ``True`` 表示有数据可 drain，``False`` 表示超时 / 要停了。

    按 0.25s 切片等，是为了让 ``stop_event`` 最多 0.25s 就能生效（注册阶段要等
    2s，不能一直阻塞退出）。Event 不会被「消费」，取消等待也不会丢数据。
    """
    if feed.pending():
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while not stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return feed.pending()
        try:
            await asyncio.wait_for(feed.wake.wait(), min(remaining, 0.25))
            return True
        except asyncio.TimeoutError:
            if feed.pending():
                return True
    return False


async def _await_registration(
    feed: _DatagramFeed, adapter: BroadcastAdapter, stop_event: asyncio.Event
) -> bool:
    """等到注册结果（或超时 / 被停止）。"""
    deadline = time.monotonic() + REGISTER_TIMEOUT_S
    while not stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not await _wait_data(feed, remaining, stop_event):
            continue
        for payload in feed.drain():
            adapter.feed(payload)
            # 用「收到过注册结果」而不是「成功 / 有错误文本」：注册失败时
            # errorMessage 可能是空串，否则会一直等到超时、误报成 timeout。
            if adapter.registration_seen:
                return True
    return False


def _send(transport: Any, payload: bytes | None) -> None:
    if payload is None:
        return
    try:
        transport.sendto(bytes(payload))
    except Exception:  # noqa: BLE001 - 发送失败会由「无包超时」这条路径兜住
        logger.debug("广播请求发送失败", exc_info=True)


# --------------------------------------------------------------------- 主流程
async def broadcast_standings_loop(
    stop_event: asyncio.Event,
    *,
    settings: Callable[[], BroadcastSettings] | None = None,
    host: str = BROADCAST_HOST,
) -> None:
    """广播服务主循环。**永不退出**（除非 ``stop_event`` 被 set）。"""
    attempt = 0
    try:
        while not stop_event.is_set():
            current = _read_settings(settings)

            if not current.enabled:
                bus.latest_standings = None
                set_standings_status("disabled")
                attempt = 0
                if await _sleep(stop_event, IDLE_POLL_S):
                    break
                continue

            if not in_session():
                bus.latest_standings = None
                set_standings_status("no-session")
                attempt = 0
                if await _sleep(stop_event, IDLE_POLL_S):
                    break
                continue

            # 读文件是同步 IO：丢线程，别卡住 100Hz 采集
            config = await asyncio.to_thread(load_broadcasting_config)
            port = resolve_port(current.port, config)
            if port is None:
                bus.latest_standings = None
                set_standings_status("port-not-configured")
                attempt = 0
                if await _sleep(stop_event, IDLE_POLL_S):
                    break
                continue

            set_standings_status("connecting", port=port)
            try:
                outcome = await _stream(
                    stop_event,
                    host=host,
                    port=port,
                    interval_ms=current.interval_ms,
                    connection_password=str(config.get("connection_password") or ""),
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 广播绝不能让 lifespan 炸掉
                logger.exception("广播连接异常，按断线处理")
                outcome = _Outcome("error", False)

            # 断开就清空：旧榜单继续挂在 state 上比没有更糟
            bus.latest_standings = None
            set_standings_status(outcome.reason, port=port)
            if stop_event.is_set():
                break

            if outcome.connected:
                attempt = 0
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            attempt += 1
            logger.info("广播断开（%s），%.0fs 后重连", outcome.reason, delay)
            if await _sleep(stop_event, delay):
                break
    finally:
        bus.latest_standings = None
        if bus.standings_status.get("connected"):
            set_standings_status("no-data")


async def _stream(
    stop_event: asyncio.Event,
    *,
    host: str,
    port: int,
    interval_ms: int,
    connection_password: str,
) -> _Outcome:
    """一次完整的「注册 → 收包」生命周期。返回结束原因，不抛异常。"""
    loop = asyncio.get_running_loop()
    feed = _DatagramFeed()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: feed, remote_addr=(host, port)
        )
    except OSError as exc:
        logger.warning("广播 socket 打开失败 %s:%s —— %s", host, port, exc)
        return _Outcome("timeout", False)

    adapter = BroadcastAdapter()
    try:
        message = set_register_message(
            display_name=CLIENT_NAME,
            connection_password=connection_password,
            command_password=READ_ONLY_COMMAND_PASSWORD,
            realtime_update_interval=max(10, int(interval_ms)),
        )
        _send(transport, bytes(message))

        if not await _await_registration(feed, adapter, stop_event):
            if stop_event.is_set():
                return _Outcome("no-data")
            logger.info("广播注册超时（%.0fs 内没有 REGISTRATION_RESULT）", REGISTER_TIMEOUT_S)
            return _Outcome("timeout", False)
        if not adapter.registration_succeeded:
            logger.warning(
                "广播注册被拒：%s", adapter.registration_error or "游戏没有给出原因"
            )
            return _Outcome("error", False)

        # 注册结果后面往往紧跟着几个包（名册头 / 赛道数据），别扔
        for payload in feed.drain():
            adapter.feed(payload)

        logger.info(
            "广播已连接（connectionId=%s, 只读=%s, %s:%s）",
            adapter.connection_id,
            adapter.is_read_only,
            host,
            port,
        )
        return await _receive(transport, feed, adapter, stop_event)
    finally:
        transport.close()


async def _receive(
    transport: Any,
    feed: _DatagramFeed,
    adapter: BroadcastAdapter,
    stop_event: asyncio.Event,
) -> _Outcome:
    """注册成功后的收包主循环：喂包、按 4Hz 组装、补拉名册、判掉线。"""
    # 先要名册和赛道信息，之后按周期补
    _send(transport, adapter.entry_list_request())
    _send(transport, adapter.track_data_request())
    adapter.entry_list_synced()

    now = time.monotonic()
    next_snapshot = now
    next_entry = now + ENTRY_LIST_RETRY_S
    next_track = now + TRACK_DATA_RETRY_S
    next_session_check = now + SESSION_CHECK_S
    last_packet = now
    session_key = _session_key(adapter, "")
    # 赛道名要「粘住」：adapter.reset() 会把 trackData 清空，如果拿清空后的空串
    # 去和上一轮的 "monza" 比，就会判定成「又换赛道了」→ 再 reset → 无限循环。
    track_marker = adapter.track_name

    while not stop_event.is_set():
        now = time.monotonic()

        # 名册：适配器发现过期就立刻补，否则「没齐 5s / 齐了 30s」两档
        if now >= next_entry or adapter.needs_entry_list_sync:
            _send(transport, adapter.entry_list_request())
            adapter.entry_list_synced()
            next_entry = now + (
                ENTRY_LIST_REFRESH_S if adapter.entry_list_complete else ENTRY_LIST_RETRY_S
            )
        if now >= next_track:
            if adapter.track_meters is None:
                _send(transport, adapter.track_data_request())
            next_track = now + TRACK_DATA_RETRY_S

        got_data = await _wait_data(feed, max(0.0, next_snapshot - now), stop_event)
        now = time.monotonic()
        payloads = feed.drain() if got_data else []
        if payloads:
            # 收到数据就说明 socket 是通的：清掉之前那条 ICMP unreachable 的残留错误
            feed.error = None
        for payload in payloads or ():
            adapter.feed(payload, now=now)
            last_packet = now
            track_marker = adapter.track_name or track_marker
            changed, session_key, track_marker = _session_transition(
                session_key, adapter, track_marker
            )
            # 会话 / 赛道变了必须 reset：entryListCars 是类级共享字典，
            # 不清会把上一场的车带到这一场来（ADR-0001 §5）。
            if changed:
                logger.info(
                    "广播会话/赛道变化（sessionIndex=%s），重置榜单",
                    session_key[0] if session_key else "?",
                )
                adapter.reset()
                adapter.entry_list_synced()
                bus.latest_standings = None
                _send(transport, adapter.entry_list_request())
                _send(transport, adapter.track_data_request())
                next_entry = now + ENTRY_LIST_RETRY_S
                next_track = now + TRACK_DATA_RETRY_S

        if now - last_packet > PACKET_TIMEOUT_S:
            logger.info("广播已 %.1fs 没有新包，判定断线", now - last_packet)
            return _Outcome("timeout", True)

        if now >= next_session_check:
            next_session_check = now + SESSION_CHECK_S
            if not in_session():
                logger.info("已离开赛道，关闭广播连接")
                return _Outcome("no-session", True)
            if feed.error:
                logger.info("广播 socket 报错（%s），按断线处理", feed.error)
                return _Outcome("timeout", True)

        if now >= next_snapshot:
            # 组装是纯内存操作，和收包在同一个 Task 里做即可；不跨线程、不加锁
            bus.latest_standings = adapter.snapshot(now=now)
            set_standings_status(None, port=None, connected=True)
            next_snapshot = now + SNAPSHOT_INTERVAL_S

    return _Outcome("no-data", True)


def _session_key(adapter: BroadcastAdapter, track_marker: str) -> tuple[int, str, str] | None:
    """会话身份：变了就该清空榜单重来；``None`` 表示「还不知道，别判」。

    会话信息只由 REALTIME_UPDATE 刷新，``reset()`` 之后会回到 -1。这时如果拿
    ``-1`` 去比较，就会被判成「又换会话了」→ 再 reset → 死循环，所以负数一律返回
    ``None``。赛道名由调用方粘住（reset 会清空 trackData），避免同样的抖动。
    """
    try:
        index = int(getattr(adapter, "session_index", -1))
    except (TypeError, ValueError):
        return None
    if index < 0:
        return None
    return (
        index,
        str(getattr(adapter, "session_type", "unknown")),
        track_marker,
    )


def _session_transition(
    previous: tuple[int, str, str] | None,
    adapter: BroadcastAdapter,
    track_marker: str,
) -> tuple[bool, tuple[int, str, str] | None, str]:
    """返回 ``(是否需要 reset, 新 session_key, 新 track_marker)``。

    启动时 ``REALTIME_UPDATE`` 通常先于 ``TRACK_DATA`` 到达：如果拿「赛道名从空
    变成 Monza」去比较，会把同一场误判成换赛道，清掉刚收到的名册。所以：

    - 第一次建立 key（``previous is None``）不 reset；
    - 只有赛道名从空变非空、且 sessionIndex / sessionType 没变 → 只补赛道名；
    - 真正的会话/赛道变化 → reset，并把赛道名清空，等新赛道的 TrackData 重新建立，
      避免同一场换赛道触发两次 reset（第一次带旧赛道名，第二次带新赛道名）。
    """
    new_key = _session_key(adapter, track_marker)
    if new_key is None:
        return False, previous, track_marker
    if previous is None or new_key == previous:
        return False, new_key, track_marker
    if previous[2] == "" and new_key[2] != "" and previous[:2] == new_key[:2]:
        # 同一场里 TrackData 晚到：只是补上赛道名，不是新会话
        return False, new_key, track_marker
    return True, (new_key[0], new_key[1], ""), ""


def _read_settings(
    settings: Callable[[], BroadcastSettings] | None,
) -> BroadcastSettings:
    if settings is None:
        return BroadcastSettings()
    try:
        return settings()
    except Exception:  # noqa: BLE001 - 配置读错也要按默认值继续跑
        logger.exception("读取广播配置失败，本次用默认值")
        return BroadcastSettings()


async def _sleep(stop_event: asyncio.Event, seconds: float) -> bool:
    """等一段时间；被要求停止时返回 ``True``。"""
    try:
        await asyncio.wait_for(stop_event.wait(), max(0.0, seconds))
        return True
    except asyncio.TimeoutError:
        return False


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
