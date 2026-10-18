"""SQLite 连接与建表。

设计要点：
- 每次操作开一个短连接，写库不跨线程共享连接；
- WAL + busy_timeout，允许采集线程落库时 REST 同时读；
- 所有 SQL 使用参数化查询。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    length_m  REAL,
    spline      TEXT NOT NULL DEFAULT '[]',
    sectors     TEXT NOT NULL DEFAULT '[0.3333,0.6667]',
    date        TEXT,
    map_source  TEXT NOT NULL DEFAULT 'generated'   -- generated / recorded
);

CREATE TABLE IF NOT EXISTS sessions (
    id        TEXT PRIMARY KEY,
    track_id  TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    car_name  TEXT,
    date      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS laps (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    no         INTEGER NOT NULL,
    ms         INTEGER NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('ok','invalid','out')),
    valid      INTEGER NOT NULL CHECK (valid IN (0,1)),
    s1         INTEGER,
    s2         INTEGER,
    s3         INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (session_id, no)
);

CREATE TABLE IF NOT EXISTS samples (
    lap_id     TEXT NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    p          REAL NOT NULL,
    speed      REAL NOT NULL,
    rpm        INTEGER NOT NULL,
    gear       INTEGER NOT NULL,
    throttle   REAL NOT NULL,
    brake      REAL NOT NULL,
    steer      REAL NOT NULL,
    fl_p REAL, fl_t REAL, fl_bt REAL,
    fr_p REAL, fr_t REAL, fr_bt REAL,
    rl_p REAL, rl_t REAL, rl_bt REAL,
    rr_p REAL, rr_t REAL, rr_bt REAL,
    t_ms       REAL,
    g_long     REAL,
    g_lat      REAL,
    PRIMARY KEY (lap_id, idx)
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_track_date ON sessions(track_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_laps_session_no     ON laps(session_id, no);
CREATE INDEX IF NOT EXISTS idx_laps_session_ms     ON laps(session_id, ms);
CREATE INDEX IF NOT EXISTS idx_laps_kind_valid     ON laps(kind, valid);
CREATE INDEX IF NOT EXISTS idx_tracks_date         ON tracks(date DESC);
"""

SAMPLE_COLUMNS = (
    "lap_id", "idx", "p", "speed", "rpm", "gear", "throttle", "brake", "steer",
    "fl_p", "fl_t", "fl_bt", "fr_p", "fr_t", "fr_bt",
    "rl_p", "rl_t", "rl_bt", "rr_p", "rr_t", "rr_bt",
    "t_ms", "g_long", "g_lat",
)


class Database:
    """轻量 SQLite 封装，每次操作独立连接。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.is_ok = False
        self.last_error: str | None = None

    # ------------------------------------------------------------------ 连接
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ 初始化
    def initialize(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self.connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(SCHEMA)
                # 轻量迁移：旧库没有 map_source 列
                columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
                if "map_source" not in columns:
                    conn.execute(
                        "ALTER TABLE tracks ADD COLUMN map_source TEXT NOT NULL DEFAULT 'generated'"
                    )

                # v2 迁移：不再保存出场圈，并按 session 重新从 1 编号
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version < 2:
                    conn.execute("DELETE FROM laps WHERE kind = 'out'")
                    session_ids = [
                        row[0] for row in conn.execute("SELECT id FROM sessions").fetchall()
                    ]
                    for session_id in session_ids:
                        rows = conn.execute(
                            "SELECT id FROM laps WHERE session_id = ? "
                            "ORDER BY no ASC, created_at ASC",
                            (session_id,),
                        ).fetchall()
                        if not rows:
                            continue
                        # 先改成负数避开 UNIQUE(session_id, no)，再顺序编号
                        conn.execute(
                            "UPDATE laps SET no = -no WHERE session_id = ?", (session_id,)
                        )
                        for index, row in enumerate(rows, start=1):
                            conn.execute(
                                "UPDATE laps SET no = ? WHERE id = ?", (index, row[0])
                            )
                    conn.execute("PRAGMA user_version = 2")
                conn.commit()
            finally:
                conn.close()
            self.is_ok = True
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - 启动阶段任何失败都不应让 sidecar 崩溃
            self.is_ok = False
            self.last_error = str(exc)
            return False

    def close(self) -> None:
        """WAL 模式下没有需要显式关闭的常驻连接；预留接口。"""
