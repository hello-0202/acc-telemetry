"""广播适配器：vendored 结构体 → 领域模型。

全部离线：报文由 ``broadcast_fixtures.py`` 手工合成，不需要游戏 / 网络 / 夹具文件。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import broadcast_fixtures as F  # noqa: E402
from acc_telemetry.domain.model import CAR_LOCATIONS, SESSION_PHASES, SESSION_TYPES  # noqa: E402
from acc_telemetry.sources.broadcast import (  # noqa: E402
    BroadcastAdapter,
    car_location_name,
    decode_text,
)

PKG_ROOT = Path(__file__).resolve().parents[1] / "acc_telemetry"


@pytest.fixture()
def adapter() -> BroadcastAdapter:
    # 每个用例一个干净适配器：构造时就会清掉类级共享的 entryListCars
    return BroadcastAdapter()


def single_car_session(
    adapter: BroadcastAdapter,
    *,
    car_count: int = 1,
    cars: tuple[int, ...] = (1,),
    now: float = 0.0,
) -> None:
    adapter.feed(F.registration_result(1), now=now)
    adapter.feed(F.entry_list(1, car_count), now=now)
    for car_id in cars:
        adapter.feed(F.entry_list_car(car_id, race_number=car_id), now=now)


# ---------------------------------------------------------------- EntryList 累积
def test_entry_list_accumulates_all_cars(adapter: BroadcastAdapter) -> None:
    single_car_session(adapter, car_count=3, cars=(1, 2, 3), now=1.0)

    snapshot = adapter.snapshot(now=1.0)

    assert [row.car_id for row in snapshot.rows] == [1, 2, 3]
    assert adapter.cars_expected == 3
    assert adapter.cars_known == 3
    assert snapshot.entry_list_complete is True
    assert snapshot.cars_expected == 3


def test_entry_list_incomplete_until_every_car_arrives(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.registration_result(1), now=0.0)
    adapter.feed(F.entry_list(1, 2), now=0.0)
    adapter.feed(F.entry_list_car(1), now=0.0)

    snapshot = adapter.snapshot(now=0.0)

    assert adapter.cars_known == 1
    assert snapshot.entry_list_complete is False


def test_entry_only_car_takes_car_id_from_dataset_key(adapter: BroadcastAdapter) -> None:
    """名册里的车 carId 字段还是 0，行主键只能取数据集 key（= entryId）。"""
    adapter.feed(F.entry_list_car(9, race_number=9), now=0.0)

    rows = adapter.snapshot(now=0.0).rows

    assert len(rows) == 1
    assert rows[0].car_id == 9
    assert rows[0].race_number == 9
    assert rows[0].driver_name == "Test Driver"


def test_reset_clears_previous_session(adapter: BroadcastAdapter) -> None:
    single_car_session(adapter, car_count=2, cars=(1, 2))
    assert len(adapter.snapshot(now=0.0).rows) == 2

    adapter.reset()

    assert adapter.snapshot(now=0.0).rows == ()
    assert adapter.cars_expected == 0
    assert adapter.cars_known == 0


def test_new_adapter_inherits_nothing_from_shared_dataset(adapter: BroadcastAdapter) -> None:
    """类级共享 defaultdict 的坑：新实例必须自带清理，不能继承上一份数据。

    代价是「同进程只允许一个适配器」—— 这里把这个事实也断言下来，避免有人靠多实例绕开。
    """
    single_car_session(adapter, car_count=1, cars=(1,))
    assert adapter.snapshot(now=0.0).rows

    fresh = BroadcastAdapter()

    assert fresh.snapshot(now=0.0).rows == ()
    assert adapter.snapshot(now=0.0).rows == ()  # 原实例也被清空，符合预期


# ---------------------------------------------------------------- 缺失字段兜底
def test_realtime_only_car_has_empty_identity(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(4, position=3, speed_kmh=88), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(4)

    assert row is not None
    assert row.driver_name == ""
    assert row.team_name is None
    assert row.race_number is None
    assert row.position == 3


def test_unknown_handshake_does_not_raise(adapter: BroadcastAdapter) -> None:
    assert adapter.feed(b"") == -1
    assert adapter.feed(b"\x7f\x01\x02") == 127
    assert adapter.snapshot(now=0.0).rows == ()
    assert adapter.connection_id <= 0
    assert adapter.registration_succeeded is False


# ---------------------------------------------------------------- 名字解码
def test_driver_name_prefers_first_and_last(adapter: BroadcastAdapter) -> None:
    adapter.feed(
        F.entry_list_car(1, drivers=(("Giorgio", "Prova", "G.PROVA"),)), now=0.0
    )

    assert adapter.snapshot(now=0.0).row_for(1).driver_name == "Giorgio Prova"


def test_driver_name_falls_back_to_short_name(adapter: BroadcastAdapter) -> None:
    # 首末名为 NUL → 解码成空，退到 shortName（解析器只把「长度 0」当结束，b"\x00" 能过）
    adapter.feed(
        F.entry_list_car_raw_names(1, drivers=((b"\x00", b"\x00", b"T.DRIVER"),)), now=0.0
    )

    assert adapter.snapshot(now=0.0).row_for(1).driver_name == "T.DRIVER"


def test_driver_name_with_non_utf8_bytes_degrades_to_latin1(adapter: BroadcastAdapter) -> None:
    # 0xE9 是 Latin-1 的 é，单独出现不是合法 UTF-8
    adapter.feed(
        F.entry_list_car_raw_names(1, drivers=((b"Andr\xe9", b"Pr\xe9vot", b"A.PREVOT"),)),
        now=0.0,
    )

    assert "Andr" in adapter.snapshot(now=0.0).row_for(1).driver_name


def test_decode_text_never_raises_on_junk() -> None:
    assert decode_text(b"") == ""
    assert decode_text(b"abc\x00rest") == "abc"
    assert decode_text(b"\xff\xfe") in ("\xff\xfe", "ÿþ")
    assert decode_text(None) == ""
    assert decode_text(12345) == ""


def test_driver_index_picks_current_driver(adapter: BroadcastAdapter) -> None:
    # 先来实时更新把 driverIndex 设成 1，再来名册：解析器会跳过第 0 位车手
    adapter.feed(F.car_update(3, driver_index=1, driver_count=2), now=0.0)
    adapter.feed(
        F.entry_list_car(
            3,
            current_driver_index=1,
            drivers=(("First", "Driver", "F.DRIVER"), ("Second", "Driver", "S.DRIVER")),
        ),
        now=0.0,
    )

    assert adapter.snapshot(now=0.0).row_for(3).driver_name == "Second Driver"


# ---------------------------------------------------------------- 圈速哨兵
def test_lap_sentinels_become_zero(adapter: BroadcastAdapter) -> None:
    adapter.feed(
        F.car_update(
            1,
            last_lap_ms=99_999_999,
            best_lap_ms=2_147_483_647,
            current_lap_ms=-1,
        ),
        now=0.0,
    )

    row = adapter.snapshot(now=0.0).row_for(1)

    assert (row.last_lap_ms, row.best_lap_ms, row.current_lap_ms) == (0, 0, 0)


def test_valid_lap_times_pass_through(adapter: BroadcastAdapter) -> None:
    adapter.feed(
        F.car_update(1, last_lap_ms=101_234, best_lap_ms=99_876, current_lap_ms=12_000),
        now=0.0,
    )

    row = adapter.snapshot(now=0.0).row_for(1)

    assert (row.last_lap_ms, row.best_lap_ms, row.current_lap_ms) == (101_234, 99_876, 12_000)


def test_delta_best_zero_and_sentinel_are_unknown(adapter: BroadcastAdapter) -> None:
    # 0 = 还没有有效参考圈（出场圈 / 尚无有效圈 / 换会话后），不是「正好等于最佳」。
    adapter.feed(F.car_update(1, delta_best=0), now=0.0)
    adapter.feed(F.car_update(2, delta_best=2_147_483_647), now=0.0)
    adapter.feed(F.car_update(3, delta_best=1_234), now=0.0)

    snapshot = adapter.snapshot(now=0.0)

    assert snapshot.row_for(1).delta_best_ms is None
    assert snapshot.row_for(2).delta_best_ms is None
    assert snapshot.row_for(3).delta_best_ms == 1_234


def test_delta_best_none_before_any_realtime_update(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.entry_list_car(1), now=0.0)

    assert adapter.snapshot(now=0.0).row_for(1).delta_best_ms is None


# ---------------------------------------------------------------- 位置类字段可空
def test_positional_fields_are_none_without_realtime_update(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.entry_list_car(1), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(1)

    assert row.spline_position is None
    assert row.world_x is None and row.world_y is None
    assert row.stale is True


def test_spline_zero_is_kept_when_live(adapter: BroadcastAdapter) -> None:
    """0 是合法的起跑线位置，不能当空值。"""
    adapter.feed(F.car_update(1, spline_position=0.0, world_x=1.0, world_y=2.0), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(1)

    assert row.spline_position == 0.0
    assert row.stale is False


def test_world_coordinates_zero_zero_means_unknown(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(1, world_x=0.0, world_y=0.0), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(1)

    assert row.world_x is None and row.world_y is None


def test_world_coordinates_pass_through(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(1, world_x=-123.5, world_y=456.25), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(1)

    assert (row.world_x, row.world_y) == (-123.5, 456.25)


def test_stale_row_keeps_position_but_drops_moving_fields(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(1, position=2, spline_position=0.4, world_x=5.0, world_y=6.0), now=0.0)

    row = adapter.snapshot(now=30.0).row_for(1)

    assert row.stale is True
    assert row.position == 2  # 名次是历史结论，不该随刷新率消失
    assert row.spline_position is None
    assert row.world_x is None


# ---------------------------------------------------------------- 车辆位置分类
def test_car_location_mapping() -> None:
    assert car_location_name(1) == "track"
    assert car_location_name(2) == "pitlane"
    assert car_location_name(3) == "pit_entry"
    assert car_location_name(4) == "pit_exit"
    assert car_location_name(0) == "unknown"
    assert car_location_name(99) == "unknown"
    assert car_location_name(None) == "unknown"
    assert car_location_name(0, in_garage=True) == "garage"


def test_pit_teleport_heuristic_does_not_override_pitlane(adapter: BroadcastAdapter) -> None:
    """解析器的 inGarage 启发式会在一辆车的首帧误判；只信 carLocation。"""
    # 首帧就在维修区、且离原点很远 → 解析器内部 inGarage 被置 True
    adapter.feed(F.car_update(5, car_location=2, world_x=500.0, world_y=500.0), now=0.0)

    row = adapter.snapshot(now=0.0).row_for(5)

    assert row.car_location == "pitlane"
    assert row.in_pit is True


def test_garage_requires_unknown_location(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(5, car_location=2, world_x=500.0, world_y=500.0), now=0.0)
    adapter.feed(F.car_update(5, car_location=0, world_x=500.0, world_y=500.0), now=0.1)

    assert adapter.snapshot(now=0.1).row_for(5).car_location == "garage"


# ---------------------------------------------------------------- 排序 / 会话
def test_rows_sorted_by_position_then_track_position(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.car_update(1, position=3, track_position=3), now=0.0)
    adapter.feed(F.car_update(2, position=1, track_position=1), now=0.0)
    adapter.feed(F.car_update(3, position=0, track_position=2), now=0.0)
    adapter.feed(F.car_update(4, position=0, track_position=0), now=0.0)

    car_ids = [row.car_id for row in adapter.snapshot(now=0.0).rows]

    assert car_ids == [2, 1, 3, 4]


def test_session_type_and_phase_are_semantic_strings(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.realtime_update(session_type=10, session_phase=5), now=0.0)

    snapshot = adapter.snapshot(now=0.0)

    assert snapshot.session_type == "race"
    assert snapshot.session_phase == "session"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "practice"),
        (4, "qualify"),
        (9, "superpole"),
        (10, "race"),
        (11, "hotlap"),
        (12, "hotstint"),
        (13, "hotstintsuperpole"),
        (14, "unknown"),  # Replay 不是实时会话
        (1, "unknown"),   # 共享内存的 qualify 值，在广播里无意义
        (2, "unknown"),   # 共享内存的 race 值，在广播里无意义
    ],
)
def test_broadcast_session_type_uses_its_own_enum(
    adapter: BroadcastAdapter, raw: int, expected: str
) -> None:
    adapter.feed(F.realtime_update(session_type=raw, session_phase=5), now=0.0)

    assert adapter.snapshot(now=0.0).session_type == expected


def test_unknown_session_enum_degrades(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.realtime_update(session_type=99, session_phase=99), now=0.0)

    snapshot = adapter.snapshot(now=0.0)

    assert snapshot.session_type == "unknown"
    assert snapshot.session_phase == "unknown"


def test_track_meters_decoded_from_int32_wire(adapter: BroadcastAdapter) -> None:
    assert adapter.snapshot(now=0.0).track_meters is None

    adapter.feed(F.track_data(meters=0), now=0.0)
    assert adapter.snapshot(now=0.0).track_meters is None

    adapter.feed(F.track_data(meters=5793), now=0.0)
    assert adapter.snapshot(now=0.0).track_meters == 5793.0
    assert adapter.track_meters == 5793.0
    assert adapter.track_name == "test_track"


def test_track_meters_rejects_out_of_range_value(adapter: BroadcastAdapter) -> None:
    # 解码后若不是合理赛道长度，宁可未知也不给脏值
    adapter.feed(F.track_data(meters=1_000_000), now=0.0)

    assert adapter.snapshot(now=0.0).track_meters is None


def test_reset_clears_track_and_session_info(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.realtime_update(session_type=10, session_phase=5), now=0.0)
    adapter.feed(F.track_data(meters=5793), now=0.0)

    adapter.reset()

    snapshot = adapter.snapshot(now=0.0)
    assert snapshot.track_meters is None
    assert snapshot.session_type == "unknown"
    assert snapshot.session_phase == "unknown"


def test_snapshot_ignores_non_positive_car_ids(adapter: BroadcastAdapter) -> None:
    # BROADCASTING_EVENT 往共享 defaultdict 里塞 0 / 超大 key 时，不应进入榜单
    adapter.feed(F.entry_list(1, 1), now=0.0)
    adapter.feed(F.entry_list_car(1, race_number=1), now=0.0)
    adapter.feed(F.car_event(car_id=0), now=0.0)

    assert [row.car_id for row in adapter.snapshot(now=0.0).rows] == [1]


# ---------------------------------------------------------------- 上行请求 / 重同步
def test_requests_need_a_connection_id(adapter: BroadcastAdapter) -> None:
    assert adapter.entry_list_request() is None
    assert adapter.track_data_request() is None

    adapter.feed(F.registration_result(7), now=0.0)

    assert adapter.entry_list_request() == bytes([10]) + (7).to_bytes(4, "little")
    assert adapter.track_data_request() == bytes([11]) + (7).to_bytes(4, "little")


def test_registration_seen_even_when_rejected_without_error_text(
    adapter: BroadcastAdapter,
) -> None:
    # 注册被拒但 errorMessage 为空：服务不能一直等到超时
    assert adapter.registration_seen is False

    adapter.feed(F.registration_result(9, success=False, error=""), now=0.0)

    assert adapter.registration_seen is True
    assert adapter.registration_succeeded is False


def test_registration_fields_are_exposed(adapter: BroadcastAdapter) -> None:
    adapter.feed(F.registration_result(3, read_only=True, error="read only mode"), now=0.0)

    assert adapter.connection_id == 3
    assert adapter.registration_succeeded is True
    assert adapter.is_read_only is True
    assert adapter.registration_error == "read only mode"


def test_outdated_entry_list_requests_resync(adapter: BroadcastAdapter) -> None:
    """实时更新里的 carId 对不上名册 entryId → 解析器抛重同步信号。"""
    adapter.feed(F.car_update(6, driver_count=1), now=0.0)

    assert adapter.needs_entry_list_sync is True

    adapter.entry_list_synced()

    assert adapter.needs_entry_list_sync is False


# ---------------------------------------------------------------- 纯度 / 词表
def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_layer_only_uses_stdlib() -> None:
    allowed = {"__future__", "dataclasses", "typing", "math"}
    for path in (PKG_ROOT / "domain").glob("*.py"):
        roots = _imported_roots(path)
        assert roots <= allowed, f"{path.name} 引入了非标准库依赖: {roots - allowed}"


def test_sources_do_not_depend_on_backend() -> None:
    for path in (PKG_ROOT / "sources").glob("*.py"):
        roots = _imported_roots(path)
        assert "backend" not in roots, f"{path.name} 反向依赖了 backend"


def test_vocabulary_tables_are_consistent() -> None:
    assert "unknown" in CAR_LOCATIONS and "garage" in CAR_LOCATIONS
    assert "session" in SESSION_PHASES
    assert "race" in SESSION_TYPES
