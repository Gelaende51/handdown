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
    shape_ops = [p.role_hint for p in shape.parts if p.role_hint != "base"]
    partners_by_name = len(name.base) >= 2
    if not name_roles and not shape_ops and not partners_by_name and not has_text:
        return None  # a single element
    c = Composition()
    for p in shape.parts:
        label = shape.frame if p.role_hint == "frame" else "slash" if p.role_hint == "negation" else None
        c.parts.append({"role": p.role_hint, "label": label, "size": p.size, "position": None, "count": 1})
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
    c.relations = list(shape.relations)
    fused = len(shape.parts) <= 1
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
    c.fit = _fit(c, roles)
    return c


def _fit(c: Composition, roles: list[str]) -> str:
    if roles.count("negation") > 1 or ("negation" in roles and "frame" in roles and "base" not in roles):
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
