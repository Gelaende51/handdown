# Composite Pictograms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose pictograms into elements and operators (negation, frame, modifier, repetition, partner, text, decoration), with relations, relative sizes, generic/unique, font type and fit. Then list composites under every element in the vault and HTML.

**Architecture:**
- A new package `handdown.composition` with four parts: name evidence (`names.py`), shape evidence (`shape.py`), the merge and classification step (`merge.py`), and the catalog runner plus storage (`run.py`).
- The AI conflict/sample pass extends `ai.py`. The vault and HTML read the new tables.
- Shape analysis renders at 64 px with the existing `metrics.render`.

**Tech Stack:** Python 3.13, numpy, scipy.ndimage, sqlite3, resvg-py (via `metrics.render`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-composite-pictograms-design.md`

## Global Constraints

- Frames are **always meaning**: every frame is an operator with a shape label (circle, square, rounded-square, triangle, octagon, diamond, shield, bubble, other).
- AI only for conflicts plus a sample (~2,000); rules cover everything else.
- Many-to-many: a composite is listed under every element concept it contains.
- Size classes from `extent_ratio`: dominant > 1.25, equal 0.8–1.25, minor 0.4–0.8, tiny < 0.4.
- Font types: `mark`, `ligature`, `sequence`, `unique`. Fit: `glyph`, `degrades`, `contradictory`, `sequence`.
- The repository is public: no harvested asset enters git. Tests build their SVGs inline.
- Memory: the container has 3 GB. The runner reads in id chunks and uses at most 2 workers locally.

## Review Focus

- A single-part pictogram (no composite): must produce **no** composition row, not a one-part "composite". → test in Task 3.
- A name that says "off" with no slash in the shape: `conflict=1`, still stored with name evidence. → test in Task 3.
- An empty or blank render (all-white): the shape analysis returns no parts and no crash. → test in Task 2.
- A frame with nothing inside (a bare circle icon named "circle"): it is an element, not a framed composite, and negating it counts as contradictory. → tests in Tasks 1 and 3.
- Re-running the stage: idempotent (rows replaced, manual overrides kept). → test in Task 4.

---

### Task 1: Operator vocabulary and name evidence

**Files:**
- Create: `src/handdown/composition/__init__.py` (empty)
- Create: `src/handdown/composition/names.py`
- Test: `tests/test_composition_names.py`

**Interfaces:**
- Consumes: `handdown.concepts.split_name(name) -> list[str]`, `STYLE`, `SIZES`
- Produces: `name_evidence(name: str, tags: list[str] | None = None) -> NameEvidence`. Fields:
  - `base: list[str]`: element tokens
  - `parts: list[NamePart]`: `NamePart(role, label, count=1)`
  - `frame: str | None`
- Produces: `OPERATORS: dict[str, tuple[str, str]]`: token → (role, label)
- Produces: `UNICODE_MARKS: dict[str, str]`: label → code point

- [ ] **Step 1: Write the failing test**

```python
from handdown.composition.names import name_evidence


def roles(ev):
    return sorted((p.role, p.label) for p in ev.parts)


def test_negation_and_base():
    ev = name_evidence("bell-off")
    assert ev.base == ["bell"]
    assert roles(ev) == [("negation", "slash")]


def test_frame_is_meaning_with_shape():
    ev = name_evidence("home-circle-outline")
    assert ev.base == ["home"]
    assert ev.frame == "circle"
    assert roles(ev) == [("frame", "circle")]


def test_modifier_and_repetition():
    assert roles(name_evidence("user-plus")) == [("modifier", "plus")]
    ev = name_evidence("file-multiple")
    assert roles(ev) == [("repetition", "plural")]


def test_bare_frame_is_an_element_not_a_composite():
    ev = name_evidence("circle")
    assert ev.base == ["circle"] and ev.parts == []


def test_partners_two_elements():
    ev = name_evidence("car-house")
    assert ev.base == ["car", "house"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition_names.py -q`
Expected: FAIL (`ModuleNotFoundError: handdown.composition`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Name evidence: which operators a pictogram's name (and aliases) announce."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..concepts import SIZES, STYLE, split_name

# token -> (role, label)
OPERATORS: dict[str, tuple[str, str]] = {
    **{t: ("negation", "slash") for t in ("off", "slash", "slashed", "disabled", "no", "not", "crossed", "forbidden", "ban", "prohibited")},
    **{t: ("frame", t) for t in ("circle", "square", "triangle", "octagon", "diamond", "shield", "hexagon")},
    "box": ("frame", "square"), "rect": ("frame", "square"), "rectangle": ("frame", "square"),
    "rounded": ("frame", "rounded-square"), "bubble": ("frame", "bubble"), "badge": ("frame", "badge"),
    **{t: ("modifier", "plus") for t in ("plus", "add", "new")},
    **{t: ("modifier", "minus") for t in ("minus", "remove", "subtract")},
    **{t: ("modifier", "check") for t in ("check", "checked", "checkmark", "done", "ok", "verified")},
    **{t: ("modifier", "x") for t in ("x", "close", "cancel", "xmark", "times")},
    **{t: ("modifier", t) for t in ("lock", "unlock", "clock", "star", "heart", "search", "edit", "question", "alert", "sync", "share", "gear", "download", "upload")},
    "exclamation": ("modifier", "alert"), "warning": ("modifier", "alert"), "settings": ("modifier", "gear"), "cog": ("modifier", "gear"),
    "time": ("modifier", "clock"), "history": ("modifier", "clock"), "pen": ("modifier", "edit"), "pencil": ("modifier", "edit"),
    **{t: ("repetition", "plural") for t in ("multiple", "stack", "stacked", "group", "copy", "duplicate", "many")},
    "dot": ("modifier", "dot"), "notification": ("modifier", "dot"),
}
# Unicode precedents for operators a font could implement as combining marks
UNICODE_MARKS = {"slash": "U+0338", "circle": "U+20DD", "square": "U+20DE", "diamond": "U+20DF", "prohibition": "U+20E0", "triangle": "U+20E4"}


@dataclass
class NamePart:
    role: str
    label: str
    count: int = 1


@dataclass
class NameEvidence:
    base: list[str] = field(default_factory=list)
    parts: list[NamePart] = field(default_factory=list)
    frame: str | None = None


def name_evidence(name: str, tags: list[str] | None = None) -> NameEvidence:
    tokens = [t for t in split_name(name) if t not in STYLE and t not in SIZES]
    ev = NameEvidence()
    ops = [t for t in tokens if t in OPERATORS]
    base = [t for t in tokens if t not in OPERATORS]
    if not base:  # "circle", "plus": the operator word is itself the element
        ev.base = tokens[:1]
        return ev
    ev.base = base
    seen: set[tuple[str, str]] = set()
    for t in ops:
        role, label = OPERATORS[t]
        if (role, label) in seen:
            continue
        seen.add((role, label))
        ev.parts.append(NamePart(role, label))
        if role == "frame":
            ev.frame = label
    return ev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition_names.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/composition tests/test_composition_names.py
git commit -m "Composition: operator vocabulary and name evidence"
```

### Task 2: Shape evidence with relations and relative sizes

**Files:**
- Create: `src/handdown/composition/shape.py`
- Test: `tests/test_composition_shape.py`

**Interfaces:**
- Consumes: `handdown.metrics.render(svg, size) -> np.ndarray` (ink 0..1)
- Produces: `shape_evidence(svg: str) -> ShapeEvidence`. Fields:
  - `parts: list[ShapePart]`: `ShapePart(bbox=(y0, y1, x0, x1), area, role_hint, label)`, where role_hint is one of base, negation, frame, modifier, repetition, text, partner
  - `slash: bool`, `frame: str | None`, `repetition: int`
  - `relations: list[tuple[int, int, str]]`
- Produces: `size_metrics(part, base, glyph_bbox, size=64) -> dict` with keys `area_ratio`, `extent_ratio`, `glyph_share`, `px16`, `size_class`
- Produces: `size_class(extent_ratio) -> str`

- [ ] **Step 1: Write the failing test**

```python
from handdown.composition.shape import shape_evidence, size_class

NS = 'xmlns="http://www.w3.org/2000/svg"'
BELL = '<path d="M6 17h12l-2-3v-4a4 4 0 0 0-8 0v4z"/>'


def svg(body):
    return f'<svg {NS} viewBox="0 0 24 24">{body}</svg>'


def test_slash_detected():
    ev = shape_evidence(svg(BELL + '<path d="M3 3L21 21" stroke="#000" stroke-width="2"/>'))
    assert ev.slash


def test_frame_detected_with_content():
    ev = shape_evidence(svg('<circle cx="12" cy="12" r="10" fill="none" stroke="#000" stroke-width="2"/><rect x="9" y="9" width="6" height="6"/>'))
    assert ev.frame == "circle"
    assert any(p.role_hint == "frame" for p in ev.parts)


def test_repetition_counts_equal_copies():
    ev = shape_evidence(svg('<rect x="2" y="9" width="5" height="6"/><rect x="9.5" y="9" width="5" height="6"/><rect x="17" y="9" width="5" height="6"/>'))
    assert ev.repetition == 3


def test_corner_badge_is_minor_modifier_with_relation():
    ev = shape_evidence(svg('<rect x="2" y="2" width="14" height="14"/><circle cx="19" cy="19" r="3"/>'))
    mods = [p for p in ev.parts if p.role_hint == "modifier"]
    assert len(mods) == 1
    assert any(rel.startswith("corner:br") for _, _, rel in ev.relations)
    assert mods[0].size["size_class"] in ("minor", "tiny")


def test_blank_render_has_no_parts():
    assert shape_evidence(svg("")).parts == []


def test_size_classes():
    assert [size_class(x) for x in (1.5, 1.0, 0.5, 0.2)] == ["dominant", "equal", "minor", "tiny"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition_shape.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Shape evidence from a 64 px render: parts, slash, frame, repetition,
corner modifiers, pairwise relations and relative sizes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from ..metrics import render

SIZE = 64


def size_class(extent_ratio: float) -> str:
    if extent_ratio > 1.25:
        return "dominant"
    if extent_ratio >= 0.8:
        return "equal"
    if extent_ratio >= 0.4:
        return "minor"
    return "tiny"


@dataclass
class ShapePart:
    bbox: tuple[int, int, int, int]  # y0, y1, x0, x1 (exclusive ends)
    area: int
    role_hint: str = "base"
    label: str | None = None
    size: dict[str, Any] = field(default_factory=dict)

    @property
    def extent(self) -> int:
        return max(self.bbox[1] - self.bbox[0], self.bbox[3] - self.bbox[2])


@dataclass
class ShapeEvidence:
    parts: list[ShapePart] = field(default_factory=list)
    slash: bool = False
    frame: str | None = None
    repetition: int = 1
    relations: list[tuple[int, int, str]] = field(default_factory=list)


def size_metrics(part: ShapePart, base: ShapePart, glyph: tuple[int, int, int, int], size: int = SIZE) -> dict[str, Any]:
    glyph_extent = max(glyph[1] - glyph[0], glyph[3] - glyph[2]) or 1
    extent_ratio = part.extent / (base.extent or 1)
    return {
        "area_ratio": round(part.area / (base.area or 1), 3),
        "extent_ratio": round(extent_ratio, 3),
        "glyph_share": round(part.extent / glyph_extent, 3),
        "px16": round(part.extent * 16 / size, 1),
        "size_class": size_class(extent_ratio),
    }


def _is_slash(mask: np.ndarray, bbox: tuple[int, int, int, int], glyph_extent: int) -> bool:
    ys, xs = np.nonzero(mask)
    if len(ys) < 10:
        return False
    length = math.hypot(np.ptp(ys), np.ptp(xs))
    if length < 0.6 * glyph_extent * math.sqrt(2) * 0.7:
        return False
    cov = np.cov(np.vstack([xs, ys]))
    evals = np.linalg.eigvalsh(cov)
    elongation = math.sqrt(evals[1] / max(evals[0], 1e-6))
    angle = abs(math.degrees(math.atan2(np.ptp(ys), np.ptp(xs))))
    return elongation > 5 and 30 <= angle <= 60


def _frame_label(mask: np.ndarray, others: np.ndarray) -> str | None:
    filled = ndimage.binary_fill_holes(mask)
    if not others.any() or (filled & others).sum() < 0.9 * others.sum():
        return None
    ys, xs = np.nonzero(filled)
    h, w = np.ptp(ys) + 1, np.ptp(xs) + 1
    extent = filled.sum() / (h * w)
    aspect = w / h
    if 0.85 < aspect < 1.18 and abs(extent - math.pi / 4) < 0.06:
        return "circle"
    if extent > 0.92:
        return "square"
    if 0.85 < extent <= 0.92:
        return "rounded-square"
    if abs(extent - 0.5) < 0.08:
        return "triangle" if filled[: h // 3].sum() < filled[-h // 3 :].sum() else "diamond"
    if 0.8 < extent < 0.86 and 0.9 < aspect < 1.1:
        return "octagon"
    return "other"


def shape_evidence(svg: str) -> ShapeEvidence:
    ink = render(svg, SIZE) > 0.5
    ev = ShapeEvidence()
    if not ink.any():
        return ev
    labels, n = ndimage.label(ink)
    ys, xs = np.nonzero(ink)
    glyph = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
    glyph_extent = max(glyph[1] - glyph[0], glyph[3] - glyph[2])
    slices = ndimage.find_objects(labels)
    for i, sl in enumerate(slices, 1):
        area = int((labels[sl] == i).sum())
        if area < 4:
            continue
        ev.parts.append(ShapePart(bbox=(sl[0].start, sl[0].stop, sl[1].start, sl[1].stop), area=area, label=str(i)))
    if not ev.parts:
        return ev
    masks = {p.label: labels == int(p.label) for p in ev.parts}
    # frame: the largest part encloses all others
    biggest = max(ev.parts, key=lambda p: (p.extent, p.area))
    others = ink & ~masks[biggest.label]
    frame = _frame_label(masks[biggest.label], others) if len(ev.parts) > 1 else None
    if frame:
        biggest.role_hint = "frame"
        ev.frame = frame
    # slash
    for p in ev.parts:
        if p.role_hint == "base" and _is_slash(masks[p.label], p.bbox, glyph_extent):
            p.role_hint = "negation"
            ev.slash = True
    # repetition: equal-sized parts with matching normalized masks
    content = [p for p in ev.parts if p.role_hint == "base"]
    if len(content) >= 2:
        ref = max(content, key=lambda p: p.area)
        same = [p for p in content if abs(p.area - ref.area) <= 0.15 * ref.area and _similar(masks[p.label], masks[ref.label], p, ref)]
        if len(same) >= 2 and len(same) == len(content):
            ev.repetition = len(same)
            for p in same[1:]:
                p.role_hint = "repetition"
    # base = largest remaining content part; corner modifiers are small parts in a corner
    content = [p for p in ev.parts if p.role_hint == "base"]
    base = max(content, key=lambda p: p.area) if content else biggest
    for p in content:
        if p is base:
            continue
        ratio = p.extent / (base.extent or 1)
        corner = _corner(p, glyph)
        p.role_hint = "modifier" if ratio < 0.6 and corner else "partner"
    for p in ev.parts:
        p.size = size_metrics(p, base, glyph)
    idx = {id(p): k for k, p in enumerate(ev.parts)}
    for p in ev.parts:
        if p is base:
            continue
        ev.relations.append((idx[id(base)], idx[id(p)], _relation(p, base, glyph, masks)))
    return ev


def _similar(a: np.ndarray, b: np.ndarray, pa: ShapePart, pb: ShapePart) -> bool:
    ca = a[pa.bbox[0]:pa.bbox[1], pa.bbox[2]:pa.bbox[3]]
    cb = b[pb.bbox[0]:pb.bbox[1], pb.bbox[2]:pb.bbox[3]]
    h, w = min(ca.shape[0], cb.shape[0]), min(ca.shape[1], cb.shape[1])
    ca, cb = ca[:h, :w], cb[:h, :w]
    union = (ca | cb).sum()
    return bool(union) and (ca & cb).sum() / union >= 0.85


def _corner(p: ShapePart, glyph: tuple[int, int, int, int]) -> str | None:
    cy = (p.bbox[0] + p.bbox[1]) / 2
    cx = (p.bbox[2] + p.bbox[3]) / 2
    gy0, gy1, gx0, gx1 = glyph
    th, tw = (gy1 - gy0) / 3, (gx1 - gx0) / 3
    v = "t" if cy < gy0 + th else "b" if cy > gy1 - th else None
    h = "l" if cx < gx0 + tw else "r" if cx > gx1 - tw else None
    return f"{v}{h}" if v and h else None


def _relation(p: ShapePart, base: ShapePart, glyph: tuple[int, int, int, int], masks: dict[str, np.ndarray]) -> str:
    if p.role_hint == "frame":
        return "surrounds"
    if p.role_hint == "negation":
        return "crossing"
    if p.role_hint == "modifier":
        grown = ndimage.binary_dilation(masks[p.label], iterations=2)
        gap = not (grown & masks[base.label]).any()
        return f"corner:{_corner(p, glyph)}" + (":cutout" if gap else ":touching")
    py0, py1, px0, px1 = p.bbox
    by0, by1, bx0, bx1 = base.bbox
    if ndimage.binary_dilation(masks[p.label], iterations=1)[masks[base.label]].any():
        return "touching"
    if py1 <= by0:
        return "above"
    if py0 >= by1:
        return "below"
    if px1 <= bx0:
        return "left_of"
    if px0 >= bx1:
        return "right_of"
    return "over"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition_shape.py -q`
Expected: PASS (6 tests). If a geometric threshold misses a hand-built case, adjust the threshold rather than the test, and note it in `dev/takeaways.md`.

- [ ] **Step 5: Commit**

```bash
git add src/handdown/composition/shape.py tests/test_composition_shape.py
git commit -m "Composition: shape evidence, relations and relative sizes"
```

### Task 3: Merge, classify and store

**Files:**
- Modify: `src/handdown/schema.sql` (append the tables)
- Create: `src/handdown/composition/merge.py`
- Test: `tests/test_composition_merge.py`

**Interfaces:**
- Consumes: `NameEvidence`, `ShapeEvidence` from Tasks 1–2
- Produces: `classify(name_ev, shape_ev, has_text: bool) -> Composition | None`. Returns None for single-part pictograms.
  - `Composition` fields: `parts: list[dict]` (role, label, element, position, count, size…), `relations: list[tuple[int, int, str]]`, `kind`, `font_type`, `fit`, `conflict: bool`, `confidence: float`

Schema to append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS composition (
    pictogram_id INTEGER PRIMARY KEY REFERENCES pictogram(id) ON DELETE CASCADE,
    kind TEXT,            -- generic | unique
    font_type TEXT,       -- mark | ligature | sequence | unique
    fit TEXT,             -- glyph | degrades | contradictory | sequence
    conflict INTEGER DEFAULT 0,
    confidence REAL,
    method TEXT,          -- rules | ai | manual
    computed_at TEXT
);
CREATE TABLE IF NOT EXISTS composition_part (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    part_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    label TEXT,
    concept_id TEXT,
    position TEXT,
    count INTEGER DEFAULT 1,
    area_ratio REAL, extent_ratio REAL, glyph_share REAL, px16 REAL, size_class TEXT,
    PRIMARY KEY (pictogram_id, part_no)
);
CREATE INDEX IF NOT EXISTS composition_part_concept ON composition_part(concept_id);
CREATE TABLE IF NOT EXISTS composition_relation (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    part_a INTEGER NOT NULL, part_b INTEGER NOT NULL, relation TEXT NOT NULL,
    PRIMARY KEY (pictogram_id, part_a, part_b)
);
```

- [ ] **Step 1: Write the failing test**

```python
from handdown.composition.merge import classify
from handdown.composition.names import name_evidence
from handdown.composition.shape import ShapeEvidence, ShapePart


def shape(*parts, slash=False, frame=None, repetition=1, relations=()):
    return ShapeEvidence(parts=list(parts), slash=slash, frame=frame, repetition=repetition, relations=list(relations))


def part(role, extent=20, size_class="equal", px16=8.0):
    return ShapePart(bbox=(0, extent, 0, extent), area=extent * extent, role_hint=role,
                     size={"size_class": size_class, "px16": px16, "extent_ratio": 1.0, "area_ratio": 1.0, "glyph_share": 1.0})


def test_single_part_is_not_a_composite():
    assert classify(name_evidence("bell"), shape(part("base")), False) is None


def test_agreeing_negation_is_generic_mark():
    c = classify(name_evidence("bell-off"), shape(part("base"), part("negation"), slash=True, relations=[(0, 1, "crossing")]), False)
    assert c.kind == "generic" and c.font_type == "mark" and not c.conflict and c.fit == "glyph"


def test_name_without_shape_is_conflict():
    c = classify(name_evidence("bell-off"), shape(part("base")), False)
    assert c is not None and c.conflict


def test_equal_partners_are_sequence_and_degrade():
    c = classify(name_evidence("car-house"), shape(part("base"), part("partner"), relations=[(0, 1, "right_of")]), False)
    assert c.font_type == "sequence" and c.fit in ("degrades", "sequence")


def test_negated_bare_frame_is_contradictory():
    c = classify(name_evidence("circle-off"), shape(part("frame"), part("negation"), slash=True), False)
    assert c.fit == "contradictory"


def test_tiny_modifier_degrades():
    c = classify(name_evidence("user-plus"), shape(part("base"), part("modifier", 6, "tiny", 1.5), relations=[(0, 1, "corner:br:cutout")]), False)
    assert c.font_type == "ligature" and c.fit == "degrades"


def test_merged_parts_are_unique():
    c = classify(name_evidence("user-plus"), shape(part("base")), False)
    assert c.kind == "unique"  # name says composite, shape is one fused part
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition_merge.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Combine name and shape evidence into one classified composition."""

from __future__ import annotations

from dataclasses import dataclass, field

from .names import NameEvidence
from .shape import ShapeEvidence

MIN_MODIFIER_PX16 = 5.0  # smaller corner modifiers stop being recognizable at 16 px


@dataclass
class Composition:
    parts: list[dict] = field(default_factory=list)
    relations: list[tuple[int, int, str]] = field(default_factory=list)
    kind: str = "generic"
    font_type: str = "ligature"
    fit: str = "glyph"
    conflict: bool = False
    confidence: float = 0.5


def classify(name: NameEvidence, shape: ShapeEvidence, has_text: bool) -> Composition | None:
    name_roles = {(p.role, p.label) for p in name.parts}
    shape_roles = [p.role_hint for p in shape.parts]
    shape_ops = [r for r in shape_roles if r != "base"]
    partners_by_name = len(name.base) >= 2
    if not name_roles and not shape_ops and not partners_by_name and not has_text:
        return None  # a single element
    c = Composition()
    # parts: shape parts carry sizes; name-only operators are added without geometry
    for p in shape.parts:
        label = p.label if p.role_hint != "frame" else shape.frame
        if p.role_hint == "negation":
            label = "slash"
        c.parts.append({"role": p.role_hint, "label": label, "size": p.size, "position": None, "count": 1})
    if shape.repetition > 1 and c.parts:
        c.parts[0]["count"] = shape.repetition
    shape_role_set = {p["role"] for p in c.parts}
    for role, label in sorted(name_roles):
        if role not in shape_role_set:
            c.parts.append({"role": role, "label": label, "size": {}, "position": None, "count": 1, "from_name": True})
    if has_text:
        c.parts.append({"role": "text", "label": "text", "size": {}, "position": None, "count": 1})
    c.relations = list(shape.relations)
    # evidence agreement
    name_only = [p for p in c.parts if p.get("from_name")]
    shape_only = shape_ops and not name_roles and not partners_by_name
    c.conflict = bool(name_only) and len(shape.parts) > 1
    c.kind = "unique" if name_only and len(shape.parts) == 1 else "generic"
    if name_only and len(shape.parts) == 1:
        c.conflict = False  # fused drawing: name-announced parts are merged into one shape
    c.confidence = 0.9 if (name_roles and shape_ops and not c.conflict) else 0.6 if not shape_only else 0.5
    roles = [p["role"] for p in c.parts]
    # font type
    if c.kind == "unique":
        c.font_type = "unique"
    elif "partner" in roles or partners_by_name:
        c.font_type = "sequence"
    elif set(roles) - {"base"} <= {"negation", "frame"}:
        c.font_type = "mark"
    else:
        c.font_type = "ligature"
    c.fit = _fit(c, roles)
    return c


def _fit(c: Composition, roles: list[str]) -> str:
    ops = [r for r in roles if r not in ("base",)]
    if roles.count("negation") > 1 or ("negation" in roles and "base" not in roles and "frame" in roles):
        return "contradictory"
    labels = {p["label"] for p in c.parts if p["role"] == "modifier"}
    if {"check", "x"} <= labels or roles.count("frame") > 1:
        return "contradictory"
    if "partner" in roles:
        return "sequence" if roles.count("partner") >= 2 else "degrades"
    mods = [p for p in c.parts if p["role"] == "modifier"]
    if len(mods) > 1 or len(set(ops)) >= 3 or "text" in roles:
        return "degrades"
    if any(p["size"].get("px16", 99) < MIN_MODIFIER_PX16 for p in mods):
        return "degrades"
    if any(p.get("count", 1) > 3 for p in c.parts):
        return "degrades"
    return "glyph"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition_merge.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/schema.sql src/handdown/composition/merge.py tests/test_composition_merge.py
git commit -m "Composition: merge evidence, classify kind/font type/fit, schema"
```

### Task 4: Catalog runner, element concepts and CLI

**Files:**
- Create: `src/handdown/composition/run.py`
- Modify: `src/handdown/cli.py` (add the `compose` command)
- Test: `tests/test_composition_run.py`

**Interfaces:**
- Consumes: `name_evidence`, `shape_evidence`, `classify`; `concepts.resolve(tokens)`; the tables from Task 3
- Produces: `run(conn, cfg, limit=None, workers=1, log=print) -> dict[str, int]`, which is idempotent (it replaces rule rows and keeps `method='manual'`)
- Produces: CLI `handdown compose [--limit N] [--workers W]`

- [ ] **Step 1: Write the failing test**

```python
from handdown import db
from handdown.composition import run as comp
from handdown.config import Config
from handdown.pipeline import harvest, process
from tests.test_e2e import NS, Fixture  # reuse the fixture adapter


class Composites(Fixture):
    def sources(self):
        from handdown.adapters.base import SourceInfo
        yield SourceInfo(id="fx:c", name="C", platform_id="fixture", domain="ui")

    def items(self, source_id):
        from handdown.adapters.base import Item
        bell = '<path d="M6 17h12l-2-3v-4a4 4 0 0 0-8 0v4z"/>'
        yield Item(original_id="bell", name="bell", svg=f'<svg {NS} viewBox="0 0 24 24">{bell}</svg>')
        yield Item(original_id="bell-off", name="bell-off",
                   svg=f'<svg {NS} viewBox="0 0 24 24">{bell}<path d="M3 3L21 21" stroke="#000" stroke-width="2"/></svg>')


def test_run_stores_composites_under_elements_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    harvest(conn, Composites())
    process(conn, cfg, workers=1)
    first = comp.run(conn, cfg, log=lambda *_: None)
    again = comp.run(conn, cfg, log=lambda *_: None)
    assert first == again
    assert conn.execute("SELECT COUNT(*) FROM composition").fetchone()[0] == 1
    roles = {r[0] for r in conn.execute("SELECT role FROM composition_part")}
    assert {"base", "negation"} <= roles
    base_concept = conn.execute("SELECT concept_id FROM composition_part WHERE role='base'").fetchone()[0]
    assert base_concept and "bell" in base_concept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition_run.py -q`
Expected: FAIL (`ImportError: run`)

- [ ] **Step 3: Write minimal implementation**

```python
"""Run composite classification over the catalog (chunked, restartable)."""

from __future__ import annotations

import json
import multiprocessing as mp
import sqlite3
from pathlib import Path
from typing import Any

from .. import db
from ..config import Config
from .merge import Composition, classify
from .names import name_evidence
from .shape import shape_evidence

CHUNK = 2000


def _work(args: tuple[int, str, str | None, str | None, int]) -> tuple[int, Composition | None, list[str] | None]:
    pid, name, tags, norm_path, has_text = args
    ev_name = name_evidence(name or "", json.loads(tags or "[]"))
    try:
        ev_shape = shape_evidence(Path(norm_path).read_text()) if norm_path else None
    except (OSError, ValueError):
        ev_shape = None
    if ev_shape is None:
        from .shape import ShapeEvidence
        ev_shape = ShapeEvidence()
    return pid, classify(ev_name, ev_shape, bool(has_text)), ev_name.base


def _store(conn: sqlite3.Connection, pid: int, c: Composition, base_tokens: list[str], now: str) -> None:
    from ..concepts import resolve

    conn.execute("DELETE FROM composition_part WHERE pictogram_id=?", (pid,))
    conn.execute("DELETE FROM composition_relation WHERE pictogram_id=?", (pid,))
    conn.execute(
        "INSERT OR REPLACE INTO composition VALUES (?,?,?,?,?,?,?,?)",
        (pid, c.kind, c.font_type, c.fit, int(c.conflict), c.confidence, "rules", now),
    )
    partners = [resolve([t]).id for t in base_tokens[1:]] if len(base_tokens) > 1 else []
    base_concept = resolve(base_tokens[:1] if partners else base_tokens).id if base_tokens else None
    for no, p in enumerate(c.parts):
        concept = base_concept if p["role"] in ("base", "repetition") else None
        if p["role"] == "partner" and partners:
            concept = partners.pop(0)
        s = p.get("size") or {}
        conn.execute(
            "INSERT INTO composition_part VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, no, p["role"], p.get("label"), concept, p.get("position"), p.get("count", 1),
             s.get("area_ratio"), s.get("extent_ratio"), s.get("glyph_share"), s.get("px16"), s.get("size_class")),
        )
    for a, b, rel in c.relations:
        conn.execute("INSERT OR REPLACE INTO composition_relation VALUES (?,?,?,?)", (pid, a, b, rel))


def run(conn: sqlite3.Connection, cfg: Config, limit: int | None = None, workers: int = 1, log: Any = print) -> dict[str, int]:
    manual = {r[0] for r in conn.execute("SELECT pictogram_id FROM composition WHERE method='manual'")}
    conn.execute("DELETE FROM composition WHERE method='rules'")
    counts = {"seen": 0, "composites": 0, "conflicts": 0}
    last = 0
    now = db.now()
    pool = mp.get_context("forkserver").Pool(workers) if workers > 1 else None
    try:
        while True:
            rows = conn.execute(
                """SELECT id, original_name, raw_tags, norm_path, has_text FROM pictogram
                   WHERE id > ? AND duplicate_of IS NULL AND svg_valid = 1 ORDER BY id LIMIT ?""",
                (last, CHUNK),
            ).fetchall()
            if not rows:
                break
            last = rows[-1][0]
            tasks = [tuple(r) for r in rows if r[0] not in manual]
            results = pool.imap_unordered(_work, tasks, chunksize=64) if pool else map(_work, tasks)
            for pid, comp, base in results:
                counts["seen"] += 1
                if comp is None:
                    conn.execute("DELETE FROM composition_part WHERE pictogram_id=?", (pid,))
                    conn.execute("DELETE FROM composition_relation WHERE pictogram_id=?", (pid,))
                    continue
                _store(conn, pid, comp, base or [], now)
                counts["composites"] += 1
                counts["conflicts"] += int(comp.conflict)
            conn.commit()
            log(f"  {counts}")
            if limit and counts["seen"] >= limit:
                break
    finally:
        if pool:
            pool.close()
    return counts
```

CLI addition in `cli.py`, before `status`:

```python
@app.command()
def compose(limit: int = typer.Option(None), workers: int = 1) -> None:
    """Classify composite pictograms (elements, operators, relations, sizes)."""
    from .composition import run as comp

    cfg = Config()
    typer.echo(comp.run(_conn(cfg), cfg, limit=limit, workers=workers, log=typer.echo))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition_run.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/handdown/composition/run.py src/handdown/cli.py tests/test_composition_run.py
git commit -m "Composition: catalog runner and compose command"
```

### Task 5: Vault, HTML and the composition-rules analysis

**Files:**
- Create: `src/handdown/composition/analysis.py`
- Modify: `src/handdown/vault.py` (Combinations section in `concept_note`, operator notes and the rules index in `indexes`)
- Modify: `src/handdown/site.py` (Combinations section on concept pages)
- Test: `tests/test_composition_run.py` (extend)

**Interfaces:**
- Consumes: the tables from Task 3
- Produces: `combinations(conn, concept_id) -> dict[str, list[sqlite3.Row]]`, which groups composites containing the concept by operator key (e.g. `negated`, `framed:circle`, `with:plus`, `repeated`, `with partner`)
- Produces: `rules_summary(conn) -> dict`, with counts per fit class, per operator, and the median `px16` of modifiers per fit class

- [ ] **Step 1: Write the failing test** (append to `tests/test_composition_run.py`)

```python
def test_combinations_listed_under_element_and_rules_index(tmp_path, monkeypatch):
    from handdown import cluster, concepts, score, vault
    from handdown.composition.analysis import combinations, rules_summary

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    harvest(conn, Composites())
    process(conn, cfg, workers=1)
    concepts.run(conn)
    cluster.run(conn)
    score.run(conn, log=lambda *_: None)
    comp.run(conn, cfg, log=lambda *_: None)
    base = conn.execute("SELECT concept_id FROM composition_part WHERE role='base'").fetchone()[0]
    groups = combinations(conn, base)
    assert "negated" in groups and len(groups["negated"]) == 1
    assert rules_summary(conn)["fit"]["glyph"] >= 1
    vault.Exporter(conn, cfg, min_sources=1).run(log=lambda *_: None)
    note = next(p for p in cfg.vault.rglob("concepts/**/*.md") if vault.read_frontmatter(p).get("concept") == base)
    assert "## Combinations" in note.read_text()
    assert (cfg.vault / "_index" / "composition-rules.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition_run.py -q`
Expected: FAIL (`ModuleNotFoundError: handdown.composition.analysis`)

- [ ] **Step 3: Write minimal implementation**

`src/handdown/composition/analysis.py`:

```python
"""Queries over classified composites for the vault, HTML and rules index."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from typing import Any


def _key(role: str, label: str | None) -> str | None:
    return {"negation": "negated", "repetition": "repeated", "partner": "with partner", "text": "with text",
            "decoration": "decorated"}.get(role) or (f"framed:{label}" if role == "frame" else f"with:{label}" if role == "modifier" else None)


def combinations(conn: sqlite3.Connection, concept_id: str) -> dict[str, list[sqlite3.Row]]:
    """Composites that contain the concept, grouped by operator."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    rows = conn.execute(
        """SELECT c.pictogram_id, c.kind, c.font_type, c.fit, p.original_name, p.norm_path, p.original_url, p.source_id
           FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
           WHERE c.pictogram_id IN (SELECT pictogram_id FROM composition_part WHERE concept_id = ?)""",
        (concept_id,),
    ).fetchall()
    for r in rows:
        ops = conn.execute("SELECT role, label FROM composition_part WHERE pictogram_id=? AND role NOT IN ('base')", (r["pictogram_id"],)).fetchall()
        keys = {k for k in (_key(o["role"], o["label"]) for o in ops) if k}
        for k in sorted(keys) or ["other"]:
            groups[k].append(r)
    return dict(groups)


def rules_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    fit = dict(conn.execute("SELECT fit, COUNT(*) FROM composition GROUP BY 1").fetchall())
    font = dict(conn.execute("SELECT font_type, COUNT(*) FROM composition GROUP BY 1").fetchall())
    kind = dict(conn.execute("SELECT kind, COUNT(*) FROM composition GROUP BY 1").fetchall())
    ops = conn.execute(
        "SELECT role, COALESCE(label, ''), COUNT(*) FROM composition_part WHERE role != 'base' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60"
    ).fetchall()
    mod_px: dict[str, list[float]] = defaultdict(list)
    for f, px in conn.execute(
        "SELECT c.fit, p.px16 FROM composition_part p JOIN composition c ON c.pictogram_id = p.pictogram_id WHERE p.role='modifier' AND p.px16 IS NOT NULL"
    ):
        mod_px[f].append(px)
    leg = dict(conn.execute(
        """SELECT c.fit, ROUND(AVG(r.value), 1) FROM composition c JOIN rating r ON r.pictogram_id = c.pictogram_id
           WHERE r.metric = 'legibility' AND r.is_override = 0 GROUP BY 1"""
    ).fetchall())
    return {
        "fit": fit, "font_type": font, "kind": kind, "operators": [tuple(o) for o in ops],
        "modifier_px16_median": {k: round(statistics.median(v), 1) for k, v in mod_px.items() if v},
        "legibility_by_fit": leg,
        "conflicts": conn.execute("SELECT COUNT(*) FROM composition WHERE conflict=1").fetchone()[0],
    }
```

In `vault.py`, add the Combinations section at the end of `concept_note` (before `write_note`):

```python
        from .composition.analysis import combinations

        groups = combinations(self.conn, k["id"])
        if groups:
            lines += ["## Combinations", ""]
            for key in sorted(groups):
                members = groups[key]
                lines.append(f"### {key} ({len(members)})")
                lines.append(" ".join(f"![[{self.media(m['norm_path'])}\\|24]]" for m in members[:24] if m["norm_path"]))
                lines.append(", ".join(f"`{m['original_name']}` ({m['fit']}, {m['font_type']}{', unique' if m['kind'] == 'unique' else ''})" for m in members[:24]))
                lines.append("")
```

and in `indexes`, the rules index:

```python
        from .composition.analysis import rules_summary

        rs = rules_summary(self.conn)
        doc = ["# Composition rules", "", "How composites are built, and which combinations work in one glyph.", ""]
        for title, key in (("Fit", "fit"), ("Font implementation", "font_type"), ("Generic vs unique", "kind"), ("Legibility by fit (mean)", "legibility_by_fit"), ("Modifier size at 16 px (median px) by fit", "modifier_px16_median")):
            doc += [f"## {title}", ""] + [f"- {a}: {b}" for a, b in sorted(rs[key].items(), key=lambda x: str(x[0]))] + [""]
        doc += ["## Operators", "", "| role | label | composites |", "|---|---|---|"] + [f"| {r} | {lbl} | {n} |" for r, lbl, n in rs["operators"]]
        doc += ["", f"Conflicts (name vs shape, queued for AI): {rs['conflicts']}", ""]
        write_note(self.vault / "_index" / "composition-rules.md", {"generated": db.now()}, "\n".join(doc) + "\n")
```

In `site.py` `_concept_body`, after the cluster sections:

```python
    from .composition.analysis import combinations

    groups = combinations(conn, k["id"])
    if groups:
        parts.append("<h2>Combinations</h2>")
        for key in sorted(groups):
            members = groups[key]
            imgs = "".join(
                f'<span class="tile" title="{esc(m["original_name"])} · {esc(m["fit"])} · {esc(m["font_type"])}"><img src="../svg/{Path(m["norm_path"]).stem}.svg" width="24" height="24" alt="" loading="lazy"></span>'
                for m in members[:48] if m["norm_path"]
            )
            for m in members[:48]:
                if m["norm_path"]:
                    _link(m["norm_path"], cfg.site / "svg" / f"{Path(m['norm_path']).stem}.svg")
            parts.append(f"<h3>{esc(key)} ({len(members)})</h3><div class=\"sheet\">{imgs}</div>")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition_run.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/handdown/composition/analysis.py src/handdown/vault.py src/handdown/site.py tests/test_composition_run.py
git commit -m "Composition: combinations under elements, rules index, HTML section"
```

### Task 6: AI pass for conflicts and a sample

**Files:**
- Modify: `src/handdown/ai.py` (add `run_composition`)
- Modify: `src/handdown/cli.py` (`ai --job composition`)
- Test: `tests/test_ai_composition.py` (stubs `ask`)

**Interfaces:**
- Consumes: `ai.sheet(svgs)`, `ai.ask(image, text, system, workdir) -> (dict, result)`, `ai._log_run`
- Produces: `run_composition(conn, workdir, limit=48, sample=0, log=print) -> dict`, which writes `composition.method='ai'` and replaces parts/relations for the assessed pictograms

- [ ] **Step 1: Write the failing test**

```python
import json

from handdown import ai, db
from handdown.config import Config


def test_ai_composition_overwrites_rule_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p', 'p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s', 'p', 's')")
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="9" height="9"/></svg>')
    conn.execute("INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid) VALUES (1,'s','a','bell-off',?,1)", (str(svg),))
    conn.execute("INSERT INTO composition VALUES (1,'generic','mark','glyph',1,0.5,'rules','t')")
    answer = {"1": {"parts": [{"role": "base", "label": "bell"}, {"role": "negation", "label": "slash"}],
                    "relations": [[0, 1, "crossing"]], "kind": "generic", "font_type": "mark", "fit": "glyph"}}
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "x", "total_cost_usd": 0}))
    out = ai.run_composition(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 1
    row = conn.execute("SELECT method, conflict FROM composition WHERE pictogram_id=1").fetchone()
    assert tuple(row) == ("ai", 0)
    assert json.loads(json.dumps([r[0] for r in conn.execute("SELECT role FROM composition_part ORDER BY part_no")])) == ["base", "negation"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ai_composition.py -q`
Expected: FAIL (`AttributeError: run_composition`)

- [ ] **Step 3: Write minimal implementation** (append to `ai.py`)

```python
COMPOSITION_PROMPT = """Each numbered cell shows one pictogram with its file name. Decompose it.
Roles: base, negation, frame, modifier, repetition, partner, text, decoration.
Relations between part indexes: above, below, left_of, right_of, over, under, touching, merged, cutout,
surrounds, crossing, corner:tl|tr|bl|br, sequence.
kind: generic (standard parts joined by a standard operator, separable) or unique (fused/artistic).
font_type: mark | ligature | sequence | unique.  fit: glyph | degrades | contradictory | sequence.
Reply with JSON only: {"1": {"parts": [{"role": "...", "label": "..."}], "relations": [[0, 1, "..."]],
"kind": "...", "font_type": "...", "fit": "..."}, ...}"""


def run_composition(conn: sqlite3.Connection, workdir: Path, limit: int = 48, sample: int = 0, log: Any = print) -> dict[str, Any]:
    """AI check of name/shape conflicts first, then a random sample of rule-classified composites."""
    workdir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT c.pictogram_id, p.original_name, p.norm_path FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
           WHERE c.method = 'rules' AND c.conflict = 1 LIMIT ?""", (limit,)).fetchall()
    if sample:
        rows += conn.execute(
            """SELECT c.pictogram_id, p.original_name, p.norm_path FROM composition c JOIN pictogram p ON p.id = c.pictogram_id
               WHERE c.method = 'rules' AND c.conflict = 0 ORDER BY random() LIMIT ?""", (sample,)).fetchall()
    done = 0
    cost = 0.0
    now = db.now()
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        img = sheet([Path(r["norm_path"]).read_text() for r in batch])
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
            if not isinstance(a, dict) or not a.get("parts"):
                continue
            pid = r["pictogram_id"]
            conn.execute("DELETE FROM composition_part WHERE pictogram_id=?", (pid,))
            conn.execute("DELETE FROM composition_relation WHERE pictogram_id=?", (pid,))
            conn.execute("UPDATE composition SET kind=?, font_type=?, fit=?, conflict=0, confidence=0.8, method='ai', computed_at=? WHERE pictogram_id=?",
                         (a.get("kind"), a.get("font_type"), a.get("fit"), now, pid))
            for no, part in enumerate(a["parts"]):
                conn.execute("INSERT INTO composition_part (pictogram_id, part_no, role, label) VALUES (?,?,?,?)",
                             (pid, no, part.get("role"), part.get("label")))
            for rel in a.get("relations") or []:
                if isinstance(rel, list) and len(rel) == 3:
                    conn.execute("INSERT OR REPLACE INTO composition_relation VALUES (?,?,?,?)", (pid, int(rel[0]), int(rel[1]), str(rel[2])))
            done += 1
        conn.commit()
        log(f"  batch {start // BATCH + 1}: {done} assessed, ${cost:.3f}, run {rid}")
    return {"assessed": done, "cost_usd": round(cost, 4)}
```

In `cli.py`, change the `ai` command's signature and body:

```python
@app.command()
def ai(
    limit: int = typer.Option(48, help="max items to assess in this run"),
    min_sources: int = 2,
    job: str = typer.Option("rating", help="rating | composition"),
    sample: int = typer.Option(0, help="composition: extra random sample size"),
) -> None:
    """Headless claude -p passes: cluster rating, or composition conflicts + sample."""
    from . import ai as a
    from . import score as sc

    cfg = Config()
    conn = _conn(cfg)
    if job == "composition":
        typer.echo(a.run_composition(conn, cfg.data / "ai-work", limit=limit, sample=sample, log=typer.echo))
        return
    typer.echo(a.run(conn, cfg.data / "ai-work", limit=limit, min_sources=min_sources, log=typer.echo))
    sc.combined(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ai_composition.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/handdown/ai.py src/handdown/cli.py tests/test_ai_composition.py
git commit -m "Composition: AI pass for conflicts and a sample"
```

### Task 7: Run on the catalog and record findings

- [ ] **Step 1:** `uv run pytest -q` must show all tests passing.
- [ ] **Step 2:** `uv run handdown compose --workers 2`, in the background, logged to `data/compose.log`.
- [ ] **Step 3:** `uv run handdown ai --job composition --limit 48 --sample 48` as the pilot (bounded cost); compare rule and AI answers on the sample.
- [ ] **Step 4:** `uv run handdown export vault` and `uv run handdown export html`.
- [ ] **Step 5:** Add the findings (rule precision per operator, median modifier size per fit class) to `dev/takeaways.md` and the spec's analysis section. Then commit and push (no workflow changes).
