"""Cut the parts of a composite out as pictograms of their own, by rules
(docs/superpowers/specs/2026-10-02-composite-extraction-design.md).

The composite is classified as in ``run`` (same name and shape evidence), so
part numbers match the stored ``composition_part`` rows. A part is cut out
from the SVG's own elements and subpaths when they reproduce its ink region
(``vector``), else as a mask of its connected ink areas (``mask``). Parts
without ink areas of their own (announced by the name, a slash drawn into the
base, text) and parts sharing ink with them are left to the AI path."""

from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np
from defusedxml.ElementTree import fromstring
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.svgLib.path import parse_path
from PIL import Image
from scipy import ndimage

from .. import raster
from ..metrics import render
from .merge import classify
from .names import name_evidence
from .run import SIGN_DOMAINS
from .shape import SIZE, shape_evidence

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
EXTRACT = 256  # render size for masks and checks (a multiple of SIZE)
DRAWABLE = {"path", "circle", "ellipse", "rect", "polygon", "polyline", "line"}
DRAWN_OPERATORS = {"negation", "frame", "modifier", "partner", "repetition"}


@dataclass
class Extracted:
    part_no: int
    role: str
    label: str | None
    method: str | None  # vector | mask | None (not separable by rules)
    svg: str | None = None
    reason: str | None = None


class _SplitPen:
    """A fontTools pen that starts a new absolute SVG path at every moveTo."""

    def __init__(self) -> None:
        self.pens: list[SVGPathPen] = []

    def moveTo(self, pt):
        self.pens.append(SVGPathPen(None))
        self.pens[-1].moveTo(pt)

    def lineTo(self, pt):
        self.pens[-1].lineTo(pt)

    def curveTo(self, *pts):
        self.pens[-1].curveTo(*pts)

    def qCurveTo(self, *pts):
        self.pens[-1].qCurveTo(*pts)

    def closePath(self):
        self.pens[-1].closePath()

    def endPath(self):
        self.pens[-1].endPath()


def split_subpaths(d: str) -> list[str]:
    """The subpaths of path data as separate absolute paths (relative moves
    after the first depend on the previous subpath, so they are resolved)."""
    pen = _SplitPen()
    parse_path(d, pen)
    return [p.getCommands() for p in pen.pens if p.getCommands()]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pieces(root: ET.Element) -> list[tuple[int, int | None]]:
    """Drawable elements (numbered in ``data-hd``) and their subpaths."""
    out: list[tuple[int, int | None]] = []
    n = 0
    for el in root.iter():
        if _local(el.tag) not in DRAWABLE:
            continue
        el.set("data-hd", str(n))
        subs = split_subpaths(el.get("d", "")) if _local(el.tag) == "path" else []
        out += [(n, k) for k in range(len(subs))] if len(subs) > 1 else [(n, None)]
        n += 1
    return out


def _subset(root: ET.Element, keep: list[tuple[int, int | None]]) -> str:
    """The SVG with only the given pieces drawn."""
    tree = copy.deepcopy(root)
    wanted: dict[int, set[int | None]] = {}
    for el_no, sub in keep:
        wanted.setdefault(el_no, set()).add(sub)
    parents = {child: parent for parent in tree.iter() for child in parent}
    for el in list(tree.iter()):
        tag = el.get("data-hd")
        if tag is None:
            continue
        subs = wanted.get(int(tag))
        if subs is None:
            parents[el].remove(el)
            continue
        if None not in subs:  # some subpaths of a path
            parts = split_subpaths(el.get("d", ""))
            el.set("d", " ".join(parts[k] for k in sorted(subs) if k is not None and k < len(parts)))
        del el.attrib["data-hd"]
    return ET.tostring(tree, encoding="unicode")


def _crop_png(mask: np.ndarray) -> str:
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    pad = max(2, int(0.06 * max(y1 - y0, x1 - x0)))
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(mask.shape[0], y1 + pad), min(mask.shape[1], x1 + pad)
    img = Image.fromarray(np.where(mask[y0:y1, x0:x1], 0, 255).astype(np.uint8)).convert("1")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return raster.to_svg(buf.getvalue())


def extract(svg: str, name: str, tags: list[str], has_text: bool, domain: str | None) -> list[Extracted]:
    ev_name = name_evidence(name or "", tags)
    shape = shape_evidence(svg, any(p.role == "negation" for p in ev_name.parts))
    c = classify(ev_name, shape, has_text, sign_domain=domain in SIGN_DOMAINS)
    if c is None or shape.labels is None:
        return []

    def components(part: dict) -> list[int]:
        return [int(shape.parts[i].label) for i in part.get("shape_parts", []) if (shape.parts[i].label or "").isdigit()]

    # a drawn operator without ink of its own (slash drawn into the base, operator only in the name)
    # lies inside another part's ink: that part cannot be cut out cleanly
    shared = any(p["role"] in DRAWN_OPERATORS and not components(p) for p in c.parts)
    ink = render(svg, EXTRACT) > 0.5
    lab, _ = ndimage.label(ink)
    scale = EXTRACT // SIZE
    up = np.kron(shape.labels, np.ones((scale, scale), dtype=shape.labels.dtype))
    results: dict[int, Extracted] = {}
    masks: dict[int, np.ndarray] = {}
    for k, p in enumerate(c.parts):
        ids = components(p)
        if not ids:
            results[k] = Extracted(k, p["role"], p.get("label"), None, reason="no geometry")
        elif shared and p["role"] == "base":
            results[k] = Extracted(k, p["role"], p.get("label"), None, reason="shares ink with another part")
        else:
            region = np.isin(up, ids)
            keep = [label for label in np.unique(lab[region]) if label and region[lab == label].mean() > 0.5]
            masks[k] = np.isin(lab, keep)
    root = None if raster.is_raster(svg) else fromstring(svg)
    pieces = _pieces(root) if root is not None else []
    assigned: dict[int, list[tuple[int, int | None]]] = {}
    if pieces and masks:
        filled = {k: ndimage.binary_fill_holes(ndimage.binary_dilation(m, iterations=2)) for k, m in masks.items()}
        for piece in pieces:
            pm = render(_subset(root, [piece]), EXTRACT) > 0.5
            if not pm.any():
                continue
            share = {k: (pm & f).sum() / pm.sum() for k, f in filled.items()}
            best = max(share, key=lambda k: share[k])
            if share[best] >= 0.6:
                assigned.setdefault(best, []).append(piece)
    for k, m in masks.items():
        p = c.parts[k]
        if not m.any():
            results[k] = Extracted(k, p["role"], p.get("label"), None, reason="no geometry")
            continue
        if k in assigned:
            part_svg = _subset(root, assigned[k])
            got = render(part_svg, EXTRACT) > 0.5
            if (got & m).sum() / max(1, (got | m).sum()) >= 0.85:
                results[k] = Extracted(k, p["role"], p.get("label"), "vector", part_svg)
                continue
        results[k] = Extracted(k, p["role"], p.get("label"), "mask", _crop_png(m))
    return [results[k] for k in sorted(results)]


# ---- storing extracted parts and linking parts to depictions ----------------

DERIVED = "derived:composite-parts"


def _ensure_source(conn) -> None:
    conn.execute("INSERT OR IGNORE INTO platform (id, name) VALUES ('derived', 'derived')")
    conn.execute(
        """INSERT OR IGNORE INTO source (id, platform_id, name, adapter, harvest_status, notes)
           VALUES (?, 'derived', 'Parts extracted from composites', 'derived', 'harvested',
                   'each part keeps the license of the composite it was cut from (pictogram.derived_from)')""",
        (DERIVED,),
    )


def _work(args: tuple) -> tuple[int, list[Extracted] | None, str | None]:
    pid, name, tags, path, has_text, domain = args
    import json
    from pathlib import Path

    try:
        return pid, extract(Path(path).read_text(), name or "", json.loads(tags or "[]"), bool(has_text), domain), None
    except Exception as e:
        return pid, None, f"{type(e).__name__}: {e}"


def store(conn, pid: int, parts: list[Extracted], method: str = "rules", model: str = "composition.extract") -> dict[str, int]:
    """Extracted parts as pictograms of the derived source; every part of the
    composite records how (or why not) it was cut out."""
    from .. import db, provenance

    stored = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT part_no, label, concept_id FROM composition_part WHERE pictogram_id=?", (pid,))}
    if sorted(stored) != [p.part_no for p in parts]:
        conn.execute("UPDATE composition_part SET extraction='skipped: parts changed' WHERE pictogram_id=?", (pid,))
        return {"extracted": 0, "not_separable": 0, "skipped": 1}
    src, oid = conn.execute("SELECT source_id, original_id FROM pictogram WHERE id=?", (pid,)).fetchone()
    counts = {"extracted": 0, "not_separable": 0, "skipped": 0}
    for p in parts:
        label, concept_id = stored[p.part_no]
        if p.method is None:
            conn.execute("UPDATE composition_part SET extraction=? WHERE pictogram_id=? AND part_no=?", (f"not separable: {p.reason}", pid, p.part_no))
            counts["not_separable"] += 1
            continue
        concept = conn.execute("SELECT label FROM concept WHERE id=?", (concept_id,)).fetchone() if concept_id else None
        name = label or (concept[0] if concept else None) or p.role
        derived_id = f"{src}/{oid}#part{p.part_no}" + ("/ai" if method == "ai" else "")
        new = conn.execute(
            """INSERT INTO pictogram (source_id, original_id, original_name, format, derived_from, part_no, extraction, harvested_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (source_id, original_id) DO UPDATE SET original_name=excluded.original_name, format=excluded.format,
                   extraction=excluded.extraction, measured_at=NULL
               RETURNING id""",
            (DERIVED, derived_id, name, "raster" if raster.is_raster(p.svg or "") else "svg", pid, p.part_no, p.method, db.now()),
        ).fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO raw_svg VALUES (?, ?)", (new, p.svg))
        conn.execute("UPDATE composition_part SET extraction=? WHERE pictogram_id=? AND part_no=?", (p.method, pid, p.part_no))
        provenance.record(
            conn,
            [
                dict(
                    pictogram_id=pid,
                    subject="pictogram",
                    subject_id=pid,
                    field="part",
                    value=derived_id,
                    raw=name,
                    method=method,
                    model=model,
                    input="image",
                    context={"part_no": p.part_no, "role": p.role, "extraction": p.method},
                )
            ],
        )
        counts["extracted"] += 1
    return counts


def run(conn, cfg, workers: int = 1, limit: int | None = None, log=print) -> dict[str, int]:
    """Cut out the parts of every rule-classified composite not done yet."""
    import multiprocessing as mp

    from .. import db

    _ensure_source(conn)
    rows = conn.execute(
        """SELECT p.id, p.original_name, p.raw_tags, p.norm_path, p.has_text, s.domain
           FROM composition c JOIN pictogram p ON p.id = c.pictogram_id JOIN source s ON s.id = p.source_id
           WHERE c.method = 'rules' AND p.source_id != ? AND p.norm_path IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM composition_part cp WHERE cp.pictogram_id = c.pictogram_id AND cp.extraction IS NOT NULL)
           ORDER BY p.id LIMIT ?""",
        (DERIVED, limit or -1),
    ).fetchall()
    tasks = [(r[0], r[1], r[2], str(cfg.resolve(r[3])), r[4], r[5]) for r in rows]
    counts = {"composites": 0, "parts": 0, "extracted": 0, "not_separable": 0, "skipped": 0}
    pool = mp.get_context("forkserver").Pool(workers) if workers > 1 else None
    try:
        results = pool.imap_unordered(_work, tasks, chunksize=16) if pool else map(_work, tasks)
        for n, (pid, parts, err) in enumerate(results, 1):
            if parts is None:
                db.log_error(conn, "composition", str(pid), "extract", err or "")
                conn.execute("UPDATE composition_part SET extraction='skipped: error' WHERE pictogram_id=?", (pid,))
                counts["skipped"] += 1
                continue
            counts["composites"] += 1
            counts["parts"] += len(parts)
            for k, v in store(conn, pid, parts).items():
                counts[k] += v
            if n % 2000 == 0:
                conn.commit()
                log(f"  {n}/{len(tasks)} {counts}")
        conn.commit()
    finally:
        if pool:
            pool.close()
    return counts


def link_parts(conn) -> dict[str, int]:
    """Each composite part to the depiction it shows: the depiction of its
    extracted pictogram (the AI extraction first), else the largest depiction
    of the part's concept."""
    conn.execute("UPDATE composition_part SET depiction_id = NULL")
    n_ext = conn.execute(
        """UPDATE composition_part SET depiction_id = (
               SELECT g.depiction_id FROM pictogram d JOIN style_member m ON m.pictogram_id = d.id JOIN style_group g ON g.id = m.style_group_id
               WHERE d.derived_from = composition_part.pictogram_id AND d.part_no = composition_part.part_no
               ORDER BY d.extraction = 'ai' DESC LIMIT 1)
           WHERE EXISTS (SELECT 1 FROM pictogram d JOIN style_member m ON m.pictogram_id = d.id
                         WHERE d.derived_from = composition_part.pictogram_id AND d.part_no = composition_part.part_no)"""
    ).rowcount
    n_con = conn.execute(
        """UPDATE composition_part SET depiction_id = (
               SELECT id FROM depiction WHERE object_id = composition_part.concept_id ORDER BY size DESC LIMIT 1)
           WHERE depiction_id IS NULL AND concept_id IS NOT NULL
             AND EXISTS (SELECT 1 FROM depiction WHERE object_id = composition_part.concept_id)"""
    ).rowcount
    conn.commit()
    return {"from_extraction": n_ext, "from_concept": n_con}
