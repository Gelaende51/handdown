"""Symbols and ideas, the two top rungs of the hierarchy
(docs/superpowers/specs/2026-10-02-symbols-and-ideas-design.md).

A depiction's key is its drawn object (or the concept its names point to)
plus its leading idea, the strongest meaning that is not the object itself.
Depictions with one key form a symbol; keys with too few sources join the
object's literal symbol."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

SOURCE_RANK = {"manual": 4, "ai": 3, "name": 2, "alias": 1, "applied": 0}


def _wn(cid: str | None) -> Any:
    from nltk.corpus.reader.wordnet import WordNetError

    from .concepts import wordnet

    if not cid or not cid.startswith("wn:"):
        return None
    try:
        return wordnet().synset(cid[3:])
    except WordNetError:
        return None


def _close(a: str, b: str, steps: int = 2) -> bool:
    """Same concept, or within ``steps`` hypernym steps of each other (mug, drinking vessel)."""
    if a == b:
        return True
    sa, sb = _wn(a), _wn(b)
    if sa is None or sb is None:
        return False
    up = lambda s: {h for path in s.hypernym_paths() for h in path[-1 - steps : -1]}  # noqa: E731
    return sb in up(sa) or sa in up(sb)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def form(conn: sqlite3.Connection, min_sources: int = 3, log: Any = print) -> dict[str, int]:
    """Rebuild the rule-made symbols (Claude's and manual ones stay)."""
    labels = dict(conn.execute("SELECT id, label FROM concept"))
    for table in ("symbol_relation", "symbol_idea", "symbol_depiction"):
        conn.execute(f"DELETE FROM {table} WHERE method = 'rules'")
    conn.execute("DELETE FROM symbol WHERE method = 'rules' AND id NOT IN (SELECT symbol_id FROM symbol_depiction)")
    taken = {r[0] for r in conn.execute("SELECT depiction_id FROM symbol_depiction")}  # by Claude or by hand
    meanings: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
    for did, cid, src, conf in conn.execute("SELECT depiction_id, concept_id, source, confidence FROM meaning_link"):
        meanings[did].append((cid, src, conf or 0.0))
    sources: dict[int, set[str]] = defaultdict(set)
    for did, sid in conn.execute(
        "SELECT g.depiction_id, p.source_id FROM style_group g JOIN style_member m ON m.style_group_id = g.id JOIN pictogram p ON p.id = m.pictogram_id"
    ):
        sources[did].add(sid)
    keys: dict[int, tuple[str, str | None]] = {}
    for did, obj, name_c in conn.execute("SELECT id, object_id, name_concept_id FROM depiction"):
        base = obj or name_c
        if not base or did in taken:
            continue
        others = [m for m in meanings[did] if m[0] != base]
        lead = max(others, key=lambda m: (m[2], SOURCE_RANK.get(m[1], 0)))[0] if others else None
        keys[did] = (base, lead)
    groups: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    for did, key in keys.items():
        groups[key].append(did)
    merged: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    for (base, lead), dids in groups.items():
        n_sources = len(set().union(*(sources[d] for d in dids)))
        merged[(base, lead) if lead is None or n_sources >= min_sources else (base, None)].extend(dids)
    counts = {"symbols": 0, "depictions": 0, "ideas": 0, "relations": 0}
    size_of = dict(conn.execute("SELECT id, size FROM depiction"))
    symbol_of: dict[int, str] = {}
    lead_of: dict[str, str] = {}
    for (base, lead), dids in merged.items():
        label = labels.get(base, base.split(":", 1)[-1]) + (f" ({labels.get(lead, lead.split(':', 1)[-1])})" if lead else "")
        sid = f"sym:{_slug(label)}"
        if conn.execute("SELECT 1 FROM symbol WHERE id = ? AND method != 'rules'", (sid,)).fetchone():
            sid = f"{sid}-rules"
        srcs = set().union(*(sources[d] for d in dids))
        conn.execute(
            """INSERT INTO symbol (id, label, object_id, lead_id, method, size, source_count) VALUES (?,?,?,?, 'rules', ?, ?)
               ON CONFLICT (id) DO UPDATE SET label=excluded.label, object_id=excluded.object_id, lead_id=excluded.lead_id,
                   size=excluded.size, source_count=excluded.source_count""",
            (sid, label, base, lead, len(dids), len(srcs)),
        )
        canonical = max(dids, key=lambda d: (len(sources[d]), size_of.get(d) or 0, -d))
        conn.executemany(
            "INSERT INTO symbol_depiction (symbol_id, depiction_id, form, method) VALUES (?, ?, ?, 'rules')",
            [(sid, d, "canonical" if d == canonical else "variant") for d in dids],
        )
        # ideas: the leading idea, or for a literal symbol its own meanings
        support: dict[str, int] = defaultdict(int)
        for d in dids:
            for cid in {m[0] for m in meanings[d]}:
                support[cid] += 1
        ideas = [lead] if lead else [c for c, n in support.items() if n >= max(1, len(dids) // 3)] or [base]
        for cid in ideas:
            kind = "resemblance" if _close(cid, base) else "convention"
            conn.execute(
                "INSERT OR IGNORE INTO symbol_idea (symbol_id, concept_id, kind, method, confidence) VALUES (?,?,?, 'rules', ?)",
                (sid, cid, kind, round(support.get(cid, 0) / len(dids), 2)),
            )
            counts["ideas"] += 1
        for d in dids:
            symbol_of[d] = sid
        if lead:
            lead_of[sid] = lead
        counts["symbols"] += 1
        counts["depictions"] += len(dids)
    counts["relations"] = _relations(conn, symbol_of, lead_of)
    conn.commit()
    log(f"  {counts}")
    return counts


def _relations(conn: sqlite3.Connection, symbol_of: dict[int, str], lead_of: dict[str, str]) -> int:
    rows: set[tuple[str, str, str]] = set()
    by_lead: dict[str, list[str]] = defaultdict(list)
    for sid, lead in lead_of.items():
        by_lead[lead].append(sid)
    size = dict(conn.execute("SELECT id, size FROM symbol"))
    for sids in by_lead.values():  # same idea: each to the largest symbol of that idea (no quadratic web)
        head = max(sids, key=lambda s: size.get(s) or 0)
        rows |= {(s, head, "same idea") for s in sids if s != head}
    for lead, sids in by_lead.items():  # opposite of: WordNet antonyms of the leading ideas
        syn = _wn(lead)
        if syn is None:
            continue
        for anti in {a.synset() for lemma in syn.lemmas() for a in lemma.antonyms()}:
            for other in by_lead.get(f"wn:{anti.name()}", []):
                for s in sids:
                    if (other, s, "opposite of") not in rows:
                        rows.add((s, other, "opposite of"))
    composite_depiction = {
        pid: did
        for pid, did in conn.execute(
            "SELECT m.pictogram_id, g.depiction_id FROM style_member m JOIN style_group g ON g.id = m.style_group_id"
            " WHERE m.pictogram_id IN (SELECT pictogram_id FROM composition)"
        )
    }
    for pid, part_did in conn.execute("SELECT pictogram_id, depiction_id FROM composition_part WHERE depiction_id IS NOT NULL"):
        whole, part = symbol_of.get(composite_depiction.get(pid)), symbol_of.get(part_did)
        if whole and part and whole != part:
            rows.add((whole, part, "composed of"))
    conn.executemany("INSERT OR IGNORE INTO symbol_relation (symbol_a, symbol_b, relation, method) VALUES (?, ?, ?, 'rules')", sorted(rows))
    return len(rows)


def idea_relations(conn: sqlite3.Connection, ideas: list[str] | None = None) -> int:
    """Broader ideas among the ideas in use, from WordNet hypernyms (up to two steps)."""
    ideas = ideas if ideas is not None else [r[0] for r in conn.execute("SELECT DISTINCT concept_id FROM symbol_idea")]
    have = set(ideas)
    rows = []
    for cid in ideas:
        syn = _wn(cid)
        if syn is None:
            continue
        for path in syn.hypernym_paths():
            for h in path[-3:-1]:
                if f"wn:{h.name()}" in have and f"wn:{h.name()}" != cid:
                    rows.append((cid, f"wn:{h.name()}", "broader", "wordnet"))
    conn.execute("DELETE FROM idea_relation WHERE source = 'wordnet'")
    conn.executemany("INSERT OR IGNORE INTO idea_relation VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    return len(set(rows))
