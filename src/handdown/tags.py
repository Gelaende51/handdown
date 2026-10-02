"""Standardised tags for filtering: one vocabulary across measurements, name
rules and Claude's free-text features (``feature:badge``, ``frame:circle``,
``direction:left``, ``corners:rounded``, ``style:outline`` …).

Namespaces: style, frame, symmetry, mirror-safe, ends, corners, format,
color, view, direction, feature. Free text is reduced to a feature by
dropping size and filler words, merging synonyms and singularising."""

from __future__ import annotations

import json
import math
import re
from itertools import pairwise
from typing import Any

DIRECTIONS = {"points-left": "left", "points-right": "right", "points-up": "up", "points-down": "down", "facing-left": "left", "facing-right": "right"}
FILLER = {
    "small", "large", "big", "tiny", "little", "mini", "icon", "icons", "symbol", "sign", "shape", "duplicate", "variant", "version",
    "simple", "stylized", "stylised", "solid", "filled", "thin", "thick", "bold", "the", "a", "an", "of", "with",
}  # fmt: skip
FRAME_WORDS = {"frame", "outline", "background", "border", "container", "enclosure", "badge-frame"}
FRAME_SHAPES = {"circle": "circle", "circular": "circle", "round": "circle", "square": "square", "rectangle": "square", "triangle": "triangle",
                "triangular": "triangle", "octagon": "octagon", "diamond": "diamond", "shield": "shield", "hexagon": "hexagon"}  # fmt: skip
SYNONYMS = {
    "x": "cross", "x mark": "cross", "cross mark": "cross", "multiplication": "cross", "ex": "cross",
    "checkmark": "check", "check mark": "check", "tick": "check", "tick mark": "check",
    "diagonal slash": "slash", "diagonal line": "slash", "strike": "slash", "strikethrough": "slash",
    "plus sign": "plus", "plus": "plus", "minus sign": "minus", "minus": "minus",
    "exclamation mark": "exclamation", "exclamation point": "exclamation", "question mark": "question",
    "dots": "dot", "lines": "line", "badge": "badge", "notification badge": "badge",
}  # fmt: skip


def _singular(word: str) -> str:
    from .concepts import wordnet

    return wordnet().morphy(word, "n") or word


def canon_feature(text: str | None) -> str | None:
    """A free-text feature or name variety as a tag, or None."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in DIRECTIONS:
        return f"direction:{DIRECTIONS[t]}"
    words = re.findall(r"[a-z0-9]+", t)
    if {"rounded", "round"} & set(words) and {"corner", "corners", "edges"} & set(words):
        return "corners:rounded"
    if {"sharp", "pointy", "pointed"} & set(words) and {"corner", "corners", "edges", "tip", "tips"} & set(words):
        return "corners:sharp"
    if FRAME_WORDS & set(words):
        shapes = [FRAME_SHAPES[w] for w in words if w in FRAME_SHAPES]
        if shapes:
            return f"frame:{shapes[-1]}"
    phrase = " ".join(words)
    if phrase in SYNONYMS:
        return f"feature:{SYNONYMS[phrase]}"
    kept = [w for w in words if w not in FILLER]
    if not kept:
        return None
    phrase = " ".join(kept)
    if phrase in SYNONYMS:
        return f"feature:{SYNONYMS[phrase]}"
    kept[-1] = _singular(kept[-1])
    phrase = " ".join(kept)
    return f"feature:{SYNONYMS.get(phrase, phrase)}"


def _length(value: str | None) -> float:
    """An SVG length ("4", "4px", "15%"), 0 when absent or unreadable."""
    m = re.match(r"\s*(-?\d*\.?\d+)", value or "")
    return float(m.group(1)) if m else 0.0


def _turn(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Turning angle in degrees between two directions."""
    la, lb = math.hypot(*a), math.hypot(*b)
    if not la or not lb:
        return 0.0
    cos = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
    return math.degrees(math.acos(cos))


def corner_style(svg: str, sharp_turn: float = 35.0) -> str | None:
    """rounded | sharp | mixed, from the vector outlines: sharp corners are
    junctions where the outline turns by more than ``sharp_turn`` degrees;
    curves and arcs (fillets, circles, rounded rects) count as rounded."""
    from defusedxml.ElementTree import fromstring
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.svgLib.path import parse_path

    try:
        root = fromstring(svg)
    except Exception:
        return None
    sharp = curves = 0
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("circle", "ellipse"):
            curves += 4
        elif tag == "rect":
            if _length(el.get("rx")) or _length(el.get("ry")):
                curves += 4
            else:
                sharp += 4
        elif tag in ("polygon", "polyline"):
            nums = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", el.get("points", ""))]
            pts = list(zip(nums[::2], nums[1::2], strict=False))
            if tag == "polygon" and pts:
                pts.append(pts[0])
            dirs = [(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(pts)]
            if tag == "polygon" and dirs:
                dirs.append(dirs[0])
            sharp += sum(_turn(a, b) > sharp_turn for a, b in pairwise(dirs))
        elif tag == "path" and el.get("d"):
            pen = RecordingPen()
            try:
                parse_path(el.get("d"), pen)
            except Exception:
                continue
            s, c = _path_corners(pen.value, sharp_turn)
            sharp += s
            curves += c
    if not sharp and not curves:
        return None
    if not sharp:
        return "rounded"
    if not curves:
        return "sharp"
    return "rounded" if curves >= 3 * sharp else "sharp" if sharp >= 3 * curves else "mixed"


def _path_corners(ops: list, sharp_turn: float) -> tuple[int, int]:
    """(sharp corners, curve segments) of recorded pen operations."""
    sharp = curves = 0
    start = cur = None
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []  # (direction in, direction out) per segment

    def close_contour() -> None:
        nonlocal sharp
        if len(segs) > 1:
            pairs = [*pairwise(segs), (segs[-1], segs[0])]
            sharp += sum(_turn(a[1], b[0]) > sharp_turn for a, b in pairs)

    for op, pts in ops:
        if op == "moveTo":
            close_contour()
            segs = []
            start = cur = pts[0]
        elif op == "lineTo" and cur is not None:
            d = (pts[0][0] - cur[0], pts[0][1] - cur[1])
            if d != (0, 0):
                segs.append((d, d))
            cur = pts[0]
        elif op in ("curveTo", "qCurveTo") and cur is not None:
            p = [cur, *pts]
            d_in = next(((b[0] - p[0][0], b[1] - p[0][1]) for b in p[1:] if b != p[0]), (0, 0))
            d_out = next(((p[-1][0] - a[0], p[-1][1] - a[1]) for a in reversed(p[:-1]) if a != p[-1]), (0, 0))
            segs.append((d_in, d_out))
            curves += 1
            cur = pts[-1]
        elif op == "closePath" and cur is not None and start is not None:
            if cur != start:
                d = (start[0] - cur[0], start[1] - cur[1])
                segs.append((d, d))
            cur = start
    close_contour()
    return sharp, curves


def pictogram_tags(row: dict[str, Any], corners: str | None = None, view: str | None = None, varieties: list[str] | None = None) -> set[str]:
    """Tags of one pictogram from its measurements, its corner style and its depiction's view and varieties."""
    out: set[str] = set()
    if row.get("style"):
        out.add(f"style:{row['style']}")
    if row.get("container_shape") not in (None, "none", "other"):
        out.add(f"frame:{row['container_shape']}")
    sym = json.loads(row.get("symmetry") or "{}") if isinstance(row.get("symmetry"), str) else (row.get("symmetry") or {})
    out |= {f"symmetry:{name}" for key, name in (("h", "horizontal"), ("v", "vertical"), ("r", "rotational")) if sym.get(key)}
    if row.get("mirror_safe"):
        out.add("mirror-safe")
    if row.get("stroke_caps") in ("round", "square", "butt"):
        out.add(f"ends:{row['stroke_caps']}")
    if row.get("format"):
        out.add(f"format:{row['format']}")
    if row.get("color_class"):
        out.add(f"color:{row['color_class']}")
    if corners:
        out.add(f"corners:{corners}")
    if view and view != "unknown":
        out.add(f"view:{view}")
    for v in varieties or []:
        t = canon_feature(v)
        if t:
            out.add(t)
    return out


ROLE_TAGS = {"negation": "feature:slash", "repetition": "feature:repetition", "text": "feature:text"}


def _corners(args: tuple[int, str | None]) -> tuple[int, str | None]:
    pid, path = args
    if not path:
        return pid, None
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return pid, None
    return pid, None if 'data-handdown="raster"' in svg[:200] else corner_style(svg)


def build(conn, cfg, workers: int = 2, min_count: int = 20, chunk: int = 20000, log: Any = print) -> dict[str, int]:
    """Tag every unique, on-topic pictogram (rebuilds ``pictogram_tag`` and ``tag_count``)."""
    import multiprocessing as mp
    from collections import Counter, defaultdict

    depiction = {}
    for pid, view, varieties in conn.execute(
        "SELECT m.pictogram_id, d.view, d.varieties FROM style_member m JOIN style_group g ON g.id = m.style_group_id JOIN depiction d ON d.id = g.depiction_id"
    ):
        depiction[pid] = (view, json.loads(varieties or "[]"))
    parts: dict[int, set[str]] = defaultdict(set)
    for pid, role, label in conn.execute("SELECT pictogram_id, role, label FROM composition_part"):
        tag = ROLE_TAGS.get(role) or (f"frame:{FRAME_SHAPES[label]}" if role == "frame" and label in FRAME_SHAPES else None)
        if tag:
            parts[pid].add(tag)
    described: dict[int, set[str]] = defaultdict(set)  # Claude's description pass (describe.py)
    for pid, tags_json in conn.execute("SELECT pictogram_id, tags FROM pictogram_description"):
        described[pid] |= {"described", *json.loads(tags_json or "[]")}
    for pid, script in conn.execute("SELECT pictogram_id, script FROM pictogram_text"):
        described[pid] |= {"feature:text"} | ({f"text:{script}"} if script else set())
    conn.execute("DELETE FROM pictogram_tag")
    conn.execute("DELETE FROM tag_count")
    pool = mp.get_context("forkserver").Pool(workers) if workers > 1 else None
    staged: dict[int, set[str]] = {}
    last, n = 0, 0
    try:
        while True:
            rows = conn.execute(
                """SELECT id, norm_path, style, container_shape, symmetry, mirror_safe, stroke_caps, format, color_class FROM pictogram
                   WHERE id > ? AND duplicate_of IS NULL AND svg_valid = 1 AND topic IS NULL ORDER BY id LIMIT ?""",
                (last, chunk),
            ).fetchall()
            if not rows:
                break
            last = rows[-1][0]
            cols = ("id", "norm_path", "style", "container_shape", "symmetry", "mirror_safe", "stroke_caps", "format", "color_class")
            dicts = {r[0]: dict(zip(cols, r, strict=True)) for r in rows}
            tasks = [(pid, str(cfg.resolve(d["norm_path"])) if d["norm_path"] else None) for pid, d in dicts.items()]
            results = pool.imap_unordered(_corners, tasks, chunksize=200) if pool else map(_corners, tasks)
            for pid, corners in results:
                view, varieties = depiction.get(pid, (None, []))
                staged[pid] = pictogram_tags(dicts[pid], corners, view, varieties) | parts.get(pid, set()) | described.get(pid, set())
            n += len(rows)
            log(f"  {n} pictograms")
    finally:
        if pool:
            pool.close()
    counts = Counter(t for ts in staged.values() for t in ts)
    from .hierarchy.names import VARIETY_WORDS

    standard = set(ROLE_TAGS.values()) | {f"feature:{v}" for v in [*SYNONYMS.values(), *VARIETY_WORDS.values()]}
    standard |= {t for ts in described.values() for t in ts}  # chosen from the vocabulary by Claude
    keep = {t for t, k in counts.items() if not t.startswith("feature:") or t in standard or k >= min_count}  # rare free text stays out
    conn.executemany("INSERT INTO pictogram_tag (tag, pictogram_id) VALUES (?, ?)", ((t, pid) for pid, ts in staged.items() for t in ts if t in keep))
    conn.executemany("INSERT INTO tag_count VALUES (?, ?)", ((t, counts[t]) for t in keep))
    conn.commit()
    return {"pictograms": len(staged), "tags": len(keep), "dropped": len(counts) - len(keep)}
