"""Run composite classification over the catalog (chunked, restartable)."""

from __future__ import annotations

import json
import multiprocessing as mp
import sqlite3
from pathlib import Path
from typing import Any

from .. import db
from ..config import Config
from .merge import Composition, classify
from .names import name_evidence
from .shape import ShapeEvidence, shape_evidence

CHUNK = 2000
# Sources where frames are sign classes and names are codes (ISO 7010 P001 ...)
SIGN_DOMAINS = {"safety", "hazard", "public-information", "traffic", "transport", "equipment", "accessibility", "humanitarian"}


def _work(args: tuple[int, str | None, str | None, str | None, int, str | None]) -> tuple[int, Composition | None, list[str]]:
    pid, name, tags, path, has_text, domain = args
    ev_name = name_evidence(name or "", json.loads(tags or "[]"))
    expect_negation = any(p.role == "negation" for p in ev_name.parts)
    try:
        ev_shape = shape_evidence(Path(path).read_text(), expect_negation) if path else ShapeEvidence()
    except (OSError, ValueError):
        ev_shape = ShapeEvidence()
    return pid, classify(ev_name, ev_shape, bool(has_text), sign_domain=domain in SIGN_DOMAINS), ev_name.base


def _clear(conn: sqlite3.Connection, pid: int) -> None:
    conn.execute("DELETE FROM composition_part WHERE pictogram_id=?", (pid,))
    conn.execute("DELETE FROM composition_relation WHERE pictogram_id=?", (pid,))


def _store(conn: sqlite3.Connection, pid: int, c: Composition, base_tokens: list[str], now: str) -> None:
    from ..concepts import resolve

    _clear(conn, pid)
    conn.execute(
        "INSERT OR REPLACE INTO composition VALUES (?,?,?,?,?,?,?,?)",
        (pid, c.kind, c.font_type, c.fit, int(c.conflict), c.confidence, "rules", now),
    )
    partners = [resolve([t]).id for t in base_tokens[1:]] if len(base_tokens) > 1 else []
    base_concept = resolve(base_tokens[:1] if partners else base_tokens).id if base_tokens else None
    for no, p in enumerate(c.parts):
        concept = base_concept if p["role"] in ("base", "repetition") else None
        if p["role"] == "partner" and partners:
            concept = partners.pop(0)
        s = p.get("size") or {}
        conn.execute(
            "INSERT INTO composition_part VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                no,
                p["role"],
                p.get("label"),
                concept,
                p.get("position"),
                p.get("count", 1),
                s.get("area_ratio"),
                s.get("extent_ratio"),
                s.get("glyph_share"),
                s.get("px16"),
                s.get("size_class"),
            ),
        )
    for a, b, rel in c.relations:
        conn.execute("INSERT OR REPLACE INTO composition_relation VALUES (?,?,?,?)", (pid, a, b, rel))


def run(conn: sqlite3.Connection, cfg: Config, limit: int | None = None, workers: int = 1, log: Any = print) -> dict[str, int]:
    """Classify every unique, valid pictogram. Rule rows are replaced; rows set
    by hand (method 'manual') or by the AI pass (method 'ai') are kept."""
    kept = {r[0] for r in conn.execute("SELECT pictogram_id FROM composition WHERE method IN ('manual', 'ai')")}
    conn.execute("DELETE FROM composition WHERE method='rules'")
    counts = {"seen": 0, "composites": 0, "conflicts": 0}
    last = 0
    now = db.now()
    pool = mp.get_context("forkserver").Pool(workers) if workers > 1 else None
    try:
        while True:
            rows = conn.execute(
                """SELECT p.id, p.original_name, p.raw_tags, p.norm_path, p.has_text, s.domain
                   FROM pictogram p JOIN source s ON s.id = p.source_id
                   WHERE p.id > ? AND p.duplicate_of IS NULL AND p.svg_valid = 1 ORDER BY p.id LIMIT ?""",
                (last, CHUNK),
            ).fetchall()
            if not rows:
                break
            last = rows[-1][0]
            tasks = [(r[0], r[1], r[2], str(cfg.resolve(r[3])) if r[3] else None, r[4], r[5]) for r in rows if r[0] not in kept]
            results = pool.imap_unordered(_work, tasks, chunksize=64) if pool else map(_work, tasks)
            for pid, comp, base in results:
                counts["seen"] += 1
                if comp is None:
                    _clear(conn, pid)
                    continue
                _store(conn, pid, comp, base, now)
                counts["composites"] += 1
                counts["conflicts"] += int(comp.conflict)
            conn.commit()
            log(f"  {counts}")
            if limit and counts["seen"] >= limit:
                break
    finally:
        if pool:
            pool.close()
    return counts
