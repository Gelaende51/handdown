"""Nested grouping (style groups inside depictions) and rule-based objects
and meanings. One linkage tree per name concept, cut twice, keeps the
levels nested."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.ndimage import binary_fill_holes, gaussian_filter
from scipy.spatial.distance import pdist

from ..concepts import _ensure, resolve
from .names import name_roles

# Cut heights on the filled-silhouette tree. Measured on a cup: outline vs
# filled 0.06, mirrored 0.41, with steam 0.61.
STYLE_T = 0.2
DEPICTION_T = 0.35


def silhouettes(vecs: np.ndarray) -> np.ndarray:
    """Filled, blurred silhouettes: invariant to fill/stroke (style), not to
    mirroring or added features (depiction, variety)."""
    maps = vecs.reshape(-1, 16, 16).astype(np.float32)
    sil = np.stack([gaussian_filter(binary_fill_holes(m > 0.2).astype(np.float32), 0.8).ravel() for m in maps])
    sil -= sil.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(sil, axis=1, keepdims=True)
    norm[norm == 0] = 1
    return sil / norm


def nested_labels(vecs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Style-group and depiction labels from one average-linkage tree."""
    if len(vecs) == 1:
        return np.array([1]), np.array([1])
    z = linkage(np.nan_to_num(pdist(silhouettes(vecs), "cosine"), nan=1.0), method="average")
    return fcluster(z, t=STYLE_T, criterion="distance"), fcluster(z, t=DEPICTION_T, criterion="distance")


def _clear_rules(conn: sqlite3.Connection) -> None:
    rule = "SELECT id FROM depiction WHERE method = 'rules'"
    conn.execute(f"DELETE FROM style_member WHERE style_group_id IN (SELECT id FROM style_group WHERE depiction_id IN ({rule}))")
    conn.execute(f"DELETE FROM style_group WHERE depiction_id IN ({rule})")
    conn.execute(f"DELETE FROM meaning_link WHERE depiction_id IN ({rule})")
    conn.execute("DELETE FROM depiction WHERE method = 'rules'")


def _meanings(members: list[sqlite3.Row], roles: list[Any], object_id: str | None) -> dict[str, tuple[str, float]]:
    """Meaning words (token -> (source, confidence)); the object itself when none."""
    meanings: dict[str, tuple[str, float]] = {}
    for r in roles:
        for t in r.meaning_tokens:
            # "folder-download": the action is applied to an object, which is
            # not yet a way of drawing the meaning (the AI pass can promote it)
            link = ("applied", 0.6) if r.object_tokens else ("name", 0.8)
            if t not in meanings or meanings[t][0] == "applied":
                meanings[t] = link
    for m in members:
        for alias in json.loads(m["raw_tags"] or "[]"):
            for t in name_roles(alias).meaning_tokens:
                meanings.setdefault(t, ("alias", 0.6))
    if not meanings and object_id:
        return {"": ("name", 0.5)}  # an object pictogram means the object itself (resolved by the caller)
    return meanings


def run(conn: sqlite3.Connection, log: Any = print) -> dict[str, int]:
    """Rebuild rule depictions. Pictograms already placed by the AI pass or by
    hand (depictions with method 'ai'/'manual') keep their place."""
    _clear_rules(conn)
    kept = {r[0] for r in conn.execute("SELECT pictogram_id FROM style_member")}
    counts: Counter[str] = Counter()
    seen: set[str] = {r[0] for r in conn.execute("SELECT id FROM concept")}
    concepts = [r[0] for r in conn.execute("SELECT DISTINCT concept_id FROM pictogram_concept WHERE method IN ('dictionary', 'manual', 'ai')")]
    for i, cid in enumerate(concepts):
        rows = conn.execute(
            """SELECT p.id, p.source_id, p.original_name, p.raw_tags, p.style, f.vec FROM pictogram_concept pc
               JOIN pictogram p ON p.id = pc.pictogram_id JOIN feature f ON f.pictogram_id = p.id
               WHERE pc.concept_id = ? AND pc.method IN ('dictionary', 'manual', 'ai')
                 AND p.duplicate_of IS NULL AND p.topic IS NULL AND p.color_class IN ('native', 'derivable', 'threshold')""",
            (cid,),
        ).fetchall()
        rows = [r for r in rows if r["id"] not in kept and any(np.frombuffer(r["vec"], dtype=np.float16))]
        if not rows:
            continue
        style, dep = nested_labels(np.stack([np.frombuffer(r["vec"], dtype=np.float16) for r in rows]))
        for d in np.unique(dep):
            idx = [j for j in range(len(rows)) if dep[j] == d]
            members = [rows[j] for j in idx]
            roles = [name_roles(m["original_name"] or "") for m in members]
            obj = Counter(" ".join(r.object_tokens) for r in roles if r.object_tokens).most_common(1)
            object_c = resolve(obj[0][0].split()) if obj else None
            object_id = object_c.id if object_c else None
            if object_c:
                _ensure(conn, object_c, seen)
            view = Counter(r.view for r in roles).most_common(1)[0][0]
            varieties = sorted({v for r in roles for v in r.varieties})
            did = conn.execute(
                """INSERT INTO depiction (name_concept_id, object_id, view, varieties, method, representative_id, size, source_count)
                   VALUES (?,?,?,?,'rules',?,?,?) RETURNING id""",
                (cid, object_id, view, json.dumps(varieties), members[0]["id"], len(members), len({m["source_id"] for m in members})),
            ).fetchone()[0]
            counts["depictions"] += 1
            for s in sorted({int(style[j]) for j in idx}):
                smembers = [rows[j] for j in idx if style[j] == s]
                gid = conn.execute(
                    "INSERT INTO style_group (depiction_id, representative_id, size, source_count, styles) VALUES (?,?,?,?,?) RETURNING id",
                    (
                        did,
                        smembers[0]["id"],
                        len(smembers),
                        len({m["source_id"] for m in smembers}),
                        json.dumps(Counter(m["style"] or "?" for m in smembers)),
                    ),
                ).fetchone()[0]
                conn.executemany("INSERT INTO style_member VALUES (?,?)", [(gid, m["id"]) for m in smembers])
                counts["style_groups"] += 1
            for token, (src, conf) in _meanings(members, roles, object_id).items():
                meaning = object_c if token == "" else resolve([token])
                if meaning is None:
                    continue
                _ensure(conn, meaning, seen)
                conn.execute("INSERT OR IGNORE INTO meaning_link VALUES (?,?,?,?)", (did, meaning.id, src, conf))
                counts["meaning_links"] += 1
        if i % 5000 == 0:
            conn.commit()
            log(f"  {i}/{len(concepts)} {dict(counts)}")
    conn.commit()
    return dict(counts)


def reresolve_ai_objects(conn: sqlite3.Connection) -> int:
    """Re-derive objects of AI depictions from the stored answer (description)
    with object_head: "down arrow in circle" -> arrow. No new model calls."""
    from .names import object_head

    seen: set[str] = {r[0] for r in conn.execute("SELECT id FROM concept")}
    n = 0
    for did, description, current in conn.execute(
        "SELECT id, description, object_id FROM depiction WHERE method = 'ai' AND description IS NOT NULL"
    ).fetchall():
        head, _extra = object_head(description)
        if not head:
            continue
        c = resolve(head)
        if c.id != current:
            _ensure(conn, c, seen)
            conn.execute("UPDATE depiction SET object_id=? WHERE id=?", (c.id, did))
            n += 1
    conn.commit()
    return n
