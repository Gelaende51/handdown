"""AI passes over depiction clusters, run as headless ``claude -p`` jobs.

Two calls per batch of clusters:

1. **blind**: a numbered contact sheet (each cluster's representative at
   48 px and 16 px) without any names. The model says what each cell depicts
   and what it could mean. This measures recognizability without priming.
2. **informed**: the same sheet plus the true concept and the blind answers.
   The model grades the blind answers (meaning, depiction), names the
   depiction, and records semantic metadata (concreteness, semantic distance,
   familiarity, cultural risk, timelessness, anachronism, representation).

Only ``meaning``, ``depiction`` and ``familiarity`` become ratings; the rest
is metadata (``semantic_meta``), as the spec requires.

The dev container cannot see the account's usage limits, so runs are
explicit and bounded (``--limit``) and every run is logged in ``ai_run``
with its token usage.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import db
from .config import Config
from .metrics import render

MODEL = "sonnet"
BATCH = 12
CELL = 120
METHOD_VERSION = "1"

BLIND_PROMPT = """Each numbered cell shows one pictogram, large and at 16 px.
Without guessing from context, say for each cell:
- "depicts": what object/figure/shape is drawn (a few words)
- "meanings": up to 3 things it could mean as a sign or UI icon, most likely first
Reply with JSON only: {"1": {"depicts": "...", "meanings": ["..."]}, "2": ...}"""

INFORMED_PROMPT = """Each numbered cell shows one pictogram. For each cell you get the intended
meaning and a blind viewer's guesses (made without knowing the meaning).
Grade and describe each cell. Reply with JSON only, keyed by cell number:
{"1": {
  "meaning": 0-100,        // how well the blind guesses match the intended meaning; conventional uses of
                           // the drawn object count as a match (a star read as "favorite" matches "star")
  "depiction": 0-100,      // how well the blind "depicts" matches what is actually drawn
  "description": "...",    // short name of what is drawn, e.g. "trash can with lid"
  "familiarity": 0-100,    // how commonly this drawing is used for this meaning
  "concreteness": 1-5,     // 1 abstract .. 5 concrete object
  "semantic_distance": 1-5,// 1 drawing is the meaning .. 5 many interpretive steps
  "representation": "iconic|indexical|symbolic|metonymic",
  "metaphor_chain": "...", // e.g. "floppy disk -> storage -> save", or ""
  "alternatives": ["..."], // other plausible meanings (ambiguity)
  "cultural_risk": "...",  // regions/cultures where it may be misread, or ""
  "timelessness": 1-5,     // 1 soon obsolete .. 5 timeless
  "anachronism": true|false// depicted object is obsolete
}, ...}"""


class QuotaExceeded(RuntimeError):
    """The account's usage limit was reached; stop and resume after the reset."""


QUOTA_MARKERS = ("hit your session limit", "hit your usage limit", "usage limit reached", "rate limit")
HIERARCHY_BATCH = 24  # cells per call: the instructions and overhead are paid once per 24 pictograms


def sheet(svgs: list[str], cols: int = 4) -> bytes:
    rows = (len(svgs) + cols - 1) // cols
    img = Image.new("L", (cols * CELL, rows * CELL), 255)
    d = ImageDraw.Draw(img)
    for i, svg in enumerate(svgs):
        x, y = (i % cols) * CELL, (i // cols) * CELL
        big = render(svg, 72)
        small = render(svg, 16)
        img.paste(Image.fromarray(((1 - big) * 255).astype(np.uint8)), (x + 8, y + 24))
        img.paste(Image.fromarray(((1 - (small > 0.5)) * 255).astype(np.uint8)), (x + 92, y + 80))
        d.text((x + 4, y + 4), str(i + 1), fill=0)
        d.rectangle((x, y, x + CELL - 1, y + CELL - 1), outline=200)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def ask(image: bytes, text: str, system: str, workdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": text},
            ],
        },
    }
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "--model",
            MODEL,
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--system-prompt",
            system,
        ],
        input=json.dumps(msg) + "\n",
        capture_output=True,
        text=True,
        timeout=600,
        cwd=workdir,
        env=env,
    )
    result = None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
    if result is None or result.get("is_error"):
        message = f"{proc.stderr[-500:]} {(result or {}).get('result') or ''}"
        if any(m in message.lower() for m in QUOTA_MARKERS):
            raise QuotaExceeded(message.strip())
        raise RuntimeError(f"claude -p failed: {message.strip()}")
    text_out = result.get("result") or ""
    m = re.search(r"\{.*\}", text_out, re.S)
    if not m:
        raise ValueError(f"no JSON in answer: {text_out[:200]}")
    return json.loads(m.group(0)), result


def _log_run(conn: sqlite3.Connection, job: str, result: dict[str, Any]) -> str:
    u = result.get("usage") or {}
    rid = result.get("session_id") or uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO ai_run VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            job,
            MODEL,
            u.get("input_tokens"),
            u.get("output_tokens"),
            u.get("cache_read_input_tokens"),
            u.get("cache_creation_input_tokens"),
            result.get("total_cost_usd"),
            None,
            db.now(),
        ),
    )
    return rid


def pending_clusters(conn: sqlite3.Connection, limit: int, min_sources: int) -> list[sqlite3.Row]:
    """Clusters of the most established concepts first, not yet assessed."""
    return conn.execute(
        """SELECT c.id, c.concept_id, k.label, k.definition, p.norm_path
           FROM depiction_cluster c JOIN concept k ON k.id = c.concept_id
           JOIN pictogram p ON p.id = c.representative_id
           LEFT JOIN semantic_meta s ON s.cluster_id = c.id
           WHERE s.cluster_id IS NULL AND c.source_count >= ?
           ORDER BY (SELECT COUNT(DISTINCT source_id) FROM pictogram_concept pc JOIN pictogram pp ON pp.id = pc.pictogram_id
                     WHERE pc.concept_id = c.concept_id) DESC, c.source_count DESC
           LIMIT ?""",
        (min_sources, limit),
    ).fetchall()


def run(conn: sqlite3.Connection, workdir: Path, limit: int = 48, min_sources: int = 2, log: Any = print) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    clusters = pending_clusters(conn, limit, min_sources)
    done = 0
    cost = 0.0
    for start in range(0, len(clusters), BATCH):
        batch = clusters[start : start + BATCH]
        svgs = [Config().resolve(c["norm_path"]).read_text() for c in batch]  # type: ignore[union-attr]
        img = sheet(svgs)
        try:
            blind, r1 = ask(img, BLIND_PROMPT, "You look at pictograms and report what you see. Reply with JSON only.", workdir)
            rid1 = _log_run(conn, "blind", r1)
            lines = []
            for i, c in enumerate(batch, 1):
                b = blind.get(str(i), {})
                lines.append(
                    f'{i}. intended meaning: "{c["label"]}"'
                    + (f" ({c['definition']})" if c["definition"] else "")
                    + f' | blind viewer: depicts "{b.get("depicts", "?")}", meanings {json.dumps(b.get("meanings", []))}'
                )
            informed, r2 = ask(img, INFORMED_PROMPT + "\n\n" + "\n".join(lines), "You are a pictogram design researcher. Reply with JSON only.", workdir)
            rid2 = _log_run(conn, "informed", r2)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log(f"  batch {start // BATCH + 1}: {e}")
            continue
        cost += (r1.get("total_cost_usd") or 0) + (r2.get("total_cost_usd") or 0)
        now = db.now()
        for i, c in enumerate(batch, 1):
            a = informed.get(str(i))
            if not isinstance(a, dict):
                continue
            b = blind.get(str(i), {})
            conn.execute(
                "UPDATE depiction_cluster SET description=COALESCE(description, ?), representation=?, metaphor_chain=? WHERE id=?",
                (a.get("description"), a.get("representation"), a.get("metaphor_chain") or None, c["id"]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO semantic_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c["id"],
                    a.get("concreteness"),
                    a.get("semantic_distance"),
                    len(a.get("alternatives") or []),
                    json.dumps({"blind": b, "alternatives": a.get("alternatives") or []}, ensure_ascii=False),
                    json.dumps(a.get("cultural_risk") or "", ensure_ascii=False),
                    a.get("timelessness"),
                    int(bool(a.get("anachronism"))),
                    MODEL,
                    f"{rid1},{rid2}",
                    now,
                ),
            )
            members = [r[0] for r in conn.execute("SELECT pictogram_id FROM cluster_member WHERE cluster_id=?", (c["id"],))]
            for metric in ("meaning", "depiction", "familiarity"):
                v = a.get(metric)
                if isinstance(v, (int, float)):
                    conn.executemany(
                        """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, detail, method, method_version, model, run_id, computed_at, is_override)
                           VALUES (?,?,?,?,?,?,?,?,?,0)""",
                        [
                            (
                                pid,
                                metric,
                                float(v),
                                json.dumps({"cluster": c["id"]}),
                                "ai-blind" if metric != "familiarity" else "ai",
                                METHOD_VERSION,
                                MODEL,
                                rid2,
                                now,
                            )
                            for pid in members
                        ],
                    )
            done += 1
        conn.commit()
        log(f"  batch {start // BATCH + 1}: {done} clusters assessed, ${cost:.3f} so far")
    return {"clusters": done, "cost_usd": round(cost, 4)}


COMPOSITION_PROMPT = """Each numbered cell shows one pictogram; its file name is listed below.
Decompose each into parts. Roles: base, negation, frame, modifier, repetition, partner, text, decoration.
Relations between part indexes: above, below, left_of, right_of, over, under, touching, merged, cutout,
surrounds, crossing, corner:tl|tr|bl|br, sequence.
kind: generic (standard parts joined by a standard operator, separable) or unique (fused or artistic).
font_type, as a font would implement it:
  mark     = only a negation (slash/cross-out) or an enclosing frame applied to a base, like a combining
             character that works over any base (U+20E0, U+20DD, U+0338);
  ligature = base + a specific small modifier (plus, check, clock, lock, ...) or a repetition of the base;
  sequence = two or more independent elements that could be written one after the other;
  unique   = parts fused so that they cannot be separated.
fit: glyph (works at 16 px) | degrades (too many/small parts or text) | contradictory (e.g. double negation,
check + x, two sign frames) | sequence (better written as separate glyphs).
Reply with JSON only: {"1": {"parts": [{"role": "...", "label": "..."}], "relations": [[0, 1, "..."]],
"kind": "...", "font_type": "...", "fit": "..."}, ...}"""


def run_composition(conn: sqlite3.Connection, workdir: Path, limit: int = 48, sample: int = 0, log: Any = print) -> dict[str, Any]:
    """AI check of name/shape conflicts first, then a random sample of
    rule-classified composites (to measure rule precision)."""
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    rows = conn.execute(
        """SELECT c.pictogram_id, p.original_name, p.norm_path FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
           WHERE c.method = 'rules' AND c.conflict = 1 LIMIT ?""",
        (limit,),
    ).fetchall()
    if sample:
        rows += conn.execute(
            """SELECT c.pictogram_id, p.original_name, p.norm_path FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
               WHERE c.method = 'rules' AND c.conflict = 0 ORDER BY random() LIMIT ?""",
            (sample,),
        ).fetchall()
    done = 0
    cost = 0.0
    now = db.now()
    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]
        img = sheet([cfg.resolve(r["norm_path"]).read_text() for r in batch])  # type: ignore[union-attr]
        names = "\n".join(f"{i}. {r['original_name']}" for i, r in enumerate(batch, 1))
        try:
            answer, result = ask(img, COMPOSITION_PROMPT + "\n\n" + names, "You analyse pictogram composition. Reply with JSON only.", workdir)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log(f"  batch {start // BATCH + 1}: {e}")
            continue
        rid = _log_run(conn, "composition", result)
        cost += result.get("total_cost_usd") or 0
        for i, r in enumerate(batch, 1):
            a = answer.get(str(i))
            parts = a.get("parts") if isinstance(a, dict) else None
            if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts) or not parts:
                continue
            pid = r["pictogram_id"]
            conn.execute("DELETE FROM composition_part WHERE pictogram_id=?", (pid,))
            conn.execute("DELETE FROM composition_relation WHERE pictogram_id=?", (pid,))
            conn.execute(
                "UPDATE composition SET kind=?, font_type=?, fit=?, conflict=0, confidence=0.8, method='ai', computed_at=? WHERE pictogram_id=?",
                (a.get("kind"), a.get("font_type"), a.get("fit"), now, pid),
            )
            for no, part in enumerate(parts):
                conn.execute(
                    "INSERT INTO composition_part (pictogram_id, part_no, role, label) VALUES (?,?,?,?)",
                    (pid, no, str(part.get("role")), part.get("label")),
                )
            for rel in a.get("relations") or []:
                if isinstance(rel, list) and len(rel) == 3 and all(isinstance(x, (int, str)) for x in rel):
                    try:
                        conn.execute("INSERT OR REPLACE INTO composition_relation VALUES (?,?,?,?)", (pid, int(rel[0]), int(rel[1]), str(rel[2])))
                    except ValueError:
                        continue
            done += 1
        conn.commit()
        log(f"  batch {start // BATCH + 1}: {done} assessed, ${cost:.3f}, run {rid}")
    return {"assessed": done, "cost_usd": round(cost, 4)}


HIERARCHY_PROMPT = """Each numbered cell shows one pictogram (large and at 16 px). For each cell say:
- "object": the main thing drawn, as its plain name in 1-2 words ("mug", "floppy disk", "arrow", "shield");
  frames, badges, arrows or other added elements go into "features", not into the object
- "view": one of front, side, top, bottom, three-quarter, isometric, partial, full, unknown
- "features": visible details that are PRESENT, as short nouns ("steam", "saucer", "lid"); say whether
  they are there, not how they are drawn
- "meanings": up to 3 things it could mean as a sign or UI icon
Reply with JSON only: {"1": {"object": "...", "view": "...", "features": ["..."], "meanings": ["..."]}, ...}"""


def run_hierarchy(
    conn: sqlite3.Connection, workdir: Path, limit: int = 48, workers: int = 1, log: Any = print, batch_size: int | None = None
) -> dict[str, Any]:
    """Blind object/view/features/meanings for style groups not yet assessed,
    established depictions first. A style group whose view or features differ
    from its depiction's moves into its own depiction (method 'ai'). Model
    calls run ``workers`` at a time; database writes stay in this thread."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .concepts import split_name

    workdir.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    seen: set[str] = {r[0] for r in conn.execute("SELECT id FROM concept")}
    rows = conn.execute(
        """SELECT g.id AS gid, g.depiction_id, p.norm_path FROM style_group g
           JOIN depiction d ON d.id = g.depiction_id JOIN pictogram p ON p.id = g.representative_id
           WHERE g.assessed_at IS NULL ORDER BY d.source_count DESC, g.source_count DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    size = batch_size or HIERARCHY_BATCH
    batches = [rows[i : i + size] for i in range(0, len(rows), size)]
    done, cost, now, quota_hit = 0, 0.0, db.now(), False

    def call(batch: list[sqlite3.Row]) -> tuple[dict[str, Any], dict[str, Any]]:
        img = sheet([cfg.resolve(r["norm_path"]).read_text() for r in batch], cols=6)  # type: ignore[union-attr]
        return ask(img, HIERARCHY_PROMPT, "You look at pictograms and report what you see. Reply with JSON only.", workdir)

    with ThreadPoolExecutor(max(1, workers)) as pool:
        futures = {pool.submit(call, b): b for b in batches}
        for n, fut in enumerate(as_completed(futures), 1):
            batch = futures[fut]
            try:
                answer, result = fut.result()
            except QuotaExceeded as e:
                log(f"  usage limit reached, stopping: {e}")
                quota_hit = True
                for f in futures:
                    f.cancel()
                break
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                log(f"  batch {n}: {e}")
                continue
            _log_run(conn, "hierarchy", result)
            cost += result.get("total_cost_usd") or 0
            for i, r in enumerate(batch, 1):
                conn.execute("UPDATE style_group SET assessed_at=? WHERE id=?", (now, r["gid"]))
                a = answer.get(str(i))
                if not isinstance(a, dict) or not isinstance(a.get("object"), str) or not split_name(a["object"]):
                    continue
                _apply_hierarchy_answer(conn, r, a, seen)
                done += 1
            conn.commit()
            if n % 10 == 0 or n == len(batches):
                log(f"  {n}/{len(batches)} batches: {done} style groups, ${cost:.3f}")
    return {"assessed": done, "cost_usd": round(cost, 4), "quota_hit": quota_hit}


def _apply_hierarchy_answer(conn: sqlite3.Connection, r: sqlite3.Row, a: dict[str, Any], seen: set[str]) -> None:
    from .concepts import _ensure, resolve, split_name
    from .hierarchy.names import VIEWS, object_head

    view = a.get("view") if a.get("view") in VIEWS else None
    features = a.get("features")
    feats = sorted({f.strip().lower() for f in features if isinstance(f, str) and f.strip()}) if isinstance(features, list) else None
    head, _extra = object_head(a["object"])
    obj_c = resolve(head or split_name(a["object"]))
    _ensure(conn, obj_c, seen)
    dep = conn.execute("SELECT * FROM depiction WHERE id=?", (r["depiction_id"],)).fetchone()
    new_view = view or dep["view"]
    new_var = json.dumps(feats) if feats is not None else dep["varieties"]
    siblings = conn.execute("SELECT COUNT(*) FROM style_group WHERE depiction_id=?", (dep["id"],)).fetchone()[0]
    if (new_view, new_var) != (dep["view"], dep["varieties"]) and siblings > 1:
        target = conn.execute(
            """INSERT INTO depiction (name_concept_id, object_id, view, varieties, description, method, representative_id, size, source_count)
               SELECT name_concept_id, ?, ?, ?, ?, 'ai', representative_id, size, source_count FROM depiction WHERE id=? RETURNING id""",
            (obj_c.id, new_view, new_var, a["object"], dep["id"]),
        ).fetchone()[0]
        conn.execute("UPDATE style_group SET depiction_id=? WHERE id=?", (target, r["gid"]))
    else:
        target = dep["id"]
        conn.execute(
            "UPDATE depiction SET object_id=?, view=?, varieties=?, description=?, method='ai' WHERE id=?",
            (obj_c.id, new_view, new_var, a["object"], target),
        )
    meanings = a.get("meanings") if isinstance(a.get("meanings"), list) else []
    for m in meanings[:3]:
        if isinstance(m, str) and split_name(m):
            mc = resolve(split_name(m))
            _ensure(conn, mc, seen)
            conn.execute("INSERT OR IGNORE INTO meaning_link VALUES (?,?,?,?)", (target, mc.id, "ai", 0.7))
