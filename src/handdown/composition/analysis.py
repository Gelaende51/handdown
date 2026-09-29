"""Queries over classified composites for the vault, HTML and rules index."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from typing import Any

SIMPLE_KEYS = {"negation": "negated", "repetition": "repeated", "partner": "with partner", "text": "with text", "decoration": "decorated"}


def _key(role: str, label: str | None) -> str | None:
    if role in SIMPLE_KEYS:
        return SIMPLE_KEYS[role]
    if role == "frame":
        return f"framed:{label}"
    if role == "modifier":
        return f"with:{label}"
    return None


def combinations(conn: sqlite3.Connection, concept_id: str) -> dict[str, list[sqlite3.Row]]:
    """Composites that contain the concept, grouped by operator. A composite
    with several operators appears in each of their groups."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    rows = conn.execute(
        """SELECT c.pictogram_id, c.kind, c.font_type, c.fit, p.original_name, p.norm_path, p.original_url, p.source_id
           FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
           WHERE c.pictogram_id IN (SELECT pictogram_id FROM composition_part WHERE concept_id = ?)""",
        (concept_id,),
    ).fetchall()
    for r in rows:
        ops = conn.execute("SELECT role, label FROM composition_part WHERE pictogram_id=? AND role != 'base'", (r["pictogram_id"],)).fetchall()
        keys = {k for k in (_key(o["role"], o["label"]) for o in ops) if k}
        for k in sorted(keys) or ["other"]:
            groups[k].append(r)
    return dict(groups)


def rules_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Counts behind the composition-rules index note."""
    fit = dict(conn.execute("SELECT fit, COUNT(*) FROM composition GROUP BY 1").fetchall())
    font = dict(conn.execute("SELECT font_type, COUNT(*) FROM composition GROUP BY 1").fetchall())
    kind = dict(conn.execute("SELECT kind, COUNT(*) FROM composition GROUP BY 1").fetchall())
    ops = conn.execute(
        "SELECT role, COALESCE(label, ''), COUNT(*) FROM composition_part WHERE role != 'base' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60"
    ).fetchall()
    mod_px: dict[str, list[float]] = defaultdict(list)
    for f, px in conn.execute(
        """SELECT c.fit, p.px16 FROM composition_part p JOIN composition c ON c.pictogram_id = p.pictogram_id
           WHERE p.role = 'modifier' AND p.px16 IS NOT NULL"""
    ):
        mod_px[f].append(px)
    legibility = dict(
        conn.execute(
            """SELECT c.fit, ROUND(AVG(r.value), 1) FROM composition c JOIN rating r ON r.pictogram_id = c.pictogram_id
               WHERE r.metric = 'legibility' AND r.is_override = 0 GROUP BY 1"""
        ).fetchall()
    )
    return {
        "fit": fit,
        "font_type": font,
        "kind": kind,
        "operators": [tuple(o) for o in ops],
        "modifier_px16_median": {k: round(statistics.median(v), 1) for k, v in mod_px.items() if v},
        "legibility_by_fit": legibility,
        "conflicts": conn.execute("SELECT COUNT(*) FROM composition WHERE conflict = 1").fetchone()[0],
    }
