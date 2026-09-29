"""Obsidian-compatible vault generated from the database.

Everything is regenerated except each note's ``overrides:`` and ``notes:``
frontmatter fields, which ``sync`` reads back into the database and
``export`` carries over into the regenerated note.
"""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image

from . import db
from .config import Config
from .metrics import render

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SCORE_COLS = ("combined", "legibility", "simplicity", "convention", "distinctiveness", "balance", "consistency")
MAX_VARIANTS = 40


def slug(text: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", text).strip()
    return re.sub(r"\s+", " ", s)[:100] or "untitled"


def read_frontmatter(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    m = FM.match(path.read_text())
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {"_unparseable": True}


def write_note(path: Path, front: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + body
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


class Exporter:
    def __init__(self, conn: sqlite3.Connection, cfg: Config, min_sources: int = 2):
        self.conn = conn
        self.cfg = cfg
        self.vault = cfg.vault
        self.min_sources = min_sources
        self.names: dict[str, str] = {}  # concept id -> note path (relative, no .md)

    # ---- helpers -------------------------------------------------------
    def media(self, norm_path: str | None) -> str | None:
        path = self.cfg.resolve(norm_path)
        if path is None:
            return None
        return "_media/norm/" + os.path.relpath(path, self.cfg.norm)

    def bitmap(self, pid: int, norm_path: str | None) -> str | None:
        """Pure 1-bit 16 px render, enlarged 3x with hard pixels."""
        path = self.cfg.resolve(norm_path)
        if path is None or not path.exists():
            return None
        sha = path.stem
        out = self.cfg.png / "1bit16" / sha[:2] / f"{sha}.png"
        if not out.exists():
            a = render(path.read_text(), 16)
            img = Image.fromarray(np.where(a > 0.5, 0, 255).astype(np.uint8)).resize((48, 48), Image.Resampling.NEAREST)
            out.parent.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            img.convert("1").save(buf, "PNG")
            out.write_bytes(buf.getvalue())
        return "_media/png/" + os.path.relpath(out, self.cfg.png)

    def scores(self, pids: list[int]) -> dict[int, dict[str, float]]:
        out: dict[int, dict[str, float]] = defaultdict(dict)
        for i in range(0, len(pids), 900):
            part = pids[i : i + 900]
            q = f"SELECT pictogram_id, metric, value FROM rating WHERE value IS NOT NULL AND pictogram_id IN ({','.join('?' * len(part))}) ORDER BY is_override"
            for pid, metric, value in self.conn.execute(q, part):
                out[pid][metric] = value
        return out

    # ---- export --------------------------------------------------------
    def concepts(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT k.*, COUNT(DISTINCT p.source_id) AS n_src, COUNT(DISTINCT p.id) AS n
               FROM concept k JOIN depiction_cluster c ON c.concept_id = k.id
               JOIN cluster_member m ON m.cluster_id = c.id JOIN pictogram p ON p.id = m.pictogram_id
               GROUP BY k.id HAVING n_src >= ? ORDER BY n_src DESC""",
            (self.min_sources,),
        ).fetchall()

    def plan_names(self, concepts: list[sqlite3.Row]) -> None:
        domains = dict(
            self.conn.execute(
                """SELECT c.concept_id, s.domain FROM depiction_cluster c JOIN cluster_member m ON m.cluster_id=c.id
               JOIN pictogram p ON p.id=m.pictogram_id JOIN source s ON s.id=p.source_id
               GROUP BY c.concept_id, s.domain ORDER BY COUNT(*)"""
            ).fetchall()
        )  # last (most frequent) domain wins
        used: set[str] = set()
        for k in concepts:
            dom = domains.get(k["id"]) or "misc"
            base = slug(k["label"])
            name = f"concepts/{dom}/{base}"
            if name in used:
                name = f"concepts/{dom}/{base} ({slug(k['wordnet_synset'] or k['id'])})"
            used.add(name)
            self.names[k["id"]] = name

    def concept_note(self, k: sqlite3.Row) -> None:
        path = self.vault / f"{self.names[k['id']]}.md"
        old = read_frontmatter(path)
        clusters = self.conn.execute(
            """SELECT c.* FROM depiction_cluster c
               WHERE c.concept_id=? ORDER BY c.source_count DESC, c.size DESC""",
            (k["id"],),
        ).fetchall()
        labels = json.loads(k["labels"] or "{}")
        front: dict[str, Any] = {
            "concept": k["id"],
            "label": k["label"],
            "wordnet": k["wordnet_synset"],
            "wikidata": k["wikidata_qid"],
            "referent_type": k["referent_type"],
            "parent": k["parent_id"],
            "labels": labels,
            "pictograms": k["n"],
            "sources": k["n_src"],
            "depictions": len(clusters),
            "overrides": old.get("overrides") or {},
            "notes": old.get("notes") or "",
        }
        lines = [f"# {k['label']}", ""]
        if k["definition"]:
            lines += [f"> {k['definition']}", ""]
        if k["parent_id"] and k["parent_id"] in self.names:
            lines += [f"Broader: [[{self.names[k['parent_id']]}|{k['parent_id']}]]", ""]
        members = defaultdict(list)
        all_pids: list[int] = []
        for c in clusters:
            rows = self.conn.execute(
                """SELECT p.id, p.original_name, p.original_url, p.norm_path, p.style, p.color_class, p.source_id,
                          s.name AS sname, s.license_spdx FROM cluster_member m JOIN pictogram p ON p.id=m.pictogram_id
                   JOIN source s ON s.id=p.source_id WHERE m.cluster_id=?""",
                (c["id"],),
            ).fetchall()
            members[c["id"]] = rows
            all_pids += [r["id"] for r in rows]
        sc = self.scores(all_pids)
        for n, c in enumerate(clusters, 1):
            rows = sorted(members[c["id"]], key=lambda r: -sc[r["id"]].get("combined", 0))
            rep = next((r for r in rows if r["id"] == c["representative_id"]), rows[0])
            desc = c["description"] or f"depiction {n}"
            lines += [f"## {n}. {desc}", ""]
            lines += [
                f"Convention: **{c['convention_strength']:.0f}%** of sources for this meaning "
                f"({c['source_count']} sources, {c['size']} pictograms) · representative `#{rep['id']}` · "
                f"override key `cluster-rep:{rep['id']}`",
                "",
            ]
            m = self.media(rep["norm_path"])
            bit = self.bitmap(rep["id"], rep["norm_path"])
            if m:
                sheet = " ".join(f"![[{m}|{s}]]" for s in (12, 16, 24, 48))
                lines += [sheet + (f"  1-bit 16 px: ![[{bit}|48]]" if bit else ""), ""]
            pairs = self.conn.execute("SELECT concept_b, strength FROM confusion_pair WHERE cluster_id=?", (c["id"],)).fetchall()
            if pairs:
                links = ", ".join(f"[[{self.names[b]}|{b}]]" if b in self.names else b for b, _ in pairs)
                lines += [f"Looks like: {links}", ""]
            lines += ["| | name | source | license | style | " + " | ".join(SCORE_COLS) + " |", "|---|---|---|---|---|" + "---|" * len(SCORE_COLS)]
            for r in rows[:MAX_VARIANTS]:
                img = self.media(r["norm_path"])
                s = sc[r["id"]]
                cells = " | ".join(f"{s[x]:.0f}" if x in s else "–" for x in SCORE_COLS)
                src = f"[[sources/{slug(r['source_id'])}\\|{r['sname']}]]"
                name = f"[{r['original_name']}]({r['original_url']})" if r["original_url"] else r["original_name"]
                lines.append(f"| ![[{img}\\|24]] | {name} | {src} | {r['license_spdx'] or '?'} | {r['style'] or ''} | {cells} |")
            if len(rows) > MAX_VARIANTS:
                lines.append(f"\n…and {len(rows) - MAX_VARIANTS} more variants (see HTML export).")
            lines.append("")
        write_note(path, front, "\n".join(lines))

    def source_notes(self) -> None:
        for s in self.conn.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM pictogram p WHERE p.source_id=s.id) AS n,
                      (SELECT COUNT(*) FROM harvest_error e WHERE e.source_id=s.id) AS errors
               FROM source s"""
        ):
            path = self.vault / "sources" / f"{slug(s['id'])}.md"
            old = read_frontmatter(path)
            front = {
                k: s[k]
                for k in (
                    "id",
                    "name",
                    "platform_id",
                    "url",
                    "license_spdx",
                    "license_url",
                    "author",
                    "version",
                    "domain",
                    "category",
                    "grid_size",
                    "harvest_status",
                    "found_via",
                    "accessed_at",
                    "adapter",
                )
            }
            front.update({"pictograms": s["n"], "errors": s["errors"], "overrides": old.get("overrides") or {}, "notes": old.get("notes") or ""})
            body = [f"# {s['name']}", "", f"Platform: [[platforms/{slug(s['platform_id'] or 'unknown')}]]", ""]
            if s["url"]:
                body += [f"Home: <{s['url']}>", ""]
            if s["notes"]:
                body += [s["notes"], ""]
            stats = self.conn.execute("SELECT color_class, COUNT(*) FROM pictogram WHERE source_id=? GROUP BY 1", (s["id"],)).fetchall()
            if stats:
                body += ["| color class | count |", "|---|---|"] + [f"| {a} | {b} |" for a, b in stats] + [""]
            write_note(path, front, "\n".join(body))

    def platform_notes(self) -> None:
        for p in self.conn.execute("SELECT * FROM platform"):
            path = self.vault / "platforms" / f"{slug(p['id'])}.md"
            old = read_frontmatter(path)
            front = {k: p[k] for k in p.keys()}
            front.update({"overrides": old.get("overrides") or {}, "notes": old.get("notes") or ""})
            srcs = self.conn.execute("SELECT id, name, harvest_status FROM source WHERE platform_id=? ORDER BY name", (p["id"],)).fetchall()
            searches = self.conn.execute(
                "SELECT query, engine, run_at, candidates_found FROM search_log WHERE seed=? ORDER BY run_at", (f"platform:{p['id']}",)
            ).fetchall()
            body = [f"# {p['name']}", "", f"<{p['url']}>", "", f"## Sources ({len(srcs)})", ""]
            body += [f"- [[sources/{slug(s['id'])}|{s['name']}]] — {s['harvest_status']}" for s in srcs]
            if searches:
                body += ["", "## Searches seeded from here", ""]
                body += [f"- `{q['query']}` ({q['engine']}, {q['run_at'][:10]}): {q['candidates_found']} candidates" for q in searches]
            write_note(path, front, "\n".join(body) + "\n")

    def indexes(self, concepts: list[sqlite3.Row]) -> None:
        q = lambda sql, *a: self.conn.execute(sql, a).fetchall()  # noqa: E731
        stats = [
            "# Catalog statistics",
            "",
            f"- platforms: {q('SELECT COUNT(*) FROM platform')[0][0]}",
            f"- sources: {q('SELECT COUNT(*) FROM source')[0][0]}",
            f"- pictograms: {q('SELECT COUNT(*) FROM pictogram')[0][0]}",
            f"- unique (after dedupe): {q('SELECT COUNT(*) FROM pictogram WHERE duplicate_of IS NULL AND sha256 IS NOT NULL')[0][0]}",
            f"- concepts: {q('SELECT COUNT(*) FROM concept')[0][0]} (in vault: {len(concepts)}, ≥{self.min_sources} sources)",
            f"- depiction clusters: {q('SELECT COUNT(*) FROM depiction_cluster')[0][0]}",
            "",
            "## Colour classes",
            "",
        ]
        stats += [f"- {a}: {b}" for a, b in q("SELECT color_class, COUNT(*) FROM pictogram GROUP BY 1")]
        stats += ["", "## Licenses", ""]
        stats += [f"- {a}: {b} sources" for a, b in q("SELECT license_spdx, COUNT(*) FROM source GROUP BY 1 ORDER BY 2 DESC")]
        write_note(self.vault / "_index" / "stats.md", {"generated": db.now()}, "\n".join(stats) + "\n")

        top = [
            "# Most established meanings",
            "",
            "Concepts drawn by the most independent sources.",
            "",
            "| concept | sources | pictograms | depictions |",
            "|---|---|---|---|",
        ]
        for k in concepts[:500]:
            nd = q("SELECT COUNT(*) FROM depiction_cluster WHERE concept_id=?", k["id"])[0][0]
            top.append(f"| [[{self.names[k['id']]}\\|{k['label']}]] | {k['n_src']} | {k['n']} | {nd} |")
        write_note(self.vault / "_index" / "top-concepts.md", {"generated": db.now()}, "\n".join(top) + "\n")

        conf = ["# Confusable depictions", "", "Representatives whose shape is nearly identical to an unrelated concept.", ""]
        for a, b, s in q("SELECT concept_a, concept_b, strength FROM confusion_pair ORDER BY strength DESC LIMIT 1000"):
            la = f"[[{self.names[a]}|{a}]]" if a in self.names else a
            lb = f"[[{self.names[b]}|{b}]]" if b in self.names else b
            conf.append(f"- {la} ↔ {lb} ({s:.2f})")
        write_note(self.vault / "_index" / "confusions.md", {"generated": db.now()}, "\n".join(conf) + "\n")

        tri = ["# Source candidates", "", "| id | name | status | found via | url |", "|---|---|---|---|---|"]
        for r in q("SELECT id, name, harvest_status, found_via, url FROM source WHERE harvest_status NOT IN ('harvested') ORDER BY harvest_status, id"):
            tri.append(f"| [[sources/{slug(r[0])}\\|{r[0]}]] | {r[1]} | {r[2]} | {r[3] or ''} | {r[4] or ''} |")
        write_note(self.vault / "_index" / "triage.md", {"generated": db.now()}, "\n".join(tri) + "\n")

        errs = ["# Errors", "", "| source | stage | count | example |", "|---|---|---|---|"]
        for s, st, n, e in q("SELECT source_id, stage, COUNT(*), MIN(error) FROM harvest_error GROUP BY 1, 2 ORDER BY 3 DESC"):
            errs.append(f"| {s} | {st} | {n} | {e[:120].replace('|', '/')} |")
        write_note(self.vault / "_index" / "errors.md", {"generated": db.now()}, "\n".join(errs) + "\n")

    def run(self, log: Any = print) -> int:
        self.vault.mkdir(parents=True, exist_ok=True)
        media = self.vault / "_media"
        media.mkdir(exist_ok=True)
        for name, target in (("norm", self.cfg.norm), ("png", self.cfg.png)):
            link = media / name
            target.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                link.symlink_to(os.path.relpath(target, media))
        concepts = self.concepts()
        self.plan_names(concepts)
        expected = {f"{n}.md" for n in self.names.values()}
        existing = {str(p.relative_to(self.vault)) for p in (self.vault / "concepts").rglob("*.md")} if (self.vault / "concepts").exists() else set()
        for i, k in enumerate(concepts):
            self.concept_note(k)
            if i % 1000 == 0:
                log(f"  concept notes: {i}/{len(concepts)}")
        # Stale notes: report those holding hand-written content, remove the rest.
        kept = []
        for rel in sorted(existing - expected):
            fm = read_frontmatter(self.vault / rel)
            if fm.get("overrides") or fm.get("notes"):
                kept.append(rel)
            else:
                (self.vault / rel).unlink()
        if kept:
            log(f"  {len(kept)} notes no longer generated but hold overrides/notes; left in place:")
            for rel in kept[:50]:
                log(f"    {rel}")
        self.source_notes()
        self.platform_notes()
        self.indexes(concepts)
        return len(concepts)


def sync(conn: sqlite3.Connection, cfg: Config, log: Any = print) -> int:
    """Read ``overrides:`` and ``notes:`` from all notes into the database.

    Override formats (in a concept note):
      overrides:
        label: "rubbish bin"                  # concept field
        cluster-rep:12345: {description: trash can, meaning: 80}
        pictogram:678: {concept: wn:delete.v.01, combined: 95}
    """
    n = 0
    now = db.now()
    seen: set[tuple[str, str]] = set()
    for path in sorted(cfg.vault.rglob("*.md")):
        if "_media" in path.parts:
            continue
        fm = read_frontmatter(path)
        if fm.get("_unparseable"):
            log(f"  unparseable frontmatter: {path.relative_to(cfg.vault)}")
            continue
        owner = (fm.get("concept") and f"concept:{fm['concept']}") or (fm.get("id") and f"source:{fm['id']}")
        if not owner:
            continue
        if fm.get("notes"):
            conn.execute("INSERT OR REPLACE INTO override VALUES (?,?,?,?,?)", (owner, "notes", str(fm["notes"]), None, now))
            seen.add((owner, "notes"))
        for key, val in (fm.get("overrides") or {}).items():
            if isinstance(val, dict):
                for field, v in val.items():
                    conn.execute("INSERT OR REPLACE INTO override VALUES (?,?,?,?,?)", (str(key), str(field), json.dumps(v), None, now))
                    seen.add((str(key), str(field)))
                    n += 1
            else:
                conn.execute("INSERT OR REPLACE INTO override VALUES (?,?,?,?,?)", (owner, str(key), json.dumps(val), None, now))
                seen.add((owner, str(key)))
                n += 1
    # Overrides removed from the vault are removed here too.
    for target, field in conn.execute("SELECT target, field FROM override").fetchall():
        if (target, field) not in seen:
            conn.execute("DELETE FROM override WHERE target=? AND field=?", (target, field))
    apply_overrides(conn)
    conn.commit()
    return n


RATING_FIELDS = {"meaning", "depiction", "familiarity", "convention", "distinctiveness", "legibility", "simplicity", "balance", "consistency", "combined"}


def apply_overrides(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM rating WHERE is_override=1")
    conn.execute("DELETE FROM pictogram_concept WHERE method='manual'")
    now = db.now()
    for target, field, raw in conn.execute("SELECT target, field, value FROM override").fetchall():
        try:
            value = json.loads(raw) if raw is not None else None
        except json.JSONDecodeError:
            value = raw
        kind, _, ident = target.partition(":")
        if kind == "concept" and field in ("label", "referent_type", "domain", "wikidata_qid"):
            conn.execute(f"UPDATE concept SET {field}=? WHERE id=?", (value, ident))
        elif kind == "cluster-rep":
            cl = conn.execute("SELECT cluster_id FROM cluster_member WHERE pictogram_id=?", (int(ident),)).fetchone()
            if not cl:
                continue
            if field == "description":
                conn.execute("UPDATE depiction_cluster SET description=? WHERE id=?", (value, cl[0]))
            elif field in RATING_FIELDS:
                conn.execute(
                    """INSERT OR REPLACE INTO rating (pictogram_id, metric, value, method, computed_at, is_override)
                       SELECT pictogram_id, ?, ?, 'manual', ?, 1 FROM cluster_member WHERE cluster_id=?""",
                    (field, float(value), now, cl[0]),
                )
        elif kind == "pictogram":
            pid = int(ident)
            if field == "concept":
                conn.execute("INSERT OR REPLACE INTO pictogram_concept VALUES (?,?,1.0,'manual')", (pid, value))
            elif field in RATING_FIELDS:
                conn.execute(
                    "INSERT OR REPLACE INTO rating (pictogram_id, metric, value, method, computed_at, is_override) VALUES (?,?,?,'manual',?,1)",
                    (pid, field, float(value), now),
                )
