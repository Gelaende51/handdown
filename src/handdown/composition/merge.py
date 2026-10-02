"""Combine name and shape evidence into one classified composition."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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


def classify(name: NameEvidence, shape: ShapeEvidence, has_text: bool, sign_domain: bool = False) -> Composition | None:
    shape = replace(shape, parts=[replace(p) for p in shape.parts], relations=list(shape.relations))  # never mutate the caller's evidence
    name_roles = {(p.role, p.label) for p in name.parts}
    named_modifier = any(r == "modifier" for r, _ in name_roles)
    named_frame = any(r == "frame" for r, _ in name_roles)
    named_repetition = any(r == "repetition" for r, _ in name_roles)
    has_text = has_text or name.text
    # Multi-word names are mostly compounds or qualifiers ("music notes",
    # "arrow up"); two elements need a connector word and a separate part.
    wants_partners = len(name.base) >= 2 and name.connector
    partners_by_name = wants_partners and any(p.role_hint == "partner" for p in shape.parts)
    # Connected components are not elements (user = head + body, "i" = dot +
    # stem), and an outline with interior detail is not a frame (donut, moon).
    # Shape-only modifiers/partners need the name; shape-only frames need the
    # name or a sign source (where frames are sign classes and names are codes).
    for p in shape.parts:
        if (
            (p.role_hint == "modifier" and not named_modifier)
            or (p.role_hint == "partner" and not wants_partners)
            or (p.role_hint == "frame" and not (named_frame or sign_domain))
            or (p.role_hint == "repetition" and not (named_repetition or sign_domain))
        ):
            p.role_hint = "base"
    if not any(p.role_hint == "frame" for p in shape.parts):
        shape.frame = None
    if not any(p.role_hint == "repetition" for p in shape.parts):
        shape.repetition = 1
    shape_ops = [p.role_hint for p in shape.parts if p.role_hint != "base"]
    if not name_roles and not shape_ops and not partners_by_name and not has_text:
        return None  # a single element
    c = Composition()
    index: dict[int, int] = {}  # shape part -> composition part (all base components share one entry)
    for i, p in enumerate(shape.parts):
        if p.role_hint == "base" and "base" in (e["role"] for e in c.parts):
            index[i] = next(k for k, e in enumerate(c.parts) if e["role"] == "base")
            c.parts[index[i]]["shape_parts"].append(i)
            continue
        label = shape.frame if p.role_hint == "frame" else "slash" if p.role_hint == "negation" else None
        index[i] = len(c.parts)
        # shape_parts: which shape parts (connected ink areas) make up this part, for extraction
        c.parts.append({"role": p.role_hint, "label": label, "size": p.size, "position": None, "count": 1, "shape_parts": [i]})
    if shape.repetition > 1 and c.parts:
        c.parts[0]["count"] = shape.repetition
    shape_role_set = {p["role"] for p in c.parts}
    name_only = []
    for role, label in sorted(name_roles):
        if role in shape_role_set:
            for p in c.parts:  # name gives the label the shape could not
                if p["role"] == role and not p["label"]:
                    p["label"] = label
        else:
            entry = {"role": role, "label": label, "size": {}, "position": None, "count": 1, "from_name": True}
            c.parts.append(entry)
            name_only.append(entry)
    if has_text:
        c.parts.append({"role": "text", "label": "text", "size": {}, "position": None, "count": 1})
    c.relations = sorted({(index[a], index[b], rel) for a, b, rel in shape.relations if a in index and b in index and index[a] != index[b]})
    fused = not shape_ops  # the name announces operators the drawing does not separate
    # A negation the shape does not confirm is a conflict (the slash detector
    # misses about a third); other announced operators on one fused shape
    # mean the parts are merged into a unique drawing.
    c.conflict = any(p["role"] == "negation" for p in name_only) or (bool(name_only) and not fused)
    c.kind = "unique" if name_only and fused and not c.conflict else "generic"
    c.confidence = 0.9 if (name_roles and shape_ops and not c.conflict) else 0.6
    roles = [p["role"] for p in c.parts]
    if c.kind == "unique":
        c.font_type = "unique"
    elif "partner" in roles or partners_by_name:
        c.font_type = "sequence"
    elif set(roles) - {"base"} <= {"negation", "frame"}:
        c.font_type = "mark"
    else:
        c.font_type = "ligature"
    c.fit = _fit(c, roles, name.base)
    return c


FRAME_WORDS = {"circle", "square", "triangle", "octagon", "diamond", "shield", "hexagon", "box", "rectangle"}


def _fit(c: Composition, roles: list[str], base_words: list[str] | None = None) -> str:
    bare_frame = bool(base_words) and base_words[0] in FRAME_WORDS and len(base_words) == 1
    if roles.count("negation") > 1 or ("negation" in roles and (bare_frame or ("frame" in roles and "base" not in roles))):
        return "contradictory"
    modifiers = [p for p in c.parts if p["role"] == "modifier"]
    labels = {p["label"] for p in modifiers}
    if {"check", "x"} <= labels or roles.count("frame") > 1:
        return "contradictory"
    if "partner" in roles:
        return "sequence" if roles.count("partner") >= 2 else "degrades"
    operators = {r for r in roles if r != "base"}
    if len(modifiers) > 1 or len(operators) >= 3 or "text" in roles:
        return "degrades"
    if any(p["size"].get("px16", 99) < MIN_MODIFIER_PX16 for p in modifiers):
        return "degrades"
    if any(p.get("count", 1) > 3 for p in c.parts):
        return "degrades"
    return "glyph"
