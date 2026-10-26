"""单圈采样序列的公共工具。

`samples` 按**时间**顺序存储（一圈约 1200 点），`p`（赛道进度）因此不保证
单调：出赛道绕圈、打转、倒车都会让 p 回退。任何「按进度 p 查值」的逻辑
（曲线图、PB 参考圈、自动生成赛道形状）都必须先把序列滤成 p 单调，否则
绕圈段的点会被插值/排序搞乱。
"""

from __future__ import annotations

from typing import Any, Iterable


def monotonic_progress(
    samples: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只保留 p 严格前进的样本，得到一条 p 单调递增的序列。

    绕圈段（p 不前进或回退）整段被跳过，所以返回序列可以安全地按 p
    二分或线性插值。本来 p 就单调的老数据过一遍是无损的。
    """
    out: list[dict[str, Any]] = []
    last = -1.0
    for sample in samples:
        try:
            p = float(sample.get("p"))
        except (TypeError, ValueError):
            continue
        if p != p or p <= last:  # NaN 或没前进
            continue
        last = p
        out.append(sample)
    return out
