"""SQLite access: the catalog database is the single source of truth."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"
MIGRATIONS = [("style_group", "assessed_at", "TEXT")]


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    schema = resources.files("handdown").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    # Columns added after a table was first created (CREATE IF NOT EXISTS keeps old tables).
    for table, column, decl in MIGRATIONS:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], key: tuple[str, ...]) -> None:
    """Insert a row, or update the given non-key columns if the key exists.

    ``None`` values never overwrite existing data, so partial updates from
    different stages do not erase each other.
    """
    row = {k: encode(v) for k, v in row.items()}
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    updates = ", ".join(f"{c} = COALESCE(excluded.{c}, {table}.{c})" for c in row if c not in key)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT ({', '.join(key)}) DO "
    sql += f"UPDATE SET {updates}" if updates else "NOTHING"
    conn.execute(sql, tuple(row.values()))


def encode(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


def log_error(conn: sqlite3.Connection, source_id: str, item: str, stage: str, error: str) -> None:
    conn.execute(
        "INSERT INTO harvest_error(source_id, item, stage, error, at) VALUES (?, ?, ?, ?, ?)",
        (source_id, item, stage, error[:2000], now()),
    )
