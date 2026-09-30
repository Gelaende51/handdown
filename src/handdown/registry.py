"""The source registry (``sources.yaml``): platforms and sources recorded by
hand or by discovery. It is synced into the database, which stays the single
source of truth for everything harvested.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from . import db
from .config import Config

PLATFORM_FIELDS = ("id", "name", "url", "kind", "api_url", "reachable", "notes", "found_via")
SOURCE_FIELDS = (
    "id",
    "platform_id",
    "name",
    "url",
    "license_spdx",
    "license_url",
    "author",
    "designer",
    "year",
    "found_via",
    "harvest_status",
    "adapter",
    "adapter_args",
    "system",
    "region",
    "domain",
    "category",
    "grid_size",
    "notes",
)


def load(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {"platforms": [], "sources": [], "searches": []}
    data = yaml.safe_load(path.read_text()) or {}
    return {k: data.get(k) or [] for k in ("platforms", "sources", "searches")}


def sync(conn: sqlite3.Connection, path: Path) -> int:
    data = load(path)
    # Searches done outside the pipeline (web search by an agent or a person).
    # found_via "websearch:<id>" is rewritten to the search_log id.
    search_ids: dict[str, str] = {}
    for q in data["searches"]:
        row = conn.execute("SELECT id FROM search_log WHERE engine=? AND query=?", (q["engine"], q["query"])).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO search_log (query, engine, seed, run_at, candidates_found, notes) VALUES (?,?,?,?,?,?) RETURNING id",
                (q["query"], q["engine"], q.get("seed", "manual"), str(q.get("run_at") or db.now()), 0, q.get("notes")),
            ).fetchone()
        search_ids[f"websearch:{q['id']}"] = f"search:{row[0]}"
    for p in data["platforms"]:
        row = {k: p.get(k) for k in PLATFORM_FIELDS}
        row["found_via"] = search_ids.get(row["found_via"], row["found_via"])
        row["first_seen"] = p.get("first_seen") or db.now()[:10]
        db.upsert(conn, "platform", row, ("id",))
    for s in data["sources"]:
        row = {k: s.get(k) for k in SOURCE_FIELDS}
        row["found_via"] = search_ids.get(row["found_via"], row["found_via"])
        # Never downgrade a source the pipeline already harvested.
        cur = conn.execute("SELECT harvest_status FROM source WHERE id=?", (s["id"],)).fetchone()
        if (cur and cur[0] in ("harvested", "failed", "rejected")) or not row["harvest_status"]:
            row.pop("harvest_status")  # keep the pipeline's status (or the 'candidate' default)
        db.upsert(conn, "source", row, ("id",))
    for via in search_ids.values():
        conn.execute(
            """UPDATE search_log SET candidates_found =
                   (SELECT COUNT(*) FROM source WHERE found_via = ?) + (SELECT COUNT(*) FROM platform WHERE found_via = ?)
               WHERE id = ?""",
            (via, via, int(via.split(":")[1])),
        )
    conn.commit()
    return len(data["platforms"]) + len(data["sources"])


def adapter(name: str, cfg: Config, conn: sqlite3.Connection):
    if name == "iconify":
        from .adapters.iconify import IconifyAdapter

        return IconifyAdapter(cfg, conn)
    if name in ("git-svg", "npm-svg"):
        from .adapters.tarball import GitSvgAdapter, NpmSvgAdapter

        return (GitSvgAdapter if name == "git-svg" else NpmSvgAdapter)(cfg, conn)
    if name == "commons":
        from .adapters.commons import CommonsAdapter

        return CommonsAdapter(cfg, conn)
    if name == "font":
        from .adapters.font import FontAdapter

        return FontAdapter(cfg, conn)
    raise SystemExit(f"unknown adapter: {name}")
