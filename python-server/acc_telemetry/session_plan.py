"""从 ACC 本地配置文件里读「这个比赛周末的正赛有多长」。

ACC 的共享内存只给**当前**会话的 `sessionTimeLeft`，不提供正赛长度；跑到排位赛
时想知道「正赛要准备多少油」就拿不到。单机自定义周末的设置在 ACC 退出/开局时
会写进：

    %USERPROFILE%\\Documents\\Assetto Corsa Competizione\\Config\\menuSettings.json
      └ seasonRaceEventData.<season>.raceEventData.CustomRace
          practiceLength / qualifyLength / raceLength（秒）

这里只做一件事：按当前赛道找到这个条目，返回 `raceLength`（毫秒）。
读不到、赛道对不上、或值不合理时返回 None —— 主题显示 `--`，绝不猜。

注意：联机比赛的赛程在服务器上，客户端本地没有；联机排位阶段拿不到正赛长度，
同样返回 None。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_SUBDIR = Path("Assetto Corsa Competizione") / "Config" / "menuSettings.json"
# 12 小时以上不可能是正常的正赛长度，按脏数据丢弃
_MAX_RACE_SECONDS = 12 * 3600.0

# 解析结果缓存：key = 文件路径，value = (mtime, data)。
# menuSettings.json 有一百多 KB，每圈都重新解析没必要；文件被 ACC 改写后
# mtime 变化，会自动重读。
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _documents_dirs() -> list[Path]:
    """Documents 目录候选（可能被 OneDrive 重定向，见环境变量）。"""
    dirs: list[Path] = []
    for env in ("USERPROFILE", "OneDrive", "OneDriveConsumer"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Documents")
    try:
        dirs.append(Path.home() / "Documents")
    except RuntimeError:
        pass
    # 去重但保持顺序
    seen: set[str] = set()
    unique = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def menu_settings_path() -> Path | None:
    for base in _documents_dirs():
        path = base / _CONFIG_SUBDIR
        if path.is_file():
            return path
    return None


def _load(path: Path) -> dict[str, Any] | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(str(path))
    if hit is not None and hit[0] == mtime:
        return hit[1]

    data: dict[str, Any] | None = None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.debug("读取 ACC menuSettings 失败：%s", exc)
        raw = b""
    # 这个文件的编码不固定：实测同一台机器上，
    #   · 运行中 / 某些退出路径写的是 UTF-8（里面还混过 0xfb 脏字节）
    #   · 另一些时候是带 BOM 的 UTF-16-LE（每字符两个字节，文件体积翻倍）
    # 依次尝试，最后一个 latin-1 永远能解，保证 JSON 结构能被解析出来。
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin-1"):
        try:
            parsed = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    _cache[str(path)] = (mtime, data)
    return data


def _entry_race_seconds(entry: Any, track_id: str) -> float | None:
    if not isinstance(entry, dict):
        return None
    entry_track = str(entry.get("trackName") or "").strip().lower()
    # 条目带了赛道名就必须和当前赛道一致；空值作为弱匹配放过
    if track_id and entry_track and entry_track != track_id:
        return None
    try:
        seconds = float(entry.get("raceLength") or 0)
    except (TypeError, ValueError):
        return None
    if 0 < seconds <= _MAX_RACE_SECONDS:
        return seconds
    return None


def _race_seconds(data: dict[str, Any], track_id: str) -> float | None:
    seasons = data.get("seasonRaceEventData")
    if not isinstance(seasons, dict) or not seasons:
        return None

    # 只看当前赛季：singlePlayerSeason 就是菜单里选的那一项（"Free" = 自定义比赛）。
    # 不能拿其它赛季的 CustomRace 条目 —— 每个赛季各存一份，赛道和时长都不一样，
    # 混着用会把 BGT 的 nurburgring 当成你现在的周末。
    season = data.get("singlePlayerSeason")
    if not isinstance(season, str) or season not in seasons:
        season = "Free" if "Free" in seasons else None
    if season is None:
        return None
    block = seasons.get(season)
    events = (block or {}).get("raceEventData") if isinstance(block, dict) else None
    if not isinstance(events, dict):
        return None
    # 自定义比赛周末就是 CustomRace 这一条
    return _entry_race_seconds(events.get("CustomRace"), track_id)


def race_duration_ms(track_id: str) -> int | None:
    """当前赛道在这个周末的正赛时长（毫秒）；未知返回 None。"""
    path = menu_settings_path()
    if path is None:
        return None
    data = _load(path)
    if data is None:
        return None
    seconds = _race_seconds(data, (track_id or "").strip().lower())
    if seconds is None:
        return None
    return int(round(seconds * 1000))
