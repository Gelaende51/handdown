"""Apple SF Symbols template files: one 3300-unit artboard holding a symbol in
up to 27 weight/scale variants plus guides and annotation text. At icon size
the whole artboard renders as a speck, so the catalog keeps one variant
(Regular, medium scale where present) cropped to its ink."""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET

import numpy as np
from defusedxml.ElementTree import fromstring

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
VARIANT = re.compile(r"^(Ultralight|Thin|Light|Regular|Medium|Semibold|Bold|Heavy|Black)-(S|M|L)$")
PREFERENCE = ["Regular-M", "Regular-S", "Regular-L", "Medium-M", "Medium-S", "Light-M", "Semibold-M"]


def is_template(svg: str) -> bool:
    head = svg[:4000]
    return 'id="Symbols"' in svg and ('id="Notes"' in head or 'id="Guides"' in head) and bool(re.search(r'id="(Regular|Ultralight|Black)-[SML]"', svg))


def extract(svg: str, pad: float = 0.06) -> str:
    """The template's preferred variant alone, with the view cropped to its ink."""
    from .metrics import render

    root = fromstring(svg)
    groups = {g.get("id"): g for g in root.iter() if g.tag.rsplit("}", 1)[-1] == "g" and VARIANT.match(g.get("id") or "")}
    if not groups:
        return svg
    chosen = next((groups[k] for k in PREFERENCE if k in groups), next(iter(groups.values())))
    keep_path: set[int] = set()
    parents = {child: parent for parent in root.iter() for child in parent}
    node = chosen
    while node in parents:  # the chosen group and its ancestors (their transforms apply)
        keep_path.add(id(node))
        node = parents[node]
    out = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": root.get("viewBox", "0 0 3300 2200")})

    def copy_branch(src: ET.Element, dst: ET.Element) -> None:
        for child in src:
            if child is chosen:
                dst.append(copy.deepcopy(child))
            elif id(child) in keep_path:
                twin = ET.SubElement(dst, child.tag, dict(child.attrib))
                copy_branch(child, twin)

    copy_branch(root, out)
    x0, y0, w, h = (float(v) for v in out.get("viewBox").split())
    side = max(w, h)
    out.set("viewBox", f"{x0} {y0} {side} {side}")  # square, so pixels map straight back to user units
    text = ET.tostring(out, encoding="unicode")
    size = 1024
    ink = render(text, size) > 0.5
    if not ink.any():
        return text
    ys, xs = np.nonzero(ink)
    scale = side / size
    bx0, bx1 = x0 + xs.min() * scale, x0 + (xs.max() + 1) * scale
    by0, by1 = y0 + ys.min() * scale, y0 + (ys.max() + 1) * scale
    m = pad * max(bx1 - bx0, by1 - by0)
    out.set("viewBox", f"{bx0 - m:.1f} {by0 - m:.1f} {bx1 - bx0 + 2 * m:.1f} {by1 - by0 + 2 * m:.1f}")
    return ET.tostring(out, encoding="unicode")
