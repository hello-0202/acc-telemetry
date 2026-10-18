"""配置仓储（key/value）。"""

from __future__ import annotations

import json

from ..db import Database

DEFAULTS = {
    "pushHz": 50,
    "autoStart": True,
}


class ConfigRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_all(self) -> dict:
        values = dict(DEFAULTS)
        with self.db.connection() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
        for row in rows:
            try:
                values[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                values[row["key"]] = row["value"]
        return values

    def set_many(self, values: dict) -> None:
        payload = [(k, json.dumps(v)) for k, v in values.items()]
        with self.db.connection() as conn:
            conn.executemany(
                """
                INSERT INTO config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                payload,
            )
