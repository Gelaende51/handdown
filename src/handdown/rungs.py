"""The two top rungs for the exports and the review app: symbols and the ideas
they stand for, and the relations within each rung (siblings), which pages
show apart from the hierarchy (docs/superpowers/specs/2026-10-02-symbols-and-ideas-design.md)."""

from __future__ import annotations

import sqlite3

# read from the other end of a relation
INVERSE = {"composed of": "part of", "variant of": "has variant", "derived from": "source of", "broader": "narrower"}


def symbols(conn: sqlite3.Connection, min_sources: int) -> list[sqlite3.Row]:
    """Symbols that get a page."""
    return conn.execute("SELECT * FROM symbol WHERE source_count >= ? ORDER BY source_count DESC, id", (min_sources,)).fetchall()


def symbols_for_idea(conn: sqlite3.Connection, concept_id: str, limit: int = 60) -> list[sqlite3.Row]:
    """The symbols standing for an idea, with the kind of sign."""
    return conn.execute(
        """SELECT s.id, s.label, s.size, s.source_count, s.wikipedia, i.kind FROM symbol_idea i JOIN symbol s ON s.id = i.symbol_id
           WHERE i.concept_id = ? ORDER BY s.source_count DESC, s.id LIMIT ?""",
        (concept_id, limit),
    ).fetchall()


def ideas_of(conn: sqlite3.Connection, symbol_id: str) -> list[sqlite3.Row]:
    """What a symbol stands for (the rung above), strongest first."""
    return conn.execute(
        """SELECT i.concept_id, i.kind, COALESCE(k.label, i.concept_id) AS label FROM symbol_idea i
           LEFT JOIN concept k ON k.id = i.concept_id WHERE i.symbol_id = ? ORDER BY i.confidence DESC, i.concept_id""",
        (symbol_id,),
    ).fetchall()


def depictions_of(conn: sqlite3.Connection, symbol_id: str, limit: int = 80) -> list[sqlite3.Row]:
    """A symbol's depictions (the rung below), canonical form first."""
    return conn.execute(
        """SELECT d.*, sd.form FROM symbol_depiction sd JOIN depiction d ON d.id = sd.depiction_id WHERE sd.symbol_id = ?
           ORDER BY sd.form != 'canonical', sd.form, d.source_count DESC, d.id LIMIT ?""",
        (symbol_id, limit),
    ).fetchall()


def images(conn: sqlite3.Connection, depiction_id: int, n: int) -> list[sqlite3.Row]:
    """Representatives of a depiction's style groups, largest first; off-topic ones left out."""
    return conn.execute(
        """SELECT p.id, p.sha256, p.norm_path, p.original_name FROM style_group g JOIN pictogram p ON p.id = g.representative_id
           WHERE g.depiction_id = ? AND p.topic IS NULL ORDER BY g.size DESC, p.id LIMIT ?""",
        (depiction_id, n),
    ).fetchall()


def symbol_images(conn: sqlite3.Connection, symbol_id: str, n: int) -> list[sqlite3.Row]:
    """Images of a symbol's depictions, canonical form first."""
    return conn.execute(
        """SELECT p.id, p.sha256, p.norm_path, p.original_name FROM symbol_depiction sd JOIN style_group g ON g.depiction_id = sd.depiction_id
           JOIN pictogram p ON p.id = g.representative_id WHERE sd.symbol_id = ? AND p.topic IS NULL
           ORDER BY sd.form != 'canonical', g.size DESC, p.id LIMIT ?""",
        (symbol_id, n),
    ).fetchall()


def siblings(conn: sqlite3.Connection, table: str, ident: str) -> list[tuple[str, str]]:
    """(relation as read from ``ident``, other end) within one rung: ``symbol_relation`` or ``idea_relation``."""
    a, b = ("symbol_a", "symbol_b") if table == "symbol_relation" else ("concept_a", "concept_b")
    out = [(r[0], r[1]) for r in conn.execute(f"SELECT relation, {b} FROM {table} WHERE {a} = ?", (ident,))]
    out += [(INVERSE.get(r[0], r[0]), r[1]) for r in conn.execute(f"SELECT relation, {a} FROM {table} WHERE {b} = ?", (ident,))]
    return sorted(set(out))
