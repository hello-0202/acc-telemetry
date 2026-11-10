"""广播服务的纯逻辑（``backend/collector/broadcast_reader.py``）。

**不引入真实 socket、不依赖游戏**：只测那些「想错了会静默坏事」的分支 ——

- 配置解析（``BroadcastSettings``）的默认值与越界值；
- ``in_session()``：暂停不算离开赛道，状态未知按「可能在」处理；
- ``_session_key()``：``reset()`` 之后返回 ``None``，否则会 reset 死循环；
- ``_DatagramFeed``：缓冲区语义（**不能用 Queue**，超时取消会丢报文）；
- 主循环的三个「不可用」分支各自给出正确 reason 并清空槽位。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import bus  # noqa: E402
from backend.collector import broadcast_reader as br  # noqa: E402


def _prepare_runtime(**overrides) -> None:
    bus.reset()
    bus.runtime.update({"recording": True, "game_running": True, "acc_status": 2, **overrides})


# ------------------------------------------------------------------ 配置
def test_settings_defaults() -> None:
    settings = br.BroadcastSettings.from_config(None)

    assert settings.enabled is True
    assert settings.port == 0  # 0 = 自动读 broadcasting.json
    assert settings.interval_ms == 250


def test_settings_rejects_silly_interval() -> None:
    assert br.BroadcastSettings.from_config({"broadcastIntervalMs": 0}).interval_ms == 250
    assert br.BroadcastSettings.from_config({"broadcastIntervalMs": 1}).interval_ms == 10


def test_settings_parses_strings_from_json() -> None:
    settings = br.BroadcastSettings.from_config(
        {"broadcastEnabled": False, "broadcastPort": "9100", "broadcastIntervalMs": "100"}
    )

    assert settings.enabled is False
    assert settings.port == 9100
    assert settings.interval_ms == 100


def test_settings_falls_back_when_getter_raises() -> None:
    def boom() -> br.BroadcastSettings:
        raise RuntimeError("配置坏了")

    assert br._read_settings(boom) == br.BroadcastSettings()


# ------------------------------------------------------------------ 在赛道上？
@pytest.mark.parametrize(
    "runtime, expected",
    [
        ({"game_running": False, "acc_status": 2}, False),
        ({"game_running": True, "acc_status": 2}, True),   # LIVE
        ({"game_running": True, "acc_status": 3}, True),   # PAUSE 仍算在赛道上
        ({"game_running": True, "acc_status": 0}, False),  # OFF
        ({"game_running": True, "acc_status": 1}, False),  # REPLAY
        ({"game_running": True, "acc_status": None}, True),  # 还不知道 → 靠注册超时兜底
        ({"game_running": True, "acc_status": "坏值"}, True),
    ],
)
def test_in_session(runtime: dict, expected: bool) -> None:
    bus.reset()
    bus.runtime.update(runtime)

    assert br.in_session() is expected


# ------------------------------------------------------------------ 会话身份
def test_session_key_is_none_before_first_realtime_update() -> None:
    # reset() 后 session_index 回到 -1；若返回 -1，每轮都会判「换会话了」→ 死循环
    adapter = _FakeAdapter(session_index=-1, session_type="unknown")

    assert br._session_key(adapter, "") is None


def test_session_key_changes_with_type_and_track() -> None:
    assert br._session_key(_FakeAdapter(session_index=1, session_type="race"), "monza") == (
        1, "race", "monza",
    )
    assert br._session_key(_FakeAdapter(session_index=1, session_type="practice"), "monza") != (
        1, "race", "monza",
    )
    assert br._session_key(_FakeAdapter(session_index=1, session_type="race"), "spa") != (
        1, "race", "monza",
    )


def test_session_key_tolerates_garbage_index() -> None:
    assert br._session_key(_FakeAdapter(session_index="x"), "") is None


def test_session_transition_ignores_late_track_name() -> None:
    # 启动顺序：REALTIME_UPDATE 先到（赛道名为空），TRACK_DATA 后到。
    # 只补赛道名，不能 reset，否则会清掉刚收到的名册。
    adapter = _FakeAdapter(session_index=1, session_type="race")

    changed, key, marker = br._session_transition(None, adapter, "")
    assert (changed, key, marker) == (False, (1, "race", ""), "")

    changed, key, marker = br._session_transition(key, adapter, "Monza")
    assert (changed, key, marker) == (False, (1, "race", "Monza"), "Monza")


def test_session_transition_detects_real_session_change_and_clears_track() -> None:
    adapter = _FakeAdapter(session_index=2, session_type="qualify")

    changed, key, marker = br._session_transition((1, "race", "Monza"), adapter, "Monza")

    assert changed is True
    assert key == (2, "qualify", "")  # 清掉旧赛道名，避免新 TrackData 再触发一次
    assert marker == ""


def test_session_transition_detects_track_change_with_same_session() -> None:
    adapter = _FakeAdapter(session_index=1, session_type="race")

    changed, key, marker = br._session_transition((1, "race", "Monza"), adapter, "Spa")

    assert changed is True
    assert key == (1, "race", "")
    assert marker == ""


def test_session_transition_waits_before_first_realtime_update() -> None:
    adapter = _FakeAdapter(session_index=-1)

    assert br._session_transition((1, "race", "Monza"), adapter, "Monza") == (
        False,
        (1, "race", "Monza"),
        "Monza",
    )


# ------------------------------------------------------------------ 报文缓冲
def test_feed_drains_everything_and_wakes_once() -> None:
    async def scenario() -> None:
        feed = br._DatagramFeed()
        assert await br._wait_data(feed, 0.05, asyncio.Event()) is False  # 空缓冲等不到

        feed.datagram_received(b"a", None)
        feed.datagram_received(b"b", None)
        assert await br._wait_data(feed, 0.05, asyncio.Event()) is True

        assert feed.drain() == [b"a", b"b"]
        assert feed.drain() == []          # 取完即空
        assert feed.pending() is False

    asyncio.run(scenario())


def test_feed_drops_oldest_instead_of_blocking() -> None:
    feed = br._DatagramFeed(maxsize=3)
    for i in range(5):
        feed.datagram_received(bytes([i]), None)

    assert feed.dropped == 2
    assert feed.drain() == [b"\x02", b"\x03", b"\x04"]


def test_wait_data_is_interruptible_by_stop_event() -> None:
    async def scenario() -> None:
        feed = br._DatagramFeed()
        stop = asyncio.Event()
        task = asyncio.create_task(br._wait_data(feed, 10.0, stop))
        await asyncio.sleep(0)  # 让任务先跑起来
        stop.set()
        assert await asyncio.wait_for(task, 1.0) is False

    asyncio.run(scenario())


# ------------------------------------------------------------------ 主循环分支
def _run_loop_once(settings: br.BroadcastSettings) -> None:
    """把主循环跑一小会儿再停：足够走完「不可用」分支的第一拍。"""

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            br.broadcast_standings_loop(stop, settings=lambda: settings)
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
            if bus.standings_status.get("reason"):
                break
        stop.set()
        await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def test_disabled_settings_report_disabled_and_clear_slot() -> None:
    _prepare_runtime()
    bus.latest_standings = object()  # 上一轮的残留必须清掉

    _run_loop_once(br.BroadcastSettings(enabled=False))

    assert bus.standings_status["reason"] == "disabled"
    assert bus.latest_standings is None


def test_no_session_reports_no_session() -> None:
    _prepare_runtime(game_running=False)
    bus.latest_standings = object()

    _run_loop_once(br.BroadcastSettings())

    assert bus.standings_status["reason"] == "no-session"
    assert bus.latest_standings is None


def test_port_zero_in_config_file_reports_port_not_configured(monkeypatch) -> None:
    _prepare_runtime()
    # 真机默认就是 port=0（广播关闭），这里伪造读取结果，不碰用户目录
    monkeypatch.setattr(
        br,
        "load_broadcasting_config",
        lambda: {"exists": True, "port": 0, "error": None,
                 "connection_password": "", "command_password": ""},
    )

    _run_loop_once(br.BroadcastSettings())

    assert bus.standings_status["reason"] == "port-not-configured"


def test_loop_clears_slot_on_exit() -> None:
    _prepare_runtime()
    bus.latest_standings = object()
    bus.standings_status = {"reason": None, "port": 9000, "connected": True}

    _run_loop_once(br.BroadcastSettings(enabled=False))

    assert bus.latest_standings is None


# ------------------------------------------------------------------ 替身
class _FakeAdapter:
    """只需要 ``_session_key`` 读的那几个属性。"""

    def __init__(self, *, session_index=1, session_type: str = "race") -> None:
        self.session_index = session_index
        self.session_type = session_type
