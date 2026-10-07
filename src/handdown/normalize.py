"""Sanitize untrusted SVG, classify its color use and produce a pure
black-and-white version with a square viewBox.

Every downloaded SVG passes through here before it is rendered or exported.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException
from PIL import ImageColor

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

ALLOWED = {
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "defs",
    "use",
    "symbol",
    "clipPath",
    "mask",
    "linearGradient",
    "radialGradient",
    "stop",
    "text",
    "tspan",
    "title",
    "desc",
    "a",
    "switch",
    "pattern",
    "marker",
}
UNWRAP = {"a", "switch"}  # keep children, drop the element
DROP_SILENT = {"metadata", "sodipodi:namedview", "namedview"}
DRAWABLE = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"}
PAINT_PROPS = ("fill", "stroke", "stop-color", "color")
OPACITY_PROPS = ("opacity", "fill-opacity", "stroke-opacity", "stop-opacity")
ALLOWED_ATTRS = {
    "id",
    "class",
    "d",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "width",
    "height",
    "points",
    "viewBox",
    "transform",
    "fill",
    "fill-rule",
    "clip-rule",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "clip-path",
    "mask",
    "href",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "gradientTransform",
    "fx",
    "fy",
    "fr",
    "spreadMethod",
    "patternUnits",
    "patternContentUnits",
    "patternTransform",
    "maskUnits",
    "maskContentUnits",
    "clipPathUnits",
    "preserveAspectRatio",
    "style",
    "color",
    "display",
    "visibility",
    "font-size",
    "font-family",
    "font-weight",
    "text-anchor",
    "dominant-baseline",
    "vector-effect",
    "paint-order",
    "markerWidth",
    "markerHeight",
    "refX",
    "refY",
    "orient",
    "marker-start",
    "marker-mid",
    "marker-end",
    "overflow",
}
URL_ATTRS = {"fill", "stroke", "clip-path", "mask", "marker-start", "marker-mid", "marker-end"}
MAX_BYTES = 2_000_000


@dataclass
class NormResult:
    svg: str
    viewbox: tuple[float, float, float, float]
    color_class: str
    color_count: int
    uses: dict[str, bool]
    removed: list[str]
    stroke_width: float | None
    stroke_caps: str | None
    fill_rule: str | None
    node_count: int
    path_count: int
    has_text: bool
    extra: dict[str, Any] = field(default_factory=dict)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_paint(value: str | None) -> tuple[int, int, int] | str | None:
    """None for no paint, "url(#id)" for references, else an RGB tuple."""
    if value is None:
        return None
    v = value.strip()
    if not v or v.lower() in ("none", "transparent"):
        return None
    if v.lower().startswith("url("):
        m = re.match(r"url\(\s*['\"]?(#[^'\")]+)['\"]?\s*\)", v)
        return f"url({m.group(1)})" if m else None
    if v.lower() in ("currentcolor", "inherit", "context-fill", "context-stroke"):
        return (0, 0, 0)
    try:
        rgb = ImageColor.getrgb(v)
    except ValueError:
        return (0, 0, 0)
    return rgb[:3]


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def parse_style(style: str) -> dict[str, str]:
    out = {}
    for decl in style.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_css(css: str) -> dict[str, dict[str, str]]:
    """Class selectors only (``.a, .b { ... }``), which is what exporters emit."""
    rules: dict[str, dict[str, str]] = {}
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decls = parse_style(body)
        for sel in selectors.split(","):
            sel = sel.strip()
            if re.fullmatch(r"\.[\w-]+", sel):
                rules.setdefault(sel[1:], {}).update(decls)
    return rules


DOCTYPE = re.compile(r"<!DOCTYPE\s+svg[^\[>]*(\[(?P<subset>.*?)\])?\s*>", re.S | re.I)
SIMPLE_ENTITY = re.compile(r"""<!ENTITY\s+(?P<name>[A-Za-z_][\w.-]*)\s+(?P<q>["'])(?P<value>[^"'&%<]*)(?P=q)\s*>""")


def _inline_entities(text: str) -> str:
    """Adobe Illustrator declares plain text entities (``<!ENTITY ns_flows
    "http://ns.adobe.com/Flows/1.0/">``) and uses them in attributes. Resolve
    those textually and drop the DOCTYPE; anything else (external, parameter
    or nested entities) stays and is refused by the parser."""
    m = DOCTYPE.search(text)
    if not m:
        return text
    subset = m.group("subset") or ""
    rest = SIMPLE_ENTITY.sub("", subset)
    if re.sub(r"<!--.*?-->", "", rest, flags=re.S).strip():
        return text  # something other than simple entities: leave it to defusedxml to refuse
    entities = {e.group("name"): e.group("value") for e in SIMPLE_ENTITY.finditer(subset)}
    if len(entities) > 64 or any(len(v) > 512 for v in entities.values()):
        return text
    body = text[: m.start()] + text[m.end() :]
    for name, value in entities.items():
        body = body.replace(f"&{name};", value)
    return body


def _parse(text: str) -> ET.Element:
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("svg too large")
    text = _inline_entities(text)
    try:
        root = DET.fromstring(text, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ET.ParseError) as e:
        raise ValueError(f"unparseable svg: {e}") from e
    if local(root.tag) != "svg":
        raise ValueError(f"root is <{local(root.tag)}>, not <svg>")
    return root


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    m = re.match(r"\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)", v, re.I)
    return float(m.group(1)) if m else None


def _viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    vb = root.get("viewBox")
    if vb:
        parts = [float(p) for p in re.split(r"[\s,]+", vb.strip()) if p]
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            return (parts[0], parts[1], parts[2], parts[3])
    w, h = _num(root.get("width")), _num(root.get("height"))
    if w and h:
        return (0.0, 0.0, w, h)
    return (0.0, 0.0, 24.0, 24.0)


def _square(vb: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = vb
    if w > h:
        return (x, y - (w - h) / 2, w, w)
    if h > w:
        return (x - (h - w) / 2, y, h, h)
    return vb


def _sanitize(root: ET.Element, removed: list[str]) -> None:
    css: dict[str, dict[str, str]] = {}
    for el in list(root.iter()):
        if local(el.tag) == "style":
            css.update(parse_css(el.text or ""))

    def walk(parent: ET.Element) -> None:
        i = 0
        while i < len(parent):
            child = parent[i]
            if not isinstance(child.tag, str):  # comments, processing instructions
                parent.remove(child)
                continue
            name = local(child.tag)
            ns_ok = child.tag.startswith("{" + SVG_NS) or "}" not in child.tag
            if not ns_ok or name not in ALLOWED:
                if name not in DROP_SILENT and name != "style" and ns_ok:
                    removed.append(name)
                elif not ns_ok and name not in DROP_SILENT:
                    removed.append(f"foreign:{name}")
                parent.remove(child)
                continue
            _clean_attrs(child, css, removed)
            walk(child)
            if name in UNWRAP:
                parent.remove(child)
                for j, grandchild in enumerate(list(child)):
                    parent.insert(i + j, grandchild)
                if name == "a":
                    removed.append(name)
                continue
            i += 1

    _clean_attrs(root, css, removed)
    walk(root)


def _clean_attrs(el: ET.Element, css: dict[str, dict[str, str]], removed: list[str]) -> None:
    # Inline class rules first; explicit attributes win over classes.
    for cls in (el.get("class") or "").split():
        for k, v in css.get(cls, {}).items():
            if k in ALLOWED_ATTRS and k not in el.attrib:
                el.set(k, v)
    if "style" in el.attrib:
        for k, v in parse_style(el.attrib.pop("style")).items():
            if k in ALLOWED_ATTRS and k != "style":
                el.set(k, v)
    for key in list(el.attrib):
        name = local(key)
        value = el.attrib[key]
        low = value.lower()
        if name.startswith("on"):
            removed.append(f"attr:{name}")
            del el.attrib[key]
        elif name == "href":
            del el.attrib[key]
            if value.startswith("#"):
                el.set("href", value)
            else:
                removed.append("href")
        elif name not in ALLOWED_ATTRS or ("}" in key and not key.startswith("{" + XLINK_NS)):
            del el.attrib[key]
        elif "javascript:" in low or "expression(" in low or "@import" in low:
            removed.append(f"attr:{name}")
            del el.attrib[key]
        elif name in URL_ATTRS and "url(" in low and not re.search(r"url\(\s*['\"]?#", low):
            removed.append(f"attr:{name}")
            del el.attrib[key]
    el.attrib.pop("class", None)


def _effective(el: ET.Element, prop: str, inherited: dict[str, str]) -> str | None:
    return el.get(prop, inherited.get(prop))


def _collect(root: ET.Element) -> dict[str, Any]:
    """Walk drawables with inherited paint and gather colour facts."""
    gradients: dict[str, list[tuple[tuple[int, int, int], float]]] = {}
    for el in root.iter():
        if local(el.tag) in ("linearGradient", "radialGradient") and el.get("id"):
            stops = []
            for s in el.iter():
                if local(s.tag) == "stop":
                    c = parse_paint(s.get("stop-color", "#000"))
                    op = _num(s.get("stop-opacity"))
                    op = 1.0 if op is None else op
                    if isinstance(c, tuple):
                        stops.append((c, op))
            gradients[el.get("id", "")] = stops

    colors: set[tuple[int, int, int]] = set()
    facts: dict[str, Any] = {
        "gradient": False,
        "partial_opacity": False,
        "stroke": False,
        "text": False,
        "clip": False,
        "mask": False,
        "transform": False,
        "use": False,
        "stroke_widths": [],
        "caps": set(),
        "fill_rules": set(),
        "nodes": 0,
        "paths": 0,
    }
    skip = {"defs", "clipPath", "mask", "symbol", "linearGradient", "radialGradient", "pattern", "marker"}

    def visit(el: ET.Element, inh: dict[str, str]) -> None:
        name = local(el.tag)
        if name in skip:
            return
        cur = dict(inh)
        for p in PAINT_PROPS + OPACITY_PROPS + ("stroke-width", "stroke-linecap", "fill-rule", "display"):
            if el.get(p) is not None:
                cur[p] = el.get(p)  # type: ignore[assignment]
        if el.get("transform"):
            facts["transform"] = True
        if el.get("clip-path"):
            facts["clip"] = True
        if el.get("mask"):
            facts["mask"] = True
        if cur.get("display") == "none":
            return
        if name in DRAWABLE:
            facts["paths"] += 1
            if name == "path":
                facts["nodes"] += len(re.findall(r"[a-df-zA-DF-Z]", el.get("d", "")))
            elif name in ("polyline", "polygon"):
                facts["nodes"] += max(1, len(re.findall(r"[-+]?\d*\.?\d+", el.get("points", ""))) // 2)
            else:
                facts["nodes"] += 4
            if name == "text":
                facts["text"] = True
            if name == "use":
                facts["use"] = True
            opacity = 1.0
            for p in ("opacity", "fill-opacity"):
                if cur.get(p) is not None:
                    opacity *= _num(cur[p]) if _num(cur[p]) is not None else 1.0  # type: ignore[operator]
            if opacity < 0.999:
                facts["partial_opacity"] = True
            for prop, default in (("fill", "#000"), ("stroke", "none")):
                paint = parse_paint(cur.get(prop, default))
                if paint is None:
                    continue
                if prop == "stroke":
                    facts["stroke"] = True
                    sw = _num(cur.get("stroke-width", "1"))
                    if sw is not None:
                        facts["stroke_widths"].append(sw)
                    if cur.get("stroke-linecap"):
                        facts["caps"].add(cur["stroke-linecap"])
                if isinstance(paint, str):
                    ref = paint[5:-1]
                    if ref in gradients:
                        facts["gradient"] = True
                        colors.update(c for c, _ in gradients[ref])
                else:
                    colors.add(paint)
            if cur.get("fill-rule"):
                facts["fill_rules"].add(cur["fill-rule"])
        for child in el:
            visit(child, cur)

    visit(root, {})
    facts["colors"] = colors
    return facts


def classify(facts: dict[str, Any]) -> tuple[str, int]:
    colors: set[tuple[int, int, int]] = facts["colors"]
    n = len(colors)
    if facts["gradient"]:
        return ("threshold" if n <= 6 else "none", n)
    if n <= 1 and not facts["partial_opacity"]:
        return ("native", n)
    if n <= 1:
        # duotone: one colour, secondary layer at reduced opacity; the light
        # layer drops out in pure black and white
        return ("derivable", n)
    if n > 6:
        return ("none", n)
    dark = {c for c in colors if luminance(c) < 0.5}
    light = colors - dark
    if len(dark) <= 1 and len(light) <= 1 and not facts["partial_opacity"]:
        # one ink colour plus white-ish knockout detail
        return ("derivable", n)
    return ("threshold", n)


def _ink_scale(colors: set[tuple[int, int, int]]) -> Callable[[float], float]:
    """Brightness as the black-and-white cut sees it. Usually absolute; but when
    even the darkest colour is light (an icon drawn in light green, or white for
    dark backgrounds), every colour would turn white and the pictogram vanish,
    so brightness counts from the darkest colour: it becomes the ink and white
    details stay knockouts."""
    darkest = min((luminance(c) for c in colors), default=0.0)
    if darkest < 0.5:
        return lambda lum: lum
    if darkest >= 0.999:  # white only: all of it is the drawing
        return lambda lum: 0.0
    return lambda lum: max(0.0, (lum - darkest) / (1 - darkest))


def _to_mono(root: ET.Element, colors: set[tuple[int, int, int]] | None = None) -> None:
    """Map every paint to pure black or white by effective luminance."""
    scale = _ink_scale(colors or set())
    grad_lum: dict[str, float] = {}
    for el in root.iter():
        if local(el.tag) in ("linearGradient", "radialGradient") and el.get("id"):
            lums = [luminance(c) for s in el.iter() if local(s.tag) == "stop" for c in [parse_paint(s.get("stop-color", "#000"))] if isinstance(c, tuple)]
            grad_lum[el.get("id", "")] = sum(lums) / len(lums) if lums else 0.0

    def mono(value: str | None, opacity: float) -> str | None:
        p = parse_paint(value)
        if p is None:
            return None
        lum = scale(grad_lum.get(p[5:-1], 0.0) if isinstance(p, str) else luminance(p))
        eff = 1 - (1 - lum) * opacity
        return "#000" if eff < 0.5 else "#fff"

    def visit(el: ET.Element, inh_opacity: float) -> None:
        op = inh_opacity
        for p in ("opacity",):
            if el.get(p) is not None:
                op *= _num(el.get(p)) if _num(el.get(p)) is not None else 1.0  # type: ignore[operator]
                del el.attrib[p]
        name = local(el.tag)
        for prop in ("fill", "stroke"):
            if el.get(prop) is not None:
                oprop = f"{prop}-opacity"
                pop = _num(el.get(oprop)) if el.get(oprop) is not None else 1.0
                el.attrib.pop(oprop, None)
                m = mono(el.get(prop), op * (pop if pop is not None else 1.0))
                el.set(prop, m if m else "none")
            elif name in DRAWABLE and prop == "fill" and op < 0.999:
                el.set("fill", mono("#000", op) or "none")
        for p in ("color", "stop-color"):
            if el.get(p) is not None:
                el.set(p, mono(el.get(p), 1.0) or "none")
        el.attrib.pop("stop-opacity", None)
        for child in el:
            visit(child, op)

    visit(root, 1.0)


def normalize(text: str) -> NormResult:
    from . import sf_symbols

    if sf_symbols.is_template(text):  # one variant, not the whole artboard of variants and notes
        text = sf_symbols.extract(text)
    root = _parse(text)
    removed: list[str] = []
    _sanitize(root, removed)
    vb = _square(_viewbox(root))
    facts = _collect(root)
    color_class, color_count = classify(facts)
    _to_mono(root, facts["colors"])

    for attr in ("width", "height", "x", "y", "style", "preserveAspectRatio"):
        root.attrib.pop(attr, None)
    root.set("viewBox", " ".join(_fmt(v) for v in vb))
    out = ET.tostring(root, encoding="unicode")
    if "xmlns=" not in out.split(">", 1)[0]:
        out = out.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)

    widths = facts["stroke_widths"]
    caps = facts["caps"]
    rules = facts["fill_rules"]
    duotone = color_count <= 1 and facts["partial_opacity"] and not facts["gradient"]
    return NormResult(
        extra={"duotone": duotone},
        svg=out,
        viewbox=vb,
        color_class=color_class,
        color_count=color_count,
        uses={k: bool(facts[k]) for k in ("gradient", "partial_opacity", "stroke", "text", "clip", "mask", "transform", "use")},
        removed=removed,
        stroke_width=max(set(widths), key=widths.count) if widths else None,
        stroke_caps=next(iter(caps)) if len(caps) == 1 else ("mixed" if caps else None),
        fill_rule=next(iter(rules)) if len(rules) == 1 else ("mixed" if rules else None),
        node_count=facts["nodes"],
        path_count=facts["paths"],
        has_text=bool(facts["text"]),
    )


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"
