"""Off-topic pictograms: kept as reference, left out of labelling, grouping
and exports. On topic are pictograms from any medium, game item and UI icons,
fictional logos, glyphs and symbols, and the logos of applications,
organizations, channels and websites; off topic are e.g. game character
sprites, coloured illustrations and raster copies of vector icons."""

from __future__ import annotations

import fnmatch
import sqlite3


def mark(conn: sqlite3.Connection, source_id: str, patterns: list[str], reason: str | None) -> int:
    """Mark the source's pictograms whose original id matches one of the glob
    ``patterns`` as off-topic (``reason``), or on topic again when ``reason``
    is None. Returns how many pictograms matched."""
    value = f"off-topic: {reason}" if reason else None
    ids = [
        pid
        for pid, oid in conn.execute("SELECT id, original_id FROM pictogram WHERE source_id = ?", (source_id,))
        if any(fnmatch.fnmatchcase(oid, p) for p in patterns)
    ]
    conn.executemany("UPDATE pictogram SET topic = ? WHERE id = ?", [(value, pid) for pid in ids])
    conn.commit()
    return len(ids)
