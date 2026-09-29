"""Depiction clusters: within one concept, group pictograms that draw the
meaning the same way (trash can vs. X mark for "delete").

Features are the scale- and position-normalized 16x16 ink maps from
:mod:`handdown.metrics`, blurred slightly so stroke-width differences between
styles matter less than the overall shape. CLIP-style embeddings would be
better but need model weights and RAM the dev container lacks; this module
is the swap point.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.ndimage import binary_fill_holes, gaussian_filter
from scipy.spatial.distance import pdist

METHOD_VERSION = "1"
THRESHOLD = 0.45  # average-linkage correlation distance
SILHOUETTE_WEIGHT = 1.5


def prepare(vecs: np.ndarray) -> np.ndarray:
    """Blurred ink map plus filled silhouette, so outline and filled
    variants of the same drawing land close together."""
    maps = vecs.reshape(-1, 16, 16).astype(np.float32)
    ink = np.stack([gaussian_filter(m, 0.8) for m in maps])
    sil = np.stack([gaussian_filter(binary_fill_holes(m > 0.2).astype(np.float32), 0.8) for m in maps])
    flat = np.concatenate([ink.reshape(len(maps), -1), SILHOUETTE_WEIGHT * sil.reshape(len(maps), -1)], axis=1)
    flat -= flat.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(flat, axis=1, keepdims=True)
    norm[norm == 0] = 1
    return flat / norm


def group(vecs: np.ndarray, threshold: float = THRESHOLD) -> np.ndarray:
    if len(vecs) == 1:
        return np.array([1])
    x = prepare(vecs)
    z = linkage(np.nan_to_num(pdist(x, "cosine"), nan=1.0), method="average")
    return fcluster(z, t=threshold, criterion="distance")


def run(conn: sqlite3.Connection, progress: Any = None, min_concept_size: int = 1) -> int:
    conn.execute("DELETE FROM cluster_member")
    conn.execute("DELETE FROM semantic_meta")
    conn.execute("DELETE FROM depiction_cluster")
    concepts = conn.execute(
        """SELECT pc.concept_id, COUNT(*) FROM pictogram_concept pc
           JOIN pictogram p ON p.id = pc.pictogram_id
           WHERE pc.method IN ('dictionary', 'ai', 'manual')
             AND p.color_class IN ('native', 'derivable', 'threshold')
           GROUP BY 1 HAVING COUNT(*) >= ?""",
        (min_concept_size,),
    ).fetchall()
    made = 0
    for i, (cid, _) in enumerate(concepts):
        rows = conn.execute(
            """SELECT p.id, p.source_id, f.vec, COALESCE(r.value, 0) FROM pictogram_concept pc
               JOIN pictogram p ON p.id = pc.pictogram_id
               JOIN feature f ON f.pictogram_id = p.id
               LEFT JOIN rating r ON r.pictogram_id = p.id AND r.metric = 'legibility' AND r.is_override = 0
               WHERE pc.concept_id = ? AND pc.method IN ('dictionary', 'ai', 'manual')
                 AND p.color_class IN ('native', 'derivable', 'threshold')""",
            (cid,),
        ).fetchall()
        # Empty renders (nothing visible after normalization) cannot be compared.
        rows = [r for r in rows if any(np.frombuffer(r[2], dtype=np.float16))]
        if not rows:
            continue
        ids = np.array([r[0] for r in rows])
        sources = [r[1] for r in rows]
        vecs = np.stack([np.frombuffer(r[2], dtype=np.float16) for r in rows])
        leg = np.array([r[3] for r in rows])
        labels = group(vecs)
        x = prepare(vecs)
        # Distinct sources per concept, for convention strength.
        total_sources = len(set(sources))
        for lab in np.unique(labels):
            idx = np.nonzero(labels == lab)[0]
            centroid = x[idx].mean(axis=0)
            dist = 1 - x[idx] @ centroid / (np.linalg.norm(centroid) or 1)
            # Representative: close to the centroid, legible at small size.
            score = -dist + 0.002 * leg[idx]
            rep = int(ids[idx[int(np.argmax(score))]])
            n_src = len({sources[j] for j in idx})
            cur = conn.execute(
                """INSERT INTO depiction_cluster (concept_id, representative_id, convention_strength, size, source_count)
                   VALUES (?,?,?,?,?) RETURNING id""",
                (cid, rep, round(100 * n_src / total_sources, 1), len(idx), n_src),
            )
            cl = cur.fetchone()[0]
            conn.executemany("INSERT INTO cluster_member VALUES (?,?)", [(cl, int(ids[j])) for j in idx])
            made += 1
        if progress and i % 2000 == 0:
            progress(i, len(concepts))
            conn.commit()
    from .vault import apply_overrides  # cluster ids changed; re-attach hand edits

    apply_overrides(conn)
    conn.commit()
    return made
