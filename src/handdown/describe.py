"""Text inside pictograms, tags and free descriptions by Claude, for a sample
(docs/superpowers/specs/2026-10-02-describe-design.md).

Each run of text or characters is a text group, a compound part of the
pictogram. Visual information goes into tags from the standard vocabulary
(tags.py) as far as it can; what no tag covers is the residual text. The
description and interpretation speak about the whole composition."""

from __future__ import annotations

import json
import random
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import db, provenance
from .tags import canon_feature

VOCABULARY = {
    "direction": ["left", "right", "up", "down"],
    "view": ["front", "side", "top", "bottom", "three-quarter", "isometric", "partial"],
    "corners": ["rounded", "sharp", "mixed"],
    "ends": ["round", "square"],
    "frame": ["circle", "square", "triangle", "octagon", "diamond", "shield", "hexagon"],
    "style": ["outline", "filled", "mixed", "duotone"],
    "count": ["one", "two", "three", "many"],
    "figure": ["man", "woman", "child", "adult", "group", "wheelchair user"],
    "pose": ["standing", "sitting", "walking", "running", "lying", "bending", "reaching"],
}
PROMPT = """Each numbered cell shows one pictogram (large and at 16 px); its published name follows its number below.
For each pictogram:
- "text": every separate run of text or characters drawn in it (letters, digits, words, CJK characters), one entry per group:
  {{"text": "...", "script": "latin|han|hiragana|katakana|hangul|arabic|cyrillic|greek|digits|symbols|other", "position": "..."}};
  [] if there is none
- "tags": as much of what is visible as possible, only from this vocabulary (namespace:value):
{vocabulary}
  feature:<one plain noun> for each distinct visible element or detail (feature:steam, feature:wheel, feature:arrow);
  direction is where the main figure faces or points
- "residual": visible details no tag above can say, in a short phrase ("" if none)
- "description": one or two sentences describing the whole composition: what is drawn, how it is arranged, how it is drawn
- "interpretation": one sentence on what the pictogram conveys or is used for
Reply with JSON only: {{"1": {{"text": [...], "tags": [...], "residual": "...", "description": "...", "interpretation": "..."}}, ...}}"""
SYSTEM = "You read pictograms closely and describe them precisely. Reply with JSON only."


def _vocabulary_text() -> str:
    return "\n".join(f"  {ns}: {', '.join(values)}" for ns, values in VOCABULARY.items())


def accept_tag(tag: Any) -> str | None:
    """A tag from the vocabulary (features normalised), or None."""
    if not isinstance(tag, str) or ":" not in tag:
        return None
    ns, value = (x.strip().lower() for x in tag.split(":", 1))
    if ns == "feature":
        t = canon_feature(value)
        return t if t and t.startswith("feature:") else None
    return f"{ns}:{value}" if value in VOCABULARY.get(ns, []) else None


def sample(conn: sqlite3.Connection, n: int = 300, seed: int = 0) -> list[int]:
    """Pictograms to describe: a third with detected text, a third composites, a third at random (unique, on topic)."""
    rng = random.Random(seed)
    base = "FROM pictogram p WHERE p.duplicate_of IS NULL AND p.svg_valid = 1 AND p.topic IS NULL AND p.derived_from IS NULL AND p.norm_path IS NOT NULL"
    pools = [
        [r[0] for r in conn.execute(f"SELECT p.id {base} AND p.has_text = 1")],
        [r[0] for r in conn.execute(f"SELECT p.id {base} AND p.id IN (SELECT pictogram_id FROM composition)")],
        [r[0] for r in conn.execute(f"SELECT p.id {base}")],
    ]
    picked: list[int] = []
    for k, pool in enumerate(pools):
        want = n // 3 if k < 2 else n - len(picked)
        rest = [p for p in pool if p not in picked]
        picked += rng.sample(rest, min(want, len(rest)))
    return sorted(set(picked))[:n]


def _store(conn: sqlite3.Connection, pid: int, a: dict[str, Any], model: str, run: str, stats: dict[str, Any]) -> None:
    groups = [g for g in a.get("text") or [] if isinstance(g, dict) and isinstance(g.get("text"), str) and g["text"].strip()]
    accepted: list[str] = []
    rejected: list[str] = []
    for t in a.get("tags") or []:
        ok = accept_tag(t)
        if ok:
            accepted.append(ok)
        elif isinstance(t, str) and ":" in t and not t.lower().startswith("feature:"):
            rejected.append(t)  # outside the vocabulary: kept as residual text
    accepted = sorted(set(accepted))
    residual = "; ".join(x for x in [str(a.get("residual") or "").strip(), *rejected] if x)
    conn.execute("DELETE FROM pictogram_text WHERE pictogram_id = ? AND method = 'ai'", (pid,))
    for k, g in enumerate(groups):
        conn.execute(
            "INSERT INTO pictogram_text (pictogram_id, group_no, text, script, position, method, model) VALUES (?,?,?,?,?, 'ai', ?)",
            (pid, k, g["text"].strip(), (g.get("script") or "").lower() or None, g.get("position"), model),
        )
    conn.execute(
        "INSERT OR REPLACE INTO pictogram_description VALUES (?,?,?,?,?,?,?,?)",
        (pid, json.dumps(accepted), residual or None, a.get("description"), a.get("interpretation"), model, run, db.now()),
    )
    auto = {"described"} | ({"feature:text"} if groups else set()) | {f"text:{g['script'].lower()}" for g in groups if g.get("script")}
    new_tags = set(accepted) | auto
    conn.executemany("INSERT OR IGNORE INTO pictogram_tag (tag, pictogram_id) VALUES (?, ?)", [(t, pid) for t in new_tags])
    name = conn.execute("SELECT original_name FROM pictogram WHERE id = ?", (pid,)).fetchone()[0]
    conn.execute("DELETE FROM pictogram_search WHERE pictogram_id = ?", (pid,))
    conn.execute(
        "INSERT INTO pictogram_search (pictogram_id, name, text, description) VALUES (?,?,?,?)",
        (pid, name or "", " ".join(g["text"] for g in groups), " ".join(str(a.get(k) or "") for k in ("description", "interpretation", "residual"))),
    )
    base = dict(pictogram_id=pid, subject="pictogram", subject_id=pid, method="ai", model=model, input="image", run=run)
    provenance.record(
        conn,
        [
            {**base, "field": "text", "value": g["text"].strip(), "context": {"group": k, "script": g.get("script"), "position": g.get("position")}}
            for k, g in enumerate(groups)
        ]
        + [{**base, "field": "tag", "value": t} for t in accepted]
        + [{**base, "field": "description", "raw": a.get("description"), "context": {"interpretation": a.get("interpretation"), "residual": residual}}],
    )
    stats["described"] += 1
    stats["text_groups"] += len(groups)
    stats["tags"] += len(accepted)
    stats["rejected_tags"] += len(rejected)
    return None


def _refresh_counts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM tag_count")
    conn.execute("INSERT INTO tag_count SELECT tag, COUNT(*) FROM pictogram_tag GROUP BY tag")


def run(
    conn: sqlite3.Connection, cfg: Any, ids: list[int], batch: int = 10, model: str = "sonnet", effort: str | None = None, workdir: Any = None, log: Any = print
) -> dict[str, Any]:
    """Describe the given pictograms (sheets of ``batch``); already described ones are skipped.
    Raises ``ai.QuotaExceeded`` at the usage limit."""
    from . import ai

    workdir = Path(workdir or "data/describe")
    workdir.mkdir(parents=True, exist_ok=True)
    done = {r[0] for r in conn.execute("SELECT pictogram_id FROM pictogram_description")}
    todo = [i for i in ids if i not in done]
    name = f"{model}@effort={effort or 'off'}"
    stats: dict[str, Any] = {"described": 0, "text_groups": 0, "tags": 0, "rejected_tags": 0, "cost_usd": 0.0}
    for start in range(0, len(todo), batch):
        part = todo[start : start + batch]
        rows = {r[0]: r for r in conn.execute(f"SELECT id, original_name, norm_path FROM pictogram WHERE id IN ({','.join('?' * len(part))})", part)}
        part = [p for p in part if p in rows]
        names = "\n".join(f"{i}. {rows[p][1] or ''}" for i, p in enumerate(part, 1))
        image = ai.sheet([cfg.resolve(rows[p][2]).read_text() for p in part], cols=5)
        try:
            answer, result = ai.ask(image, PROMPT.format(vocabulary=_vocabulary_text()) + "\n\nNames:\n" + names, SYSTEM, workdir, model=model, effort=effort)
        except ai.QuotaExceeded:
            raise
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
            log(f"  batch at {start} failed: {str(e)[:200]}")
            continue
        run_id = ai._log_run(conn, "describe", result)
        stats["cost_usd"] = round(stats["cost_usd"] + (result.get("total_cost_usd") or 0), 4)
        for i, p in enumerate(part, 1):
            a = answer.get(str(i)) if isinstance(answer, dict) else None
            if isinstance(a, dict):
                _store(conn, p, a, name, run_id, stats)
        _refresh_counts(conn)
        conn.commit()
        log(f"  {start + len(part)}/{len(todo)} {stats}")
    return stats


def search(conn: sqlite3.Connection, query: str, limit: int = 200) -> list[sqlite3.Row]:
    """Pictograms whose text, name or description match ``query`` (full-text, each word must occur)."""
    words = [w for w in re.findall(r"\w+", query) if w]
    if not words:
        return []
    match = " AND ".join('"' + w.replace('"', "") + '"' for w in words)
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT s.pictogram_id, snippet(pictogram_search, -1, '[', ']', '…', 12) AS snippet, p.sha256, p.original_name, p.id
           FROM pictogram_search s JOIN pictogram p ON p.id = s.pictogram_id WHERE pictogram_search MATCH ? ORDER BY rank LIMIT ?""",
        (match, limit),
    ).fetchall()
