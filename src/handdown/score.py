"""Catalog-wide scores: simplicity percentiles, convention strength,
distinctiveness and the weighted combined score."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import numpy as np

from . import db
from .cluster import prepare
from .config import DEFAULT_WEIGHTS

METHOD_VERSION = "1"
SIMPLICITY_KEYS = ("nodes", "components", "perimetric_complexity", "png_bytes64", "edge_density")


def _rank_pct(values: np.ndarray) -> np.ndarray:
    order = values.argsort(kind="stable")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    return ranks / max(1, len(values) - 1)


def _put(conn: sqlite3.Connection, rows: list[tuple[int, str, float, str | None, str]]) -> None:
    now = db.now()
    conn.executemany(
        """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, detail, method, method_version, computed_at, is_override)
           VALUES (?,?,?,COALESCE(?, (SELECT detail FROM rating WHERE pictogram_id=? AND metric=? AND is_override=0)),?,?,?,0)""",
        [(pid, metric, value, detail, pid, metric, method, METHOD_VERSION, now) for pid, metric, value, detail, method in rows],
    )


def simplicity(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT pictogram_id, detail FROM rating WHERE metric='simplicity' AND is_override=0 AND detail IS NOT NULL").fetchall()
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    raw = [json.loads(r[1]) for r in rows]
    mat = np.array([[d.get(k, 0) for k in SIMPLICITY_KEYS] for d in raw], dtype=np.float64)
    pct = np.mean([_rank_pct(mat[:, i]) for i in range(mat.shape[1])], axis=0)
    score = np.round(100 * (1 - pct), 1)
    _put(conn, [(pid, "simplicity", float(s), None, "render+percentile") for pid, s in zip(ids, score, strict=True)])
    conn.commit()
    return len(ids)


def convention(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT m.pictogram_id, c.convention_strength, c.source_count, c.id FROM cluster_member m
           JOIN depiction_cluster c ON c.id = m.cluster_id"""
    ).fetchall()
    _put(conn, [(pid, "convention", float(v), json.dumps({"cluster": cl, "sources": n}), "cluster-share") for pid, v, n, cl in rows])
    conn.commit()
    return len(rows)


def distinctiveness(conn: sqlite3.Connection, chunk: int = 512, dims: int = 48) -> int:
    """Shape distance from each cluster representative to the nearest
    representative of an unrelated concept (not the same concept, not
    parent/child, not sharing a parent)."""
    reps = conn.execute(
        """SELECT c.id, c.concept_id, COALESCE(k.parent_id, c.concept_id), f.vec
           FROM depiction_cluster c JOIN feature f ON f.pictogram_id = c.representative_id
           JOIN concept k ON k.id = c.concept_id"""
    ).fetchall()
    if len(reps) < 2:
        return 0
    cids = [r[0] for r in reps]
    concept = np.array([r[1] for r in reps])
    family = np.array([r[2] for r in reps])
    x = prepare(np.stack([np.frombuffer(r[3], dtype=np.float16) for r in reps]))
    # PCA to keep the all-pairs search affordable in memory and time.
    mean = x.mean(axis=0)
    xc = x - mean
    sample = xc[np.random.default_rng(0).choice(len(xc), min(len(xc), 20000), replace=False)]
    _, _, vt = np.linalg.svd(sample, full_matrices=False)
    p = (xc @ vt[:dims].T).astype(np.float32)
    p /= np.linalg.norm(p, axis=1, keepdims=True).clip(1e-6)
    nearest = np.zeros(len(p))
    partner = np.zeros(len(p), dtype=np.int64)
    for start in range(0, len(p), chunk):
        sim = p[start : start + chunk] @ p.T
        blk = slice(start, start + chunk)
        same = (
            (concept[blk, None] == concept[None, :])
            | (family[blk, None] == family[None, :])
            | (family[blk, None] == concept[None, :])
            | (concept[blk, None] == family[None, :])
        )
        sim[same] = -np.inf
        j = sim.argmax(axis=1)
        partner[blk] = j
        nearest[blk] = sim[np.arange(len(j)), j]
    dist = 1 - nearest  # cosine distance in PCA space, 0..2
    score = np.clip(100 * dist / 0.6, 0, 100).round(1)
    conn.execute("DELETE FROM confusion_pair WHERE evidence='embedding'")
    cluster_score = dict(zip(cids, score, strict=True))
    cluster_nearest = {cids[i]: str(concept[partner[i]]) for i in range(len(p))}
    pairs = [(concept[i], concept[partner[i]], cids[i], "embedding", round(float(nearest[i]), 3)) for i in range(len(p)) if nearest[i] > 0.9]
    conn.executemany("INSERT OR REPLACE INTO confusion_pair VALUES (?,?,?,?,?)", pairs)
    rows = conn.execute("SELECT cluster_id, pictogram_id FROM cluster_member").fetchall()
    _put(
        conn,
        [
            (pid, "distinctiveness", float(cluster_score[cl]), json.dumps({"cluster": cl, "nearest_concept": cluster_nearest[cl]}), "embedding-nn")
            for cl, pid in rows
            if cl in cluster_score
        ],
    )
    conn.commit()
    return len(rows)


def combined(conn: sqlite3.Connection, weights: dict[str, float] | None = None) -> int:
    """Weighted mean over the metrics a pictogram has; overrides win.
    Missing metrics (e.g. AI ratings not yet run) drop out and the weights
    are renormalized; the detail records which were used."""
    w = weights or DEFAULT_WEIGHTS
    scores: dict[int, dict[str, float]] = {}
    for pid, metric, value, _ov in conn.execute(
        "SELECT pictogram_id, metric, value, is_override FROM rating WHERE metric != 'combined' AND value IS NOT NULL ORDER BY is_override"
    ):
        if metric in w:
            scores.setdefault(pid, {})[metric] = value  # override rows come last and win
    rows = []
    for pid, m in scores.items():
        total = sum(w[k] for k in m)
        if not total:
            continue
        value = sum(w[k] * v for k, v in m.items()) / total
        rows.append((pid, "combined", round(value, 1), json.dumps({"used": sorted(m), "weights": {k: w[k] for k in m}}), "weighted-mean"))
    _put(conn, rows)
    conn.commit()
    return len(rows)


def run(conn: sqlite3.Connection, weights: dict[str, float] | None = None, log: Any = print) -> None:
    log(f"simplicity: {simplicity(conn)}")
    log(f"convention: {convention(conn)}")
    log(f"distinctiveness: {distinctiveness(conn)}")
    log(f"combined: {combined(conn, weights)}")
