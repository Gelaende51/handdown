"""Map pictogram names and aliases to concepts (meanings).

Dictionary pass only: a name is split, cleaned of style/size/container
tokens and looked up in WordNet. Phrases WordNet does not know become
``term:`` concepts with the first known word as parent. An AI pass can
refine this later (method ``ai``), and vault overrides win over both.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

STYLE = {
    "outline",
    "outlined",
    "outlines",
    "filled",
    "fill",
    "solid",
    "line",
    "lines",
    "linear",
    "light",
    "thin",
    "bold",
    "duotone",
    "duo",
    "twotone",
    "sharp",
    "rounded",
    "round",
    "regular",
    "alt",
    "alternate",
    "o",
    "mini",
    "micro",
    "small",
    "sm",
    "lg",
    "xs",
    "xl",
    "tone",
    "broken",
    "bulk",
    "stroke",
    "glyph",
    "color",
    "colored",
    "flat",
    "mono",
    "monochrome",
    "fluent",
    "ic",
    "icon",
    "symbol",
    "v1",
    "v2",
    "v3",
    "variant",
    "outline2",
    "sharp2",
    "baseline",
    "normal",
}
CONTAINER = {"circle", "square", "box", "rounded", "badge", "rect", "rectangle", "shield", "octagon", "triangle"}
SIZES = {"8", "10", "12", "14", "16", "18", "20", "24", "28", "32", "36", "40", "48", "64", "96", "128"}
# WordNet's most frequent sense is wrong for some pictogram vocabulary. No
# general rule fixes this ("prefer artifacts" breaks arrow = direction mark),
# so the exceptions are explicit.
SENSE_OVERRIDES = {
    "folder": "folder.n.02",  # file folder, not booklet
    "mouse": "mouse.n.04",  # computer mouse
    "cart": "handcart.n.01",  # shopping cart, not horse cart
    "star": "star.n.05",  # plane figure with 5+ points, not the celestial body
}
NUMERIC_CONTEXT = {"number", "digit", "numeric", "hour", "hours", "calendar", "day", "num", "counter"}
NEGATION = {"off", "slash", "disabled", "no", "not", "crossed", "none", "forbidden", "block", "ban"}
LEXNAME_REFERENT = {
    "noun.artifact": "object",
    "noun.object": "object",
    "noun.animal": "object",
    "noun.plant": "object",
    "noun.food": "object",
    "noun.body": "object",
    "noun.person": "object",
    "noun.substance": "object",
    "noun.location": "place",
    "noun.act": "action",
    "noun.event": "action",
    "noun.process": "action",
    "noun.state": "state",
    "noun.feeling": "state",
    "noun.phenomenon": "state",
}
LANGS = (
    "fra",
    "spa",
    "ita",
    "por",
    "nld",
    "pol",
    "fin",
    "jpn",
    "cmn",
    "ind",
    "zsm",
    "tha",
    "swe",
    "dan",
    "nno",
    "nob",
    "cat",
    "eus",
    "glg",
    "ell",
    "slv",
    "hrv",
    "bul",
    "ron",
    "lit",
    "slk",
    "isl",
    "als",
    "arb",
    "heb",
)
METHOD_VERSION = "1"


def split_name(name: str) -> list[str]:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [t.lower() for t in re.split(r"[\s\-_:./+]+", name) if t]


def clean_tokens(tokens: list[str]) -> list[str]:
    out = [t for t in tokens if t not in STYLE]
    # Trailing sizes ("delete-24") go, unless the number is the content ("number-24").
    while len(out) > 1 and out[-1] in SIZES and out[-2] not in NUMERIC_CONTEXT:
        out.pop()
    content = [t for t in out if t not in CONTAINER]
    return content if content else out


def negation(tokens: list[str]) -> str:
    return "slash" if any(t in NEGATION for t in tokens) else "none"


@dataclass
class Concept:
    id: str
    label: str
    wordnet_synset: str | None = None
    parent_id: str | None = None
    referent_type: str | None = None
    definition: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def wordnet() -> Any:
    import nltk
    from nltk.corpus import wordnet as wn

    for base in {Path(os.environ.get("HANDDOWN_ROOT", ".")), Path.cwd()}:
        root = str(base / "data" / "nltk")
        if root not in nltk.data.path:
            nltk.data.path.insert(0, root)
    wn.ensure_loaded()
    return wn


def _synset(phrase: str, wn: Any) -> Any:
    syns = wn.synsets(phrase.replace(" ", "_"))
    if not syns:
        return None
    # Prefer an exact lemma match, nouns before verbs (pictograms mostly show things).
    exact = [s for s in syns if phrase.replace(" ", "_") in (name.lower() for name in s.lemma_names())]
    pool = exact or syns
    if phrase in SENSE_OVERRIDES:
        return wn.synset(SENSE_OVERRIDES[phrase])
    for pos in ("n", "v", "a", "s", "r"):
        for s in pool:
            if s.pos() == pos:
                return s
    return pool[0]


def _referent(s: Any) -> str:
    if s.pos() == "v":
        return "action"
    if s.pos() in ("a", "s", "r"):
        return "state"
    return LEXNAME_REFERENT.get(s.lexname(), "abstract")


def _concept_from_synset(s: Any, phrase: str | None = None) -> Concept:
    # Label with the word the pictograms used when it names this synset
    # ("plus", not WordNet's first lemma "asset").
    lemmas = [n.lower() for n in s.lemma_names()]
    en = phrase if phrase and phrase.replace(" ", "_") in lemmas else s.lemma_names()[0].replace("_", " ")
    labels = {"en": en}
    for lang in LANGS:
        try:
            names = s.lemma_names(lang)
        except Exception:
            continue
        if names:
            labels[lang[:2] if lang not in ("cmn", "zsm", "nno", "nob", "als", "arb") else lang] = names[0].replace("_", " ")
    return Concept(
        id=f"wn:{s.name()}",
        label=labels["en"],
        wordnet_synset=s.name(),
        referent_type=_referent(s),
        definition=s.definition(),
        labels=labels,
    )


@lru_cache(maxsize=50_000)
def _resolve_cached(phrase: str) -> Concept:
    wn = wordnet()
    s = _synset(phrase, wn)
    if s is not None:
        return _concept_from_synset(s, phrase)
    words = phrase.split()
    parent = None
    # Longest known sub-phrase from the left, then any single known word.
    for n in range(len(words) - 1, 0, -1):
        ps = _synset(" ".join(words[:n]), wn)
        if ps is not None:
            parent = ps
            break
    if parent is None:
        for w in words:
            ps = _synset(w, wn)
            if ps is not None:
                parent = ps
                break
    return Concept(
        id=f"term:{phrase}",
        label=phrase,
        parent_id=f"wn:{parent.name()}" if parent is not None else None,
        referent_type=_referent(parent) if parent is not None else None,
        labels={"en": phrase},
    )


def resolve(tokens: list[str], wn: Any = None) -> Concept:
    return _resolve_cached(" ".join(tokens))


def run(conn: sqlite3.Connection, progress: Any = None, only_missing: bool = False, chunk: int = 20000) -> tuple[int, int]:
    """Assign dictionary concepts from names and aliases; manual/ai rows stay.

    ``only_missing`` keeps existing assignments and only handles pictograms
    without any concept (e.g. after merging new shards). Rows are read in id
    chunks so memory stays flat on catalogs with millions of pictograms.
    """
    wordnet()
    if not only_missing:
        conn.execute("DELETE FROM pictogram_concept WHERE method IN ('dictionary', 'tag-match')")
    seen: set[str] = set()
    n = 0
    last = 0
    where = " AND id NOT IN (SELECT pictogram_id FROM pictogram_concept)" if only_missing else ""
    while True:
        rows = conn.execute(f"SELECT id, original_name, raw_tags FROM pictogram WHERE id > ?{where} ORDER BY id LIMIT ?", (last, chunk)).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        for pid, name, tags in rows:
            tokens = split_name(name or "")
            cleaned = clean_tokens(tokens)
            if not cleaned:
                continue
            conn.execute("UPDATE pictogram SET negation=? WHERE id=?", (negation(tokens), pid))
            found = [(resolve(cleaned), 1.0, "dictionary")]
            for alias in json.loads(tags or "[]"):
                ct = clean_tokens(split_name(alias))
                if ct and ct != cleaned:
                    found.append((resolve(ct), 0.6, "tag-match"))
            for c, conf, method in found:
                _ensure(conn, c, seen)
                conn.execute("INSERT OR IGNORE INTO pictogram_concept VALUES (?,?,?,?)", (pid, c.id, conf, method))
            n += 1
        conn.commit()
        if progress:
            progress(n)
    return n, len(seen)


def _ensure(conn: sqlite3.Connection, c: Concept, seen: set[str]) -> None:
    if c.id in seen:
        return
    seen.add(c.id)
    if c.parent_id and c.parent_id not in seen:
        s = wordnet().synset(c.parent_id[3:])
        _ensure(conn, _concept_from_synset(s), seen)
    conn.execute(
        """INSERT INTO concept (id, label, wordnet_synset, labels, definition, parent_id, referent_type)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET labels=excluded.labels,
               definition=excluded.definition, parent_id=excluded.parent_id, referent_type=excluded.referent_type""",
        (c.id, c.label, c.wordnet_synset, json.dumps(c.labels, ensure_ascii=False), c.definition, c.parent_id, c.referent_type),
    )
