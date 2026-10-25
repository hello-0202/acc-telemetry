"""配置仓储（JSON 文件）。

设置项不再落 SQLite，而是直接持久化到用户目录下的 config.json：
    %LOCALAPPDATA%\\acc-telemetry\\config.json
默认与数据库同目录，可用环境变量 ACC_TELEMETRY_DB 改变目录时一起跟随。

- 文件不存在时读取旧版 SQLite `config` 表做一次性迁移；
- 写入走「临时文件 + os.replace」，避免中途崩溃留下半个 JSON；
- 文件内容损坏时回退到默认值，不覆盖用户原文件。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from ..db import Database

logger = logging.getLogger(__name__)

DEFAULTS = {
    "pushHz": 50,
    "sampleHz": 100,
    "autoStart": True,
    "backgroundResident": False,
}


class ConfigRepository:
    def __init__(self, db: Database, path: str | Path | None = None) -> None:
        self.db = db
        self.path = Path(path) if path is not None else Path(db.path).parent / "config.json"
        self._lock = threading.Lock()

    def get_all(self) -> dict:
        with self._lock:
            return self._read()

    def set_many(self, values: dict) -> None:
        with self._lock:
            merged = self._read()
            merged.update(values)
            self._write(merged)

    # ------------------------------------------------------------------ 内部
    def _read(self) -> dict:
        values = dict(DEFAULTS)
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            migrated = self._migrate_from_db()
            values.update(migrated)
            if not migrated:
                # 首次运行也把默认值写出来，方便用户直接编辑 JSON
                try:
                    self._write(values)
                except OSError as exc:
                    logger.warning("初始化配置文件失败 %s: %s", self.path, exc)
            return values
        except OSError as exc:
            logger.warning("读取配置文件失败 %s: %s", self.path, exc)
            return values

        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("配置文件不是合法 JSON，已忽略：%s", self.path)
            return values

        if not isinstance(data, dict):
            logger.warning("配置文件顶层必须是 JSON 对象，已忽略：%s", self.path)
            return values

        values.update(data)
        return values

    def _migrate_from_db(self) -> dict:
        """旧版本把配置存在 SQLite config 表里；升级后首次读取时搬到 JSON。"""
        legacy: dict = {}
        if not self.db.is_ok:
            return legacy
        try:
            with self.db.connection() as conn:
                rows = conn.execute("SELECT key, value FROM config").fetchall()
        except sqlite3.Error:
            return legacy

        for row in rows:
            try:
                legacy[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                legacy[row["key"]] = row["value"]

        if legacy:
            try:
                self._write({**DEFAULTS, **legacy})
            except OSError as exc:
                logger.warning("配置迁移写入失败 %s: %s", self.path, exc)
        return legacy

    def _write(self, values: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(values, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
