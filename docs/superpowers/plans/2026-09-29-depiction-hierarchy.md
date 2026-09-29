# Depiction Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add style groups, depictions (object + view + varieties), objects, and many-to-many meanings above the pictogram. Show them in the vault and HTML.

**Architecture:**
- New package `handdown.hierarchy`: `names.py` (name roles), `group.py` (nested shape grouping plus rule-based objects/meanings), `analysis.py` (queries for the vault/HTML).
- An AI pass in `ai.py` (`run_hierarchy`), and a meaning-level convention score in `score.py`.

**Tech Stack:** Python 3.13, numpy, scipy (linkage/fcluster), sqlite3, NLTK WordNet, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-depiction-hierarchy-design.md`

## Global Constraints

- Style = fill/stroke/rounding/grid and simplification. **Mirroring is not style.** Presence of a detail is a variety; its execution is style.
- Views vocabulary: `front`, `side`, `top`, `bottom`, `three-quarter`, `isometric`, `partial`, `full`, `unknown`.
- Object lexnames: `noun.artifact`, `noun.object`, `noun.animal`, `noun.plant`, `noun.food`, `noun.body`, `noun.person`, `noun.substance`, `noun.shape`.
- Thresholds: style ≤ 0.2, depiction ≤ 0.45 (average linkage, cosine on `cluster.prepare` features), cut from one tree.
- AI bounded by `--limit`, run detached, logged in `ai_run`. Container memory 3 GB: read in chunks.
- Public repository: no assets in git. Commits as Vault51 via the repo config.

## Review Focus

- A name with no object word and no meaning word (e.g. `ic-24`): no crash, depiction without object and no meaning link. → Task 1 test.
- A name concept with a single pictogram: one style group, one depiction. → Task 2 test.
- Mirrored drawing: must not share a style group with the original. → Task 2 test.
- Rerun idempotency: rule rows replaced, `ai`/`manual` rows kept. → Task 2 test.
- AI answer with an unknown view word or a malformed feature list: ignored, no crash. → Task 3 test.

---

### Task 1: Name roles

**Files:**
- Create: `src/handdown/hierarchy/__init__.py`, `src/handdown/hierarchy/names.py`
- Test: `tests/test_hierarchy_names.py`

**Interfaces:**
- Consumes: `concepts.split_name`, `STYLE`, `SIZES`, `wordnet()`, `resolve(tokens)`; `composition.names.OPERATORS`
- Produces: `name_roles(name: str) -> NameRoles`. Fields:
  - `object_tokens: list[str]`, `meaning_tokens: list[str]`
  - `view: str` (vocabulary; `unknown` by default), `varieties: list[str]` (sorted)
- Produces: `is_object_word(token: str) -> bool`, `VIEWS`, `VARIETY_WORDS`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from handdown.hierarchy.names import name_roles


@pytest.fixture(scope="module", autouse=True)
def _wn():
    from handdown.concepts import wordnet

    try:
        wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")


def test_meaning_only():
    r = name_roles("download")
    assert r.object_tokens == [] and r.meaning_tokens == ["download"]


def test_object_only():
    r = name_roles("floppy-disk")
    assert r.object_tokens == ["floppy", "disk"] and r.meaning_tokens == []


def test_object_and_meaning():
    r = name_roles("cloud-download-outline")
    assert r.object_tokens == ["cloud"] and r.meaning_tokens == ["download"]


def test_view_and_variety():
    r = name_roles("coffee-cup-hot-side")
    assert r.view == "side"
    assert r.varieties == ["steam"]
    assert r.object_tokens == ["coffee", "cup"]


def test_unknown_tokens_do_not_crash():
    r = name_roles("ic-24")
    assert r.view == "unknown" and r.varieties == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hierarchy_names.py -q`
Expected: FAIL (`ModuleNotFoundError: handdown.hierarchy`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Split a pictogram name into object, meaning, view and variety words."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from ..composition.names import CONNECTORS, OPERATORS
from ..concepts import SIZES, STYLE, split_name, wordnet

OBJECT_LEXNAMES = {
    "noun.artifact", "noun.object", "noun.animal", "noun.plant", "noun.food",
    "noun.body", "noun.person", "noun.substance", "noun.shape",
}
VIEWS = {"front", "side", "top", "bottom", "three-quarter", "isometric", "partial", "full", "unknown"}
VIEW_WORDS = {
    "top": "top", "topview": "top", "overhead": "top", "above": "top", "bottom": "bottom", "below": "bottom",
    "side": "side", "profile": "side", "front": "front", "back": "front", "iso": "isometric",
    "isometric": "isometric", "3d": "three-quarter", "perspective": "three-quarter", "partial": "partial",
    "half": "partial", "full": "full",
}
# name word -> visible feature it announces
VARIETY_WORDS = {
    "hot": "steam", "steam": "steam", "steaming": "steam", "saucer": "saucer", "lid": "lid", "open": "open",
    "closed": "closed", "empty": "empty", "full": "full", "handle": "handle", "straw": "straw", "ice": "ice",
    "filled": None, "outline": None,
}


@dataclass
class NameRoles:
    object_tokens: list[str] = field(default_factory=list)
    meaning_tokens: list[str] = field(default_factory=list)
    view: str = "unknown"
    varieties: list[str] = field(default_factory=list)


@lru_cache(maxsize=100_000)
def is_object_word(token: str) -> bool:
    wn = wordnet()
    nouns = wn.synsets(token, "n")[:2]
    return any(s.lexname() in OBJECT_LEXNAMES for s in nouns)


@lru_cache(maxsize=100_000)
def _is_word(token: str) -> bool:
    return bool(wordnet().synsets(token))


def name_roles(name: str) -> NameRoles:
    r = NameRoles()
    varieties: set[str] = set()
    for t in split_name(name):
        if t in STYLE or t in SIZES or t in CONNECTORS:
            continue
        if t in VIEW_WORDS and t != "full":
            r.view = VIEW_WORDS[t]
        elif t in VARIETY_WORDS:
            if VARIETY_WORDS[t]:
                varieties.add(VARIETY_WORDS[t])
        elif t in OPERATORS:
            continue  # composite operators are classified by the composite analysis
        elif not _is_word(t):
            continue
        elif is_object_word(t):
            r.object_tokens.append(t)
        else:
            r.meaning_tokens.append(t)
    r.varieties = sorted(varieties)
    return r
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hierarchy_names.py -q`
Expected: PASS (5 tests). If a WordNet sense puts a test word in the other class, note it and adjust `is_object_word` (e.g. first-two-noun-senses rule), not the test.

- [ ] **Step 5: Commit**

```bash
git add src/handdown/hierarchy tests/test_hierarchy_names.py
git commit -m "Hierarchy: name roles (object, meaning, view, varieties)"
```

### Task 2: Nested grouping, objects and rule meanings

**Files:**
- Modify: `src/handdown/schema.sql` (append the tables)
- Create: `src/handdown/hierarchy/group.py`
- Modify: `src/handdown/cli.py` (`hierarchy` command)
- Test: `tests/test_hierarchy_group.py`

**Interfaces:**
- Consumes: `cluster.prepare`, `name_roles`, `concepts.resolve`
- Produces: `nested_labels(vecs) -> tuple[np.ndarray, np.ndarray]` (style labels, depiction labels, nested)
- Produces: `run(conn, log=print) -> dict` (counts: style_groups, depictions, meaning_links); idempotent for rule rows

Schema:

```sql
CREATE TABLE IF NOT EXISTS depiction (
    id INTEGER PRIMARY KEY,
    name_concept_id TEXT,
    object_id TEXT,
    view TEXT DEFAULT 'unknown',
    varieties TEXT DEFAULT '[]',
    description TEXT,
    method TEXT DEFAULT 'rules',
    representative_id INTEGER,
    size INTEGER,
    source_count INTEGER
);
CREATE INDEX IF NOT EXISTS depiction_object ON depiction(object_id);
CREATE TABLE IF NOT EXISTS style_group (
    id INTEGER PRIMARY KEY,
    depiction_id INTEGER REFERENCES depiction(id) ON DELETE CASCADE,
    representative_id INTEGER,
    size INTEGER,
    source_count INTEGER,
    styles TEXT
);
CREATE TABLE IF NOT EXISTS style_member (
    style_group_id INTEGER NOT NULL REFERENCES style_group(id) ON DELETE CASCADE,
    pictogram_id INTEGER NOT NULL,
    PRIMARY KEY (style_group_id, pictogram_id)
);
CREATE INDEX IF NOT EXISTS style_member_pictogram ON style_member(pictogram_id);
CREATE TABLE IF NOT EXISTS meaning_link (
    depiction_id INTEGER NOT NULL REFERENCES depiction(id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY (depiction_id, concept_id, source)
);
CREATE INDEX IF NOT EXISTS meaning_link_concept ON meaning_link(concept_id);
```

- [ ] **Step 1: Write the failing test**

```python
from collections.abc import Iterator

import numpy as np

from handdown import concepts, db
from handdown.adapters.base import Item, SourceInfo
from handdown.config import Config
from handdown.hierarchy import group as hg
from handdown.metrics import measure
from handdown.pipeline import dedupe, harvest, process

NS = 'xmlns="http://www.w3.org/2000/svg"'
CUP = '<path d="M5 8h11v7a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/><path d="M16 10h2a2 2 0 0 1 0 4h-2" fill="none" stroke="#000" stroke-width="1.5"/>'
CUP_O = '<path d="M5 8h11v7a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" fill="none" stroke="#000" stroke-width="1.5"/><path d="M16 10h2a2 2 0 0 1 0 4h-2" fill="none" stroke="#000" stroke-width="1.5"/>'
MIRROR = f'<g transform="translate(24 0) scale(-1 1)">{CUP}</g>'
STEAM = CUP + '<path d="M8 2c1 1-1 2 0 3M11 2c1 1-1 2 0 3" fill="none" stroke="#000" stroke-width="1.2"/>'


def svg(b):
    return f'<svg {NS} viewBox="0 0 24 24">{b}</svg>'


def feat(b):
    return np.asarray(measure(svg(b))["feature"], dtype=np.float16)


def test_nested_labels_style_vs_depiction():
    style, dep = hg.nested_labels(np.stack([feat(CUP), feat(CUP_O), feat(MIRROR), feat(STEAM)]))
    assert style[0] == style[1]  # outline and filled: same style group
    assert style[2] != style[0]  # mirrored: not style
    assert style[3] != style[0]  # steam present: other variety
    for a in range(4):  # nesting: same style group -> same depiction
        for b in range(4):
            if style[a] == style[b]:
                assert dep[a] == dep[b]


class Cups:
    name = "fixture"

    def sources(self) -> Iterator[SourceInfo]:
        for s in ("a", "b"):
            yield SourceInfo(id=f"fx:{s}", name=s, platform_id="fixture", domain="ui")

    def items(self, source_id: str) -> Iterator[Item]:
        if source_id == "fx:a":
            yield Item(original_id="cup", name="coffee-cup", svg=svg(CUP))
            yield Item(original_id="dl", name="floppy-disk-download", svg=svg('<rect x="4" y="4" width="16" height="16"/>'))
        else:
            yield Item(original_id="cup", name="coffee-cup-outline", svg=svg(CUP_O))
            yield Item(original_id="dl", name="cloud-download", svg=svg('<circle cx="12" cy="12" r="7"/>'))
            yield Item(original_id="one", name="ic-24", svg=svg('<path d="M4 4h4v4z"/>'))


def _catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    harvest(conn, Cups())
    process(conn, cfg, workers=1)
    dedupe(conn)
    concepts.run(conn)
    return cfg, conn


def test_run_builds_levels_and_meanings(tmp_path, monkeypatch):
    _cfg, conn = _catalog(tmp_path, monkeypatch)
    first = hg.run(conn, log=lambda *_: None)
    assert first == hg.run(conn, log=lambda *_: None)  # idempotent
    # outline + filled coffee cup: one style group with 2 members
    sizes = [r[0] for r in conn.execute("SELECT size FROM style_group sg JOIN depiction d ON d.id = sg.depiction_id WHERE d.object_id LIKE '%cup%'")]
    assert 2 in sizes
    # both download pictograms reach the meaning "download" through different objects
    rows = conn.execute("SELECT DISTINCT d.object_id FROM meaning_link m JOIN depiction d ON d.id = m.depiction_id WHERE m.concept_id LIKE '%download%'").fetchall()
    assert len(rows) == 2
    # a nameless code: depiction without object, no crash
    assert conn.execute("SELECT COUNT(*) FROM depiction WHERE object_id IS NULL").fetchone()[0] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hierarchy_group.py -q`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Nested grouping (style groups inside depictions) and rule-based objects
and meanings. One linkage tree per name concept, cut twice, keeps the
levels nested."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from ..cluster import prepare
from ..concepts import resolve
from .names import name_roles

STYLE_T = 0.2
DEPICTION_T = 0.45


def nested_labels(vecs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(vecs) == 1:
        return np.array([1]), np.array([1])
    z = linkage(np.nan_to_num(pdist(prepare(vecs), "cosine"), nan=1.0), method="average")
    return fcluster(z, t=STYLE_T, criterion="distance"), fcluster(z, t=DEPICTION_T, criterion="distance")


def _clear_rules(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meaning_link WHERE source IN ('name', 'alias')")
    rule = "SELECT id FROM depiction WHERE method = 'rules'"
    conn.execute(f"DELETE FROM style_member WHERE style_group_id IN (SELECT id FROM style_group WHERE depiction_id IN ({rule}))")
    conn.execute(f"DELETE FROM style_group WHERE depiction_id IN ({rule})")
    conn.execute(f"DELETE FROM meaning_link WHERE depiction_id IN ({rule})")
    conn.execute("DELETE FROM depiction WHERE method = 'rules'")


def run(conn: sqlite3.Connection, log: Any = print) -> dict[str, int]:
    _clear_rules(conn)
    kept = {r[0] for r in conn.execute(
        "SELECT m.pictogram_id FROM style_member m JOIN style_group g ON g.id = m.style_group_id JOIN depiction d ON d.id = g.depiction_id")}
    counts = Counter()
    concepts = [r[0] for r in conn.execute(
        "SELECT DISTINCT concept_id FROM pictogram_concept WHERE method IN ('dictionary', 'manual', 'ai')")]
    for i, cid in enumerate(concepts):
        rows = conn.execute(
            """SELECT p.id, p.source_id, p.original_name, p.raw_tags, p.style, f.vec FROM pictogram_concept pc
               JOIN pictogram p ON p.id = pc.pictogram_id JOIN feature f ON f.pictogram_id = p.id
               WHERE pc.concept_id = ? AND pc.method IN ('dictionary', 'manual', 'ai')
                 AND p.duplicate_of IS NULL AND p.color_class IN ('native', 'derivable', 'threshold')""",
            (cid,),
        ).fetchall()
        rows = [r for r in rows if r[0] not in kept and any(np.frombuffer(r[5], dtype=np.float16))]
        if not rows:
            continue
        style, dep = nested_labels(np.stack([np.frombuffer(r[5], dtype=np.float16) for r in rows]))
        for d in np.unique(dep):
            idx = np.nonzero(dep == d)[0]
            members = [rows[j] for j in idx]
            roles = [name_roles(m[2] or "") for m in members]
            obj_tokens = Counter(" ".join(r.object_tokens) for r in roles if r.object_tokens).most_common(1)
            object_id = resolve(obj_tokens[0][0].split()).id if obj_tokens else None
            view = Counter(r.view for r in roles).most_common(1)[0][0]
            varieties = sorted({v for r in roles for v in r.varieties})
            cur = conn.execute(
                """INSERT INTO depiction (name_concept_id, object_id, view, varieties, method, representative_id, size, source_count)
                   VALUES (?,?,?,?,'rules',?,?,?) RETURNING id""",
                (cid, object_id, view, json.dumps(varieties), members[0][0], len(members), len({m[1] for m in members})),
            )
            did = cur.fetchone()[0]
            counts["depictions"] += 1
            for s in np.unique(style[idx]):
                sidx = [j for j in idx if style[j] == s]
                smembers = [rows[j] for j in sidx]
                cur = conn.execute(
                    "INSERT INTO style_group (depiction_id, representative_id, size, source_count, styles) VALUES (?,?,?,?,?) RETURNING id",
                    (did, smembers[0][0], len(smembers), len({m[1] for m in smembers}), json.dumps(Counter(m[4] or "?" for m in smembers))),
                )
                gid = cur.fetchone()[0]
                conn.executemany("INSERT INTO style_member VALUES (?,?)", [(gid, m[0]) for m in smembers])
                counts["style_groups"] += 1
            meanings: dict[str, tuple[str, float]] = {}
            for r in roles:
                for t in r.meaning_tokens:
                    meanings.setdefault(resolve([t]).id, ("name", 0.8))
            if not meanings and object_id:
                meanings[object_id] = ("name", 0.5)  # an object pictogram means the object itself
            for m in members:
                for alias in json.loads(m[3] or "[]"):
                    for t in name_roles(alias).meaning_tokens:
                        meanings.setdefault(resolve([t]).id, ("alias", 0.6))
            for concept_id, (src, conf) in meanings.items():
                conn.execute("INSERT OR IGNORE INTO meaning_link VALUES (?,?,?,?)", (did, concept_id, src, conf))
                counts["meaning_links"] += 1
        if i % 5000 == 0:
            conn.commit()
            log(f"  {i}/{len(concepts)} {dict(counts)}")
    conn.commit()
    return dict(counts)
```

CLI in `cli.py`, before `status`:

```python
@app.command()
def hierarchy() -> None:
    """Build style groups, depictions, objects and meanings."""
    from .hierarchy import group as hg

    typer.echo(hg.run(_conn(Config()), log=typer.echo))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hierarchy_group.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/schema.sql src/handdown/hierarchy/group.py src/handdown/cli.py tests/test_hierarchy_group.py
git commit -m "Hierarchy: nested style groups and depictions, rule objects and meanings"
```

### Task 3: AI pass for objects, views, features and meanings

**Files:**
- Modify: `src/handdown/ai.py` (`run_hierarchy`), `src/handdown/cli.py` (`ai --job hierarchy`)
- Test: `tests/test_ai_hierarchy.py`

**Interfaces:**
- Consumes: `ai.sheet`, `ai.ask`, `ai._log_run`, `BATCH`; `hierarchy.names.VIEWS`; `concepts.resolve`, `split_name`
- Produces: `run_hierarchy(conn, workdir, limit=48, log=print) -> dict`, which assesses style groups whose depiction is `rules`, established first (source_count desc)

- [ ] **Step 1: Write the failing test**

```python
from handdown import ai, db
from handdown.config import Config


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    conn = db.connect(Config().db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="9" height="9"/></svg>')
    for pid in (1, 2):
        conn.execute("INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid) VALUES (?,?,?,?,?,1)",
                     (pid, "s", str(pid), "coffee", str(svg)))
    conn.execute("INSERT INTO depiction (id, name_concept_id, object_id, view, varieties, method, representative_id, size, source_count) VALUES (1,'wn:coffee.n.01',NULL,'unknown','[]','rules',1,2,1)")
    conn.execute("INSERT INTO style_group (id, depiction_id, representative_id, size, source_count, styles) VALUES (1,1,1,1,1,'{}'), (2,1,2,1,1,'{}')")
    conn.execute("INSERT INTO style_member VALUES (1,1), (2,2)")
    return conn


def test_hierarchy_answers_set_object_and_split_varieties(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    answer = {
        "1": {"object": "mug", "view": "side", "features": ["steam"], "meanings": ["coffee", "break"]},
        "2": {"object": "mug", "view": "side", "features": [], "meanings": ["coffee"]},
    }
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    out = ai.run_hierarchy(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 2
    deps = conn.execute("SELECT object_id, view, varieties FROM depiction ORDER BY id").fetchall()
    assert len(deps) == 2  # the steam variety split off
    assert {d[2] for d in deps} == {"[]", '["steam"]'}
    assert all(d[1] == "side" and "mug" in d[0] for d in deps)
    assert conn.execute("SELECT COUNT(*) FROM meaning_link WHERE source='ai'").fetchone()[0] >= 2


def test_hierarchy_ignores_bad_answers(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    answer = {"1": {"object": "mug", "view": "sideways-ish", "features": "steam"}, "2": "nonsense"}
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    out = ai.run_hierarchy(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 1
    assert conn.execute("SELECT view FROM depiction WHERE id=1").fetchone()[0] == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ai_hierarchy.py -q`
Expected: FAIL (`AttributeError: run_hierarchy`)

- [ ] **Step 3: Write minimal implementation** (append to `ai.py`)

```python
HIERARCHY_PROMPT = """Each numbered cell shows one pictogram (large and at 16 px). For each cell say:
- "object": what is drawn, as a short noun phrase ("coffee mug", "floppy disk", "cloud")
- "view": one of front, side, top, bottom, three-quarter, isometric, partial, full, unknown
- "features": visible details that are PRESENT, as short nouns ("steam", "saucer", "lid"); say whether
  they are there, not how they are drawn
- "meanings": up to 3 things it could mean as a sign or UI icon
Reply with JSON only: {"1": {"object": "...", "view": "...", "features": ["..."], "meanings": ["..."]}, ...}"""


def run_hierarchy(conn: sqlite3.Connection, workdir: Path, limit: int = 48, log: Any = print) -> dict[str, Any]:
    """Blind object/view/features/meanings for style groups of rule depictions,
    established first. Style groups whose view or features differ from their
    depiction move into their own depiction (method 'ai')."""
    from .concepts import resolve, split_name
    from .hierarchy.names import VIEWS

    workdir.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    rows = conn.execute(
        """SELECT g.id AS gid, g.depiction_id, p.norm_path FROM style_group g
           JOIN depiction d ON d.id = g.depiction_id JOIN pictogram p ON p.id = g.representative_id
           WHERE d.method = 'rules' ORDER BY d.source_count DESC, g.source_count DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    done, cost, now = 0, 0.0, db.now()
    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]
        img = sheet([cfg.resolve(r["norm_path"]).read_text() for r in batch])  # type: ignore[union-attr]
        try:
            answer, result = ask(img, HIERARCHY_PROMPT, "You look at pictograms and report what you see. Reply with JSON only.", workdir)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log(f"  batch {start // BATCH + 1}: {e}")
            continue
        _log_run(conn, "hierarchy", result)
        cost += result.get("total_cost_usd") or 0
        for i, r in enumerate(batch, 1):
            a = answer.get(str(i))
            if not isinstance(a, dict) or not isinstance(a.get("object"), str):
                continue
            view = a.get("view") if a.get("view") in VIEWS else None
            features = sorted({f.strip().lower() for f in a["features"] if isinstance(f, str)}) if isinstance(a.get("features"), list) else None
            obj = resolve([t for t in split_name(a["object"]) if t]).id if split_name(a["object"]) else None
            dep = conn.execute("SELECT * FROM depiction WHERE id=?", (r["depiction_id"],)).fetchone()
            target = dep["id"]
            new_view = view or dep["view"]
            new_var = json.dumps(features) if features is not None else dep["varieties"]
            if (new_view, new_var) != (dep["view"], dep["varieties"]) and conn.execute(
                "SELECT COUNT(*) FROM style_group WHERE depiction_id=?", (dep["id"],)).fetchone()[0] > 1:
                # this style group differs from its siblings: its own depiction
                target = conn.execute(
                    """INSERT INTO depiction (name_concept_id, object_id, view, varieties, method, representative_id, size, source_count)
                       SELECT name_concept_id, ?, ?, ?, 'ai', representative_id, size, source_count FROM depiction WHERE id=? RETURNING id""",
                    (obj or dep["object_id"], new_view, new_var, dep["id"]),
                ).fetchone()[0]
                conn.execute("UPDATE style_group SET depiction_id=? WHERE id=?", (target, r["gid"]))
            else:
                conn.execute("UPDATE depiction SET object_id=COALESCE(?, object_id), view=?, varieties=?, method='ai' WHERE id=?",
                             (obj, new_view, new_var, target))
            for m in (a.get("meanings") or [])[:3]:
                if isinstance(m, str) and split_name(m):
                    conn.execute("INSERT OR IGNORE INTO meaning_link VALUES (?,?,?,?)", (target, resolve(split_name(m)).id, "ai", 0.7))
            done += 1
        conn.commit()
        log(f"  batch {start // BATCH + 1}: {done} style groups, ${cost:.3f}")
    return {"assessed": done, "cost_usd": round(cost, 4)}
```

In `cli.py`, the `ai` command: add a branch after the composition branch:

```python
    if job == "hierarchy":
        typer.echo(a.run_hierarchy(conn, cfg.data / "ai-work", limit=limit, log=typer.echo))
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ai_hierarchy.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/ai.py src/handdown/cli.py tests/test_ai_hierarchy.py
git commit -m "Hierarchy: AI pass for objects, views, features and meanings"
```

### Task 4: Meaning convention, vault and HTML

**Files:**
- Create: `src/handdown/hierarchy/analysis.py`
- Modify: `src/handdown/vault.py` (Depictions / Drawn as / Used to mean sections), `src/handdown/site.py` (same)
- Test: `tests/test_hierarchy_group.py` (extend)

**Interfaces:**
- Produces: `drawn_as(conn, meaning_id) -> list[dict]`, one entry per object: `{object_id, label, sources, share, depictions: [{id, view, varieties, style_groups: [{id, size, representative_norm_path}]}]}`, sorted by sources desc. `share` = sources of this object for the meaning / all sources for the meaning (the meaning-level convention).
- Produces: `used_to_mean(conn, object_id) -> list[tuple[str, str, int]]` as (concept_id, label, sources)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_drawn_as_and_vault_sections(tmp_path, monkeypatch):
    from handdown import cluster, score, vault
    from handdown.hierarchy.analysis import drawn_as, used_to_mean

    cfg, conn = _catalog(tmp_path, monkeypatch)
    cluster.run(conn)
    score.run(conn, log=lambda *_: None)
    hg.run(conn, log=lambda *_: None)
    meaning = conn.execute("SELECT concept_id FROM meaning_link WHERE concept_id LIKE '%download%'").fetchone()[0]
    objects = drawn_as(conn, meaning)
    assert len(objects) == 2 and abs(sum(o["share"] for o in objects) - 1.0) < 1e-6
    cup = conn.execute("SELECT object_id FROM depiction WHERE object_id LIKE '%cup%'").fetchone()[0]
    assert any(c == cup for c, _, _ in used_to_mean(conn, cup))
    vault.Exporter(conn, cfg, min_sources=1).run(log=lambda *_: None)
    texts = [p.read_text() for p in cfg.vault.rglob("concepts/**/*.md")]
    assert any("## Drawn as" in t for t in texts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hierarchy_group.py -q -k drawn_as`
Expected: FAIL (`ModuleNotFoundError: handdown.hierarchy.analysis`)

- [ ] **Step 3: Write minimal implementation**

`src/handdown/hierarchy/analysis.py`:

```python
"""Queries over the depiction hierarchy for the vault and HTML."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any


def drawn_as(conn: sqlite3.Connection, meaning_id: str) -> list[dict[str, Any]]:
    """Objects (and their depictions and style groups) used for a meaning,
    with the share of independent sources per object."""
    rows = conn.execute(
        """SELECT d.id, d.object_id, d.view, d.varieties, g.id AS gid, g.size, p.norm_path, p.source_id
           FROM meaning_link m JOIN depiction d ON d.id = m.depiction_id
           JOIN style_group g ON g.depiction_id = d.id JOIN style_member sm ON sm.style_group_id = g.id
           JOIN pictogram p ON p.id = sm.pictogram_id
           WHERE m.concept_id = ?""",
        (meaning_id,),
    ).fetchall()
    objects: dict[str, dict[str, Any]] = {}
    all_sources: set[str] = set()
    for r in rows:
        key = r["object_id"] or "(unknown object)"
        o = objects.setdefault(key, {"object_id": r["object_id"], "sources": set(), "depictions": {}})
        o["sources"].add(r["source_id"])
        all_sources.add(r["source_id"])
        d = o["depictions"].setdefault(r["id"], {"id": r["id"], "view": r["view"], "varieties": json.loads(r["varieties"] or "[]"), "style_groups": {}})
        d["style_groups"].setdefault(r["gid"], {"id": r["gid"], "size": r["size"], "norm_path": r["norm_path"]})
    labels = dict(conn.execute("SELECT id, label FROM concept").fetchall()) if objects else {}
    out = []
    for key, o in objects.items():
        out.append({
            "object_id": o["object_id"], "label": labels.get(o["object_id"], key), "sources": len(o["sources"]),
            "share": len(o["sources"]) / (sum(len(x["sources"]) for x in objects.values()) or 1),
            "depictions": [{**d, "style_groups": list(d["style_groups"].values())} for d in o["depictions"].values()],
        })
    return sorted(out, key=lambda x: -x["sources"])


def used_to_mean(conn: sqlite3.Connection, object_id: str) -> list[tuple[str, str, int]]:
    rows = conn.execute(
        """SELECT m.concept_id, COALESCE(k.label, m.concept_id), COUNT(DISTINCT p.source_id)
           FROM depiction d JOIN meaning_link m ON m.depiction_id = d.id LEFT JOIN concept k ON k.id = m.concept_id
           JOIN style_group g ON g.depiction_id = d.id JOIN style_member sm ON sm.style_group_id = g.id
           JOIN pictogram p ON p.id = sm.pictogram_id
           WHERE d.object_id = ? GROUP BY 1 ORDER BY 3 DESC""",
        (object_id,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]
```

In `vault.py` `concept_note`, before `lines += self.combinations_section(k["id"])`:

```python
        lines += self.hierarchy_section(k["id"])
```

and the method:

```python
    def hierarchy_section(self, concept_id: str) -> list[str]:
        from .hierarchy.analysis import drawn_as, used_to_mean

        out: list[str] = []
        objects = drawn_as(self.conn, concept_id)
        if objects:
            out += ["## Drawn as", ""]
            for o in objects:
                link = f"[[{self.names[o['object_id']]}\\|{o['label']}]]" if o["object_id"] in self.names else o["label"]
                out.append(f"### {link}: {o['share']:.0%} of sources ({o['sources']})")
                for d in o["depictions"]:
                    variety = ", ".join(d["varieties"]) or "plain"
                    sheet = " ".join(f"![[{self.media(g['norm_path'])}\\|24]]" for g in d["style_groups"][:12] if g["norm_path"])
                    out.append(f"- view {d['view']}, {variety}: {sheet}")
                out.append("")
        means = [m for m in used_to_mean(self.conn, concept_id) if m[0] != concept_id]
        if means:
            out += ["## Used to mean", ""]
            out += [f"- [[{self.names[c]}\\|{lbl}]] ({n} sources)" if c in self.names else f"- {lbl} ({n} sources)" for c, lbl, n in means[:30]]
            out.append("")
        return out
```

In `site.py`, after `parts += _combinations_html(conn, cfg, k["id"])`:

```python
    parts += _hierarchy_html(conn, cfg, k["id"], files)
```

and the helper, before `_page`:

```python
def _hierarchy_html(conn: sqlite3.Connection, cfg: Config, concept_id: str, files: dict[str, str]) -> list[str]:
    from .hierarchy.analysis import drawn_as, used_to_mean

    out: list[str] = []
    objects = drawn_as(conn, concept_id)
    if objects:
        out.append("<h2>Drawn as</h2>")
        for o in objects:
            label = f'<a href="{files[o["object_id"]]}.html">{esc(o["label"])}</a>' if o["object_id"] in files else esc(o["label"])
            out.append(f"<h3>{label}: {o['share']:.0%} ({o['sources']})</h3>")
            for d in o["depictions"]:
                tiles = []
                for g in d["style_groups"][:24]:
                    src = cfg.resolve(g["norm_path"])
                    if src is None:
                        continue
                    _link(str(src), cfg.site / "svg" / f"{src.stem}.svg")
                    tiles.append(f'<span class="tile"><img src="../svg/{src.stem}.svg" width="24" height="24" alt="" loading="lazy"></span>')
                out.append(f'<p>view {esc(d["view"])}, {esc(", ".join(d["varieties"]) or "plain")}</p><div class="sheet">{"".join(tiles)}</div>')
    means = [m for m in used_to_mean(conn, concept_id) if m[0] != concept_id]
    if means:
        out.append("<h2>Used to mean</h2><p>" + ", ".join(
            (f'<a href="{files[c]}.html">{esc(lbl)}</a>' if c in files else esc(lbl)) + f" ({n})" for c, lbl, n in means[:30]) + "</p>")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hierarchy_group.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/hierarchy/analysis.py src/handdown/vault.py src/handdown/site.py tests/test_hierarchy_group.py
git commit -m "Hierarchy: drawn-as and used-to-mean in vault and HTML"
```

### Task 5: Run on the catalog

- [ ] **Step 1:** `uv run pytest -q`: all tests pass.
- [ ] **Step 2:** Detached: `uv run handdown hierarchy`, then `uv run handdown ai --job hierarchy --limit 48` (pilot), then `export vault` and `export html`. Log to `data/hierarchy.log`.
- [ ] **Step 3:** Inspect: the counts of style groups, depictions and meaning links, and a sample of meanings with ≥ 2 objects (download, delete, save). Record the findings in `dev/takeaways.md`. Commit and push.
