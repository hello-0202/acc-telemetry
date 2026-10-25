"""赛道几何服务：优先读 SQLite，缺失时解析本地 fastlane.ai 并缓存。"""

from __future__ import annotations

import logging

from ..repositories.track_repo import TrackRepository
from ..trackmaps.fastlane import find_fastlane, parse_fastlane
from ..trackmaps.from_track import build_spline_from_geometry, spline_is_degenerate

logger = logging.getLogger(__name__)


class GeometryService:
    def __init__(self, tracks: TrackRepository) -> None:
        self.tracks = tracks

    def get_or_parse(self, track_id: str, acc_track_name: str) -> dict | None:
        """返回真实赛道几何。

        1. 数据库已有 → 直接用；
        2. 否则在 ACC Cache 里找 fastlane.ai，解析后写回数据库；
        3. 找不到/解析失败 → None（前端回退到内置/录制地图）。
        """
        try:
            track = self.tracks.get(track_id)
        except Exception:  # noqa: BLE001
            track = None
            logger.exception("读取赛道几何失败")

        geometry = track.get("geometry") if track else None
        if not geometry:
            geometry = self._parse_and_cache(track_id, acc_track_name)

        if geometry:
            self._ensure_spline(track_id, track, geometry)
        return geometry

    def _parse_and_cache(self, track_id: str, acc_track_name: str) -> dict | None:
        path = find_fastlane(acc_track_name)
        if path is None:
            return None

        try:
            geometry = parse_fastlane(path)
        except Exception:  # noqa: BLE001
            logger.exception("解析 fastlane.ai 失败: %s", path)
            return None
        if geometry is None:
            return None

        try:
            self.tracks.set_geometry(track_id, geometry)
            logger.info(
                "已缓存赛道几何: %s (%d 点, %.1f m)",
                track_id,
                geometry["pointCount"],
                geometry["lengthM"],
            )
        except Exception:  # noqa: BLE001
            logger.exception("缓存赛道几何失败（本次仍返回内存数据）")
        return geometry

    def _ensure_spline(self, track_id: str, track: dict | None, geometry: dict) -> None:
        """用真实几何刷新轨道折线，保证没有 fastlane 数据时的回退地图也准确。"""
        if track is None:
            return
        source = track.get("map_source")
        spline = track.get("spline") or []
        # 实跑数据 / 已经由 fastlane 生成的折线不覆盖
        if source in ("recorded", "fastlane") and not spline_is_degenerate(spline):
            return

        spline = build_spline_from_geometry(geometry)
        if not spline:
            return
        try:
            self.tracks.set_spline(track_id, spline, source="fastlane")
            logger.info("已用真实几何更新赛道折线: %s (%d 点)", track_id, len(spline))
        except Exception:  # noqa: BLE001
            logger.exception("更新赛道折线失败")
