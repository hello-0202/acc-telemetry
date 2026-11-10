"""契约文件与读模型的一致性守卫。

``contracts/telemetry-state.json`` 是主题作者唯一的说明书（``mock-state.js`` 直接
深拷贝它的 ``example`` 当预览数据）。文档和代码各写一份必然会漂，这里用测试把
两边钉在一起：只要有人改了读模型的字段名却忘了改契约（或反过来），立刻失败。

只读文件、纯离线。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acc_telemetry.services.standings_model import (  # noqa: E402
    STANDINGS_REASONS,
    empty_standings,
)

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "telemetry-state.json"


def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_example_standings_has_same_keys_as_empty_structure() -> None:
    example = contract()["example"]["standings"]

    assert set(example) == set(empty_standings())


def test_example_row_keys_match_read_model_row_keys() -> None:
    example = contract()["example"]["standings"]

    assert set(example["rows"][0]) == set(example["self"]) - {"gapAheadM", "gapBehindM"}


def test_example_self_and_rows_are_internally_consistent() -> None:
    standings = contract()["example"]["standings"]

    # example 是主题预览数据，别写自相矛盾的值（自己那行 gap 必须是 0）
    self_row = standings["self"]
    assert self_row["gapToSelfM"] == 0.0
    assert standings["trackMeters"] > 0
    assert standings["available"] is True
    assert standings["reason"] is None
    assert any(item["carId"] == self_row["carId"] for item in standings["rows"])


def test_every_standings_row_field_is_documented() -> None:
    data = contract()
    documented = {
        field["path"] for field in data["fields"] if field["path"].startswith("standings.")
    }

    # 行级字段要么写成 standings.rows[].x，要么写成 standings.self.x（self.* 只列特有字段）
    row_fields = {
        path.split("[].")[-1].split(".")[-1]
        for path in documented
        if "rows[]" in path
    }
    for key in data["example"]["standings"]["rows"][0]:
        assert key in row_fields, f"契约 fields 里缺 standings.rows[].{key}"


def test_standings_reason_enum_matches_read_model() -> None:
    enum = contract()["enums"]["standingsReason"]

    assert set(enum) == set(STANDINGS_REASONS)


def test_standings_is_not_declared_as_deferred() -> None:
    deferred = contract()["deferred"]

    assert "opponents" not in deferred  # 已由 standings 实现
    assert not any(key.startswith("standings") for key in deferred)
