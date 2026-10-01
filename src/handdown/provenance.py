"""Origin of every image and text classification (table ``classification``).

Classifiers write here as they decide; ``backfill`` recovers what the catalog
and the committed benchmark files still hold from before the log existed."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import db

COLUMNS = (
    "source_id",
    "original_id",
    "pictogram_id",
    "subject",
    "subject_id",
    "field",
    "value",
    "raw",
    "confidence",
    "method",
    "model",
    "input",
    "run",
    "context",
    "created_at",
)
CLAUDE = "sonnet"  # the Claude Code alias the AI passes ran with (ai.MODEL)


def record(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Log classifications; a repeat of the same one is ignored. The pictogram's
    source and original id are filled in from ``pictogram_id``. Returns how
    many were new. The caller commits."""
    now = db.now()
    n = 0
    for row in rows:
        r = {k: row.get(k) for k in COLUMNS}
        if r["pictogram_id"] is not None and r["source_id"] is None:
            p = conn.execute("SELECT source_id, original_id FROM pictogram WHERE id=?", (r["pictogram_id"],)).fetchone()
            if p:
                r["source_id"], r["original_id"] = p[0], p[1]
        if isinstance(r["context"], dict):
            r["context"] = json.dumps(r["context"], ensure_ascii=False)
        r["created_at"] = r["created_at"] or now
        n += conn.execute(
            f"INSERT OR IGNORE INTO classification ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", [r[k] for k in COLUMNS]
        ).rowcount
    return n


def _representative(conn: sqlite3.Connection, depiction_id: int) -> int | None:
    # SQLite takes the bare column from the row holding MAX(size)
    row = conn.execute("SELECT representative_id, MAX(size) FROM style_group WHERE depiction_id=?", (depiction_id,)).fetchone()
    return row[0] if row else None


def backfill(conn: sqlite3.Connection, bench_dir: Path = Path("work/bench")) -> dict[str, int]:
    """Log what survives from before the log: Claude's hierarchy answers as
    stored on depictions (raw answers were not kept), probe results, and the
    benchmark and text benchmark answer files. Safe to repeat."""
    note = {"note": "backfilled; raw answer not kept, values as stored in the catalog"}
    counts = {"ai": 0, "probe": 0, "bench": 0, "text": 0}
    groups = conn.execute(
        """SELECT g.id, g.representative_id, g.assessed_at, d.id AS did, d.object_id, d.description, d.view, d.varieties FROM style_group g
           JOIN depiction d ON d.id = g.depiction_id WHERE g.assessed_at IS NOT NULL AND d.method = 'ai'"""
    ).fetchall()
    for g in groups:
        base = dict(
            pictogram_id=g["representative_id"],
            subject="style_group",
            subject_id=g["id"],
            method="ai",
            model=CLAUDE,
            input="image",
            run="backfill",
            context=note,
            created_at=g["assessed_at"],
        )
        rows = [
            {**base, "field": "object", "value": g["object_id"], "raw": g["description"]},
            {**base, "field": "view", "value": g["view"]},
            {**base, "field": "varieties", "value": g["varieties"]},
        ]
        rows += [
            {**base, "field": "meaning", "value": m[0]}
            for m in conn.execute("SELECT concept_id FROM meaning_link WHERE depiction_id=? AND source='ai'", (g["did"],))
        ]
        counts["ai"] += record(conn, rows)
    for d in conn.execute("SELECT id, object_id, description FROM depiction WHERE method='probe'").fetchall():
        m = re.search(r"p=([0-9.]+)", d["description"] or "")
        counts["probe"] += record(
            conn,
            [
                dict(
                    pictogram_id=_representative(conn, d["id"]),
                    subject="depiction",
                    subject_id=d["id"],
                    field="object",
                    value=d["object_id"],
                    raw=d["description"],
                    confidence=float(m.group(1)) if m else None,
                    method="probe",
                    model=PROBE_MODEL,
                    input="image",
                    run="backfill",
                    context=note,
                )
            ],
        )
    sample_path, text_path = bench_dir / "sample.jsonl", bench_dir / "text.jsonl"
    sample = {r["key"]: r for r in map(json.loads, sample_path.read_text().splitlines())} if sample_path.exists() else {}
    texts = {r["key"]: r for r in map(json.loads, text_path.read_text().splitlines())} if text_path.exists() else {}
    for path in sorted((bench_dir / "results").glob("*.jsonl")):
        text = path.name.startswith("text-")
        rows = []
        for a in map(json.loads, path.read_text().splitlines()):
            if not a.get("answer"):
                continue
            if text:
                item = texts.get(a["key"], {})
                rows.append(
                    dict(
                        subject="depiction",
                        subject_id=a["key"],
                        field="object",
                        value=a["answer"],
                        raw=a["answer"],
                        confidence=a.get("p"),
                        method=a["model"],
                        model=a["model"],
                        input="text",
                        run=path.stem,
                        context={"text": item.get("state"), "options": list(item.get("options", {}))},
                    )
                )
                continue
            s = sample.get(a["key"], {})
            pid = conn.execute("SELECT id FROM pictogram WHERE source_id=? AND original_id=?", (s.get("source_id"), s.get("original_id"))).fetchone()
            extra = {k: v for k, v in a.items() if k not in ("key", "model", "answer")}
            rows.append(
                dict(
                    source_id=s.get("source_id"),
                    original_id=s.get("original_id"),
                    pictogram_id=pid[0] if pid else None,
                    subject="depiction",
                    subject_id=a["key"],
                    field="object",
                    raw=a["answer"],
                    method="bench",
                    model=a["model"],
                    input="image",
                    run=path.stem,
                    context=extra,
                )
            )
        counts["text" if text else "bench"] += record(conn, rows)
    conn.commit()
    return counts


# The linear probe (vision.train_probe): softmax regression on both embeddings.
PROBE_MODEL = "probe: softmax regression on google/siglip-base-patch16-224 + facebook/dinov2-small embeddings, trained on Claude's labels"
