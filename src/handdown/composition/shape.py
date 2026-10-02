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
    labels: Any = None  # connected-component labels of the render (part labels are their numbers)


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


def _is_slash(mask: np.ndarray, glyph_extent: int) -> bool:
    ys, xs = np.nonzero(mask)
    if len(ys) < 10:
        return False
    if math.hypot(np.ptp(ys), np.ptp(xs)) < 0.6 * glyph_extent:
        return False
    evals = np.linalg.eigvalsh(np.cov(np.vstack([xs, ys])))
    elongation = math.sqrt(evals[1] / max(evals[0], 1e-6))
    angle = abs(math.degrees(math.atan2(np.ptp(ys), np.ptp(xs))))
    return elongation > 5 and 30 <= angle <= 60


def _diagonal_slash(ink: np.ndarray, glyph: tuple[int, int, int, int]) -> bool:
    """A slash drawn over the base merges with it into one component. Test the
    two glyph diagonals: ink along the line, mostly background beside it.
    Thresholds tuned on the catalog (side offset 9 px at 64 px, line >= 90 %
    ink, sides <= 80 %): 69 % recall on "-off" names, 2 % false positives, so
    it only confirms a negation the name announces."""
    y0, y1, x0, x1 = glyph
    if max(y1 - y0, x1 - x0) < 16:
        return False
    t = np.linspace(0.08, 0.92, 80)
    for sx, ex in ((x0, x1 - 1), (x1 - 1, x0)):
        ys = y0 + t * (y1 - 1 - y0)
        xs = sx + t * (ex - sx)
        norm = math.hypot(ex - sx, y1 - 1 - y0) or 1
        ny, nx = (ex - sx) / norm, -(y1 - 1 - y0) / norm
        on = ink[np.round(ys).astype(int), np.round(xs).astype(int)].mean()
        sides = [
            ink[np.clip(np.round(ys + ny * off).astype(int), 0, ink.shape[0] - 1), np.clip(np.round(xs + nx * off).astype(int), 0, ink.shape[1] - 1)].mean()
            for off in (9, -9)
        ]
        if on >= 0.9 and max(sides) <= 0.8:
            return True
    return False


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
        box = filled[ys.min() : ys.max() + 1]
        return "triangle" if box[: h // 3].sum() < box[-(h // 3) :].sum() else "diamond"
    if 0.8 < extent < 0.86 and 0.9 < aspect < 1.1:
        return "octagon"
    return "other"


def _corner(p: ShapePart, glyph: tuple[int, int, int, int]) -> str | None:
    cy = (p.bbox[0] + p.bbox[1]) / 2
    cx = (p.bbox[2] + p.bbox[3]) / 2
    gy0, gy1, gx0, gx1 = glyph
    th, tw = (gy1 - gy0) / 3, (gx1 - gx0) / 3
    v = "t" if cy < gy0 + th else "b" if cy > gy1 - th else None
    h = "l" if cx < gx0 + tw else "r" if cx > gx1 - tw else None
    return f"{v}{h}" if v and h else None


def _similar(a: np.ndarray, b: np.ndarray, pa: ShapePart, pb: ShapePart) -> bool:
    ca = a[pa.bbox[0] : pa.bbox[1], pa.bbox[2] : pa.bbox[3]]
    cb = b[pb.bbox[0] : pb.bbox[1], pb.bbox[2] : pb.bbox[3]]
    h, w = min(ca.shape[0], cb.shape[0]), min(ca.shape[1], cb.shape[1])
    ca, cb = ca[:h, :w], cb[:h, :w]
    union = (ca | cb).sum()
    return bool(union) and (ca & cb).sum() / union >= 0.85


def _relation(p: ShapePart, base: ShapePart, glyph: tuple[int, int, int, int], masks: dict[str, np.ndarray]) -> str:
    if p.role_hint == "frame":
        return "surrounds"
    if p.role_hint == "negation":
        return "crossing"
    grown = ndimage.binary_dilation(masks[p.label], iterations=2)
    touches = bool((grown & masks[base.label]).any())
    if p.role_hint == "modifier":
        return f"corner:{_corner(p, glyph)}" + (":touching" if touches else ":cutout")
    if touches:
        return "touching"
    py0, py1, px0, px1 = p.bbox
    by0, by1, bx0, bx1 = base.bbox
    if py1 <= by0:
        return "above"
    if py0 >= by1:
        return "below"
    if px1 <= bx0:
        return "left_of"
    if px0 >= bx1:
        return "right_of"
    return "over"


def shape_evidence(svg: str, expect_negation: bool = False) -> ShapeEvidence:
    ink = render(svg, SIZE) > 0.5
    ev = ShapeEvidence()
    if not ink.any():
        return ev
    labels, _ = ndimage.label(ink)
    ev.labels = labels
    ys, xs = np.nonzero(ink)
    glyph = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
    glyph_extent = max(glyph[1] - glyph[0], glyph[3] - glyph[2])
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        if sl is None:
            continue
        area = int((labels[sl] == i).sum())
        if area >= 4:
            ev.parts.append(ShapePart(bbox=(sl[0].start, sl[0].stop, sl[1].start, sl[1].stop), area=area, label=str(i)))
    if not ev.parts:
        return ev
    masks = {p.label: labels == int(p.label) for p in ev.parts}
    # frame: the largest part encloses all others
    biggest = max(ev.parts, key=lambda p: (p.extent, p.area))
    if len(ev.parts) > 1:
        frame = _frame_label(masks[biggest.label], ink & ~masks[biggest.label])
        if frame:
            biggest.role_hint = "frame"
            ev.frame = frame
    # slash (only when something else is drawn: a lone diagonal is an element)
    if len(ev.parts) > 1:
        for p in ev.parts:
            if p.role_hint == "base" and _is_slash(masks[p.label], glyph_extent):
                p.role_hint = "negation"
                ev.slash = True
    if expect_negation and not ev.slash and _diagonal_slash(ink, glyph):
        # merged slash: recorded as a negation part spanning the glyph
        ev.slash = True
        ev.parts.append(ShapePart(bbox=glyph, area=0, role_hint="negation", label="merged-slash"))
        masks["merged-slash"] = np.zeros_like(ink)
    # repetition: all remaining parts are equal copies
    content = [p for p in ev.parts if p.role_hint == "base"]
    if len(content) >= 2:
        ref = max(content, key=lambda p: p.area)
        same = [p for p in content if abs(p.area - ref.area) <= 0.15 * ref.area and _similar(masks[p.label], masks[ref.label], p, ref)]
        if len(same) == len(content):
            ev.repetition = len(same)
            for p in same[1:]:
                p.role_hint = "repetition"
    # base = largest remaining content; small parts in a corner are modifiers
    content = [p for p in ev.parts if p.role_hint == "base"]
    base = max(content, key=lambda p: p.area) if content else biggest
    for p in content:
        if p is not base:
            p.role_hint = "modifier" if p.extent / (base.extent or 1) < 0.6 and _corner(p, glyph) else "partner"
    for p in ev.parts:
        p.size = size_metrics(p, base, glyph)
    index = {id(p): k for k, p in enumerate(ev.parts)}
    for p in ev.parts:
        if p is not base and p.role_hint != "repetition":
            ev.relations.append((index[id(base)], index[id(p)], _relation(p, base, glyph, masks)))
    return ev
