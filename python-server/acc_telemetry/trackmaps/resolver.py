"""ACC trackName → 内置赛道地图。"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

from .catalog import TRACKS, TrackMap
from .shapes import build_spline, fallback_seed

_SUFFIXES = ("_gp", "_2018", "_2019", "_2020", "_2021", "_2022", "_2023", "_2024")
_SPLINE_POINTS = 160


def slugify(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())).strip("_")


def prettify(value: str) -> str:
    text = (value or "").replace("_", " ").strip()
    if not text:
        return "Unknown Track"
    return " ".join(word.capitalize() for word in text.split())


@dataclass(frozen=True)
class ResolvedTrack:
    id: str
    name: str
    length_m: float | None
    seed: float
    sectors: tuple[float, ...]
    corners: tuple[dict, ...]
    known: bool

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lengthM": self.length_m,
            "spline": build_spline(self.seed, _SPLINE_POINTS),
            "sectors": list(self.sectors),
            "corners": list(self.corners),
            "geometryKnown": self.known,
        }


class TrackResolver:
    def __init__(self) -> None:
        self._cache: dict[str, ResolvedTrack] = {}
        self._alias_index: dict[str, str] = {}
        for tid, tm in TRACKS.items():
            self._alias_index[tid] = tid
            for alias in tm.aliases:
                self._alias_index[slugify(alias)] = tid

    def resolve(self, acc_track_name: str, configuration: str = "") -> ResolvedTrack:
        key = f"{acc_track_name}|{configuration}"
        cached = self._cache.get(key)
        if cached:
            return cached
        resolved = self._resolve(acc_track_name, configuration)
        self._cache[key] = resolved
        return resolved

    def _lookup(self, key: str) -> TrackMap | None:
        tid = self._alias_index.get(key)
        if tid:
            return TRACKS[tid]
        return None

    def _resolve(self, acc_track_name: str, configuration: str) -> ResolvedTrack:
        base = slugify(acc_track_name)
        config = slugify(configuration)
        candidates = []
        if base and config:
            candidates.append(f"{base}_{config}")
        candidates.append(base)

        # `spa_gp` → `spa`
        stripped = base
        for suffix in _SUFFIXES:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)]
                candidates.append(stripped)

        for key in candidates:
            tm = self._lookup(key)
            if tm:
                return self._from_map(tm)

        # 模糊匹配（ACC 名称可能带车型/配置后缀）
        for key in candidates:
            match = difflib.get_close_matches(key, list(self._alias_index), n=1, cutoff=0.75)
            if match:
                tm = TRACKS[self._alias_index[match[0]]]
                return self._from_map(tm)

        digest = hashlib.sha1((acc_track_name or "").encode("utf-8")).hexdigest()[:8]
        track_id = base or f"unknown_{digest}"
        return ResolvedTrack(
            id=track_id,
            name=prettify(acc_track_name) or "Unknown Track",
            length_m=None,
            seed=fallback_seed(track_id),
            sectors=(1 / 3, 2 / 3),
            corners=(),
            known=False,
        )

    @staticmethod
    def _from_map(tm: TrackMap) -> ResolvedTrack:
        return ResolvedTrack(
            id=tm.id,
            name=tm.name,
            length_m=tm.length_m,
            seed=tm.seed,
            sectors=tm.sectors,
            corners=tm.corners,
            known=tm.known,
        )
