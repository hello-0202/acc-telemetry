"""内置赛道目录。

length_m 取常见 GT3 赛道长度；seed 与前端原型一致，保证同一赛道形状稳定。
真实测绘数据后续可替换本文件，不影响其他层。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrackMap:
    id: str
    name: str
    length_m: float | None
    seed: float
    sectors: tuple[float, ...]
    aliases: frozenset[str] = field(default_factory=frozenset)
    corners: tuple[dict, ...] = ()
    known: bool = True


TRACKS: dict[str, TrackMap] = {
    "spa": TrackMap(
        "spa", "Spa-Francorchamps", 7004.0, 1.7, (1 / 3, 2 / 3),
        frozenset({"spa", "spa_francorchamps", "spa_gp", "francorchamps"}),
    ),
    "monza": TrackMap(
        "monza", "Monza", 5793.0, 5.4, (1 / 3, 2 / 3),
        frozenset({"monza", "monza_gp", "autodromo_nazionale_monza"}),
    ),
    "zandvoort": TrackMap(
        "zandvoort", "Zandvoort", 4259.0, 9.1, (1 / 3, 2 / 3),
        frozenset({"zandvoort", "zand", "circuit_zandvoort"}),
    ),
    "silverstone": TrackMap(
        "silverstone", "Silverstone", 5891.0, 13.8, (1 / 3, 2 / 3),
        frozenset({"silverstone", "silv", "silverstone_gp"}),
    ),
    "barcelona": TrackMap(
        "barcelona", "Barcelona", 4657.0, 21.5, (1 / 3, 2 / 3),
        frozenset({"barcelona", "barc", "catalunya", "circuit_de_catalunya"}),
    ),
}
