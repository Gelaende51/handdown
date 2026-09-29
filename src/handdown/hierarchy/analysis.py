"""Queries over the depiction hierarchy for the vault and HTML."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# Concepts that get a note: name concepts (depiction clusters), meanings and
# objects of the hierarchy. Rows are concept columns plus n_src and n.
NOTE_CONCEPTS_SQL = """
SELECT k.*, COUNT(DISTINCT p.source_id) AS n_src, COUNT(DISTINCT p.id) AS n
FROM concept k JOIN (
    SELECT c.concept_id AS cid, m.pictogram_id AS pid FROM depiction_cluster c JOIN cluster_member m ON m.cluster_id = c.id
    UNION SELECT ml.concept_id, sm.pictogram_id FROM meaning_link ml
        JOIN style_group g ON g.depiction_id = ml.depiction_id JOIN style_member sm ON sm.style_group_id = g.id
    UNION SELECT d.object_id, sm.pictogram_id FROM depiction d
        JOIN style_group g ON g.depiction_id = d.id JOIN style_member sm ON sm.style_group_id = g.id
        WHERE d.object_id IS NOT NULL
) x ON x.cid = k.id JOIN pictogram p ON p.id = x.pid
GROUP BY k.id HAVING n_src >= ? ORDER BY n_src DESC
"""


def drawn_as(conn: sqlite3.Connection, meaning_id: str) -> list[dict[str, Any]]:
    """Objects (with their depictions and style groups) used for a meaning,
    and each object's share of the independent sources drawing the meaning."""
    rows = conn.execute(
        """SELECT d.id, d.object_id, d.view, d.varieties, g.id AS gid, g.size, p.norm_path, p.source_id
           FROM meaning_link m JOIN depiction d ON d.id = m.depiction_id
           JOIN style_group g ON g.depiction_id = d.id JOIN style_member sm ON sm.style_group_id = g.id
           JOIN pictogram p ON p.id = sm.pictogram_id
           WHERE m.concept_id = ?""",
        (meaning_id,),
    ).fetchall()
    objects: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["object_id"] or "(unknown object)"
        o = objects.setdefault(key, {"object_id": r["object_id"], "sources": set(), "depictions": {}})
        o["sources"].add(r["source_id"])
        d = o["depictions"].setdefault(r["id"], {"id": r["id"], "view": r["view"], "varieties": json.loads(r["varieties"] or "[]"), "style_groups": {}})
        d["style_groups"].setdefault(r["gid"], {"id": r["gid"], "size": r["size"], "norm_path": r["norm_path"]})
    total = sum(len(o["sources"]) for o in objects.values()) or 1
    out = []
    for key, o in objects.items():
        label = conn.execute("SELECT label FROM concept WHERE id=?", (o["object_id"],)).fetchone() if o["object_id"] else None
        out.append(
            {
                "object_id": o["object_id"],
                "label": label[0] if label else key,
                "sources": len(o["sources"]),
                "share": len(o["sources"]) / total,
                "depictions": [{**d, "style_groups": list(d["style_groups"].values())} for d in o["depictions"].values()],
            }
        )
    return sorted(out, key=lambda x: -x["sources"])


def used_to_mean(conn: sqlite3.Connection, object_id: str) -> list[tuple[str, str, int]]:
    """Meanings an object is used for, by number of independent sources."""
    rows = conn.execute(
        """SELECT m.concept_id, COALESCE(k.label, m.concept_id), COUNT(DISTINCT p.source_id)
           FROM depiction d JOIN meaning_link m ON m.depiction_id = d.id LEFT JOIN concept k ON k.id = m.concept_id
           JOIN style_group g ON g.depiction_id = d.id JOIN style_member sm ON sm.style_group_id = g.id
           JOIN pictogram p ON p.id = sm.pictogram_id
           WHERE d.object_id = ? GROUP BY 1 ORDER BY 3 DESC""",
        (object_id,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]
