"""探针脚本里的纯函数（不出网、不读游戏）。

探针本身要真机才能跑，但它的分析逻辑（编码探测、坐标假设检验、里程反推、名次对照）
是可离线验证的 —— 这些算错了，真机报告就会误导人。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import broadcast_probe as probe  # noqa: E402


# ---------------------------------------------------------------- 配置编码
def test_detect_encoding_recognises_acc_utf16le() -> None:
    """ACC 实测写的是 UTF-16LE 无 BOM，而且 utf-8 也能「解成功」，不能靠异常判断。"""
    raw = '{\n  "updListenerPort": 9000\n}\n'.encode("utf-16-le")
    assert probe.detect_encoding(raw) == "utf-16-le"


def test_detect_encoding_plain_utf8() -> None:
    assert probe.detect_encoding(b'{"updListenerPort": 9000}') == "utf-8-sig"


def test_read_text_any_encoding_parses_utf16le(tmp_path: Path) -> None:
    path = tmp_path / "broadcasting.json"
    path.write_bytes(json.dumps({"updListenerPort": 9000, "connectionPassword": ""}).encode("utf-16-le"))

    text, encoding = probe.read_text_any_encoding(path)

    assert encoding == "utf-16-le"
    assert json.loads(text)["updListenerPort"] == 9000


# ---------------------------------------------------------------- 坐标假设检验
def _sample(sm_xy: tuple[float, float], bc_xy: tuple[float, float], *, position: int = 1,
            bc_position: int | None = None,
            laps: int = 0, spline: float = 0.0, distance: float = 0.0) -> dict:
    return {
        "t": 0.0,
        "sm": {
            "player_xy": sm_xy,
            "car_ids": [1],
            "player_car_id": 1,
            "position": position,
            "completed_laps": laps,
            "spline": spline,
            "distance_m": distance,
        },
        "bc": {"world": bc_xy, "position": position if bc_position is None else bc_position},
    }


def test_coordinates_identifies_same_axis_convention() -> None:
    samples = [_sample((10.0, 20.0), (10.0, 20.0)), _sample((-5.0, 7.5), (-5.0, 7.5))]

    result = probe.analyse_coordinates(samples)

    assert result["samples"] == 2
    assert result["best"]["hypothesis"] == "bcX = +sm.x, bcY = +sm.z"
    assert result["best"]["mean_residual_m"] == 0.0


def test_coordinates_finds_swapped_axes() -> None:
    # 广播 X 取 sm 的 z、Y 取 sm 的 -x
    samples = [_sample((10.0, 20.0), (20.0, -10.0)), _sample((1.0, 2.0), (2.0, -1.0))]

    result = probe.analyse_coordinates(samples)

    assert result["best"]["hypothesis"] == "bcX = +sm.z, bcY = -sm.x"
    assert result["best"]["mean_residual_m"] == 0.0


def test_coordinates_ignores_empty_coordinates() -> None:
    samples = [_sample((0.0, 0.0), (0.0, 0.0)), _sample((1.0, 1.0), (0.0, 0.0))]

    assert probe.analyse_coordinates(samples)["samples"] == 0


# ---------------------------------------------------------------- 里程 / 名次
def test_track_length_estimate_from_odometer() -> None:
    samples = [_sample((0.0, 0.0), (0.0, 0.0), laps=1, spline=0.5, distance=10_500.0)]

    result = probe.analyse_track_length(samples, 7000.0)

    assert result["samples"] == 1
    assert result["estimate_from_shared_memory_m"] == 7000.0
    assert result["relative_error"] == 0.0


def test_track_length_without_odometer() -> None:
    assert probe.analyse_track_length([_sample((0.0, 0.0), (0.0, 0.0))], 7000.0)["samples"] == 0


def test_position_comparison_counts_mismatches() -> None:
    samples = [
        _sample((1.0, 1.0), (1.0, 1.0), position=2),
        _sample((1.0, 1.0), (1.0, 1.0), position=5, bc_position=4),
    ]

    result = probe.analyse_positions(samples)

    assert result["samples"] == 2
    assert result["mismatch_samples"] == 1


def test_delta_comparison_counts_broadcast_zero() -> None:
    def sample(magnitude: int, positive: bool, broadcast: int) -> dict:
        return {
            "sm": {"delta_lap_ms": magnitude, "delta_positive": int(positive)},
            "bc": {"delta_best_ms": broadcast},
        }

    result = probe.analyse_deltas(
        [
            sample(1_234, False, -1_234),
            sample(500, True, 0),  # 共享内存有值、广播为 0 → 记为「无参考圈」的证据
            sample(0, True, 0),    # 共享内存也没有 delta → 不参与统计
        ]
    )

    assert result["samples"] == 1
    assert result["mean_abs_diff_ms"] == 0.0
    assert result["broadcast_zero_while_shared_nonzero"] == 1
    assert result["sign_mismatch_samples"] == 0


def test_delta_comparison_without_samples() -> None:
    result = probe.analyse_deltas([])

    assert result["samples"] == 0
    assert "note" in result


def test_format_lap_time() -> None:
    assert probe._fmt_ms(0) == "-"
    assert probe._fmt_ms(101_100) == "1:41.100"
    assert probe._fmt_ms(9_500) == "9.500"
