"""Render-based measurements of a normalized (black-on-white) SVG.

Raw values are stored per pictogram; catalog-wide percentiles for
``simplicity`` are computed later in :mod:`handdown.score`.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import resvg_py
from PIL import Image
from scipy import ndimage
from skimage.metrics import structural_similarity
from skimage.morphology import skeletonize

METHOD_VERSION = "1"
SIZES = (8, 12, 16, 24, 32, 48, 64)


def render(svg: str, size: int) -> np.ndarray:
    """Ink coverage in [0, 1] (1 = black) at ``size`` x ``size`` pixels."""
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size, background="#ffffff", skip_system_fonts=True)
    img = Image.open(io.BytesIO(bytes(png))).convert("L")
    if img.size != (size, size):
        img = img.resize((size, size))
    return 1.0 - np.asarray(img, dtype=np.float32) / 255.0


def _up(a: np.ndarray, size: int) -> np.ndarray:
    return (
        np.asarray(
            Image.fromarray((a * 255).astype(np.uint8)).resize((size, size), Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        / 255.0
    )


def _stroke_widths(binary: np.ndarray) -> np.ndarray:
    if not binary.any():
        return np.array([0.0])
    dist = ndimage.distance_transform_edt(binary)
    skel = skeletonize(binary)
    w = 2 * dist[skel]
    return w if w.size else np.array([0.0])


def _holes(binary: np.ndarray) -> int:
    bg_labels, bg_n = ndimage.label(~binary)
    border = set(np.unique(np.concatenate([bg_labels[0], bg_labels[-1], bg_labels[:, 0], bg_labels[:, -1]])))
    min_px = max(1, binary.size // 1024)
    counts = np.bincount(bg_labels.ravel(), minlength=bg_n + 1)
    return sum(1 for i in range(1, bg_n + 1) if i not in border and counts[i] >= min_px)


def phash(a: np.ndarray) -> str:
    """256-bit average hash of a 16x16 box-downsampled ink map (near-duplicate detection)."""
    small = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((16, 16), Image.Resampling.BOX), dtype=np.float32)
    bits = (small > max(small.mean(), 8.0)).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):064x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _feature(a: np.ndarray, bbox: tuple[int, int, int, int] | None) -> list[float]:
    """Scale- and translation-normalized 16x16 ink map for shape clustering."""
    if bbox is None:
        return [0.0] * 256
    y0, y1, x0, x1 = bbox
    crop = a[y0:y1, x0:x1]
    h, w = crop.shape
    side = max(h, w)
    sq = np.zeros((side, side), dtype=np.float32)
    sq[(side - h) // 2 : (side - h) // 2 + h, (side - w) // 2 : (side - w) // 2 + w] = crop
    small = np.asarray(Image.fromarray((sq * 255).astype(np.uint8)).resize((16, 16), Image.Resampling.BOX), dtype=np.float32) / 255.0
    return [round(float(v), 3) for v in small.flatten()]


def _container(binary: np.ndarray, labels: np.ndarray, n: int) -> str:
    if n < 2:
        return "none"
    sizes = ndimage.sum(binary, labels, range(1, n + 1))
    biggest = int(np.argmax(sizes)) + 1
    outer = labels == biggest
    filled = ndimage.binary_fill_holes(outer)
    others = binary & ~outer
    if not others.any() or not (filled & others).sum() >= 0.9 * others.sum():
        return "none"
    ys, xs = np.nonzero(filled)
    bh, bw = np.ptp(ys) + 1, np.ptp(xs) + 1
    ink_ys, ink_xs = np.nonzero(binary)
    if bh < 0.9 * (np.ptp(ink_ys) + 1) or bw < 0.9 * (np.ptp(ink_xs) + 1):
        return "none"
    extent = filled.sum() / (bh * bw)
    aspect = bw / bh
    if 0.85 < aspect < 1.18 and abs(extent - math.pi / 4) < 0.06:
        return "circle"
    if extent > 0.92:
        return "square"
    if abs(extent - 0.5) < 0.08:
        return "triangle"
    return "other"


def measure(svg: str) -> dict[str, Any]:
    r = {s: render(svg, s) for s in SIZES}
    ref = r[48]
    big = r[64]
    binary = big > 0.5
    out: dict[str, Any] = {"method_version": METHOD_VERSION}

    # Legibility: how much of the 48 px image survives at small sizes.
    # Pure black and white is the target, so compare thresholded images:
    # IoU of the upscaled small render against the 48 px reference, blended
    # with SSIM so partial detail loss still registers.
    ref_bin = ref > 0.5
    curve = {}
    for s in (8, 12, 16, 24, 32):
        up = _up(r[s], 48)
        union = (ref_bin | (up > 0.5)).sum()
        iou = float((ref_bin & (up > 0.5)).sum() / union) if union else 0.0
        ssim = float(structural_similarity(up, ref, data_range=1.0))
        curve[s] = 0.7 * iou + 0.3 * max(0.0, ssim)
    ink16 = r[16][r[16] > 0.15]
    mud = float(((ink16 > 0.15) & (ink16 < 0.85)).mean()) if ink16.size else 1.0
    strokes = _stroke_widths(binary)
    if binary.any():
        ys, xs = np.nonzero(binary)
        bbox: tuple[int, int, int, int] | None = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
    else:
        bbox = None
    # Thinnest typical stroke, in 16 px pixels (median of skeleton widths).
    min_feature = float(np.median(strokes)) * 16 / 64
    # Counters (enclosed gaps) that close up at 16 px make shapes illegible.
    holes64 = _holes(binary)
    holes16 = _holes(r[16] > 0.5)
    hole_survival = min(1.0, holes16 / holes64) if holes64 else 1.0
    out["hole_survival16"] = round(hole_survival, 2)
    penalty = min(1.0, max(0.2, min_feature)) * (0.5 + 0.5 * hole_survival)
    base = 0.2 * curve[12] + 0.5 * curve[16] + 0.3 * curve[24]
    out["legibility"] = round(100 * max(0.0, base) * penalty * (1 - 0.3 * mud), 1)
    out["legibility_curve"] = {str(k): round(v, 3) for k, v in curve.items()}
    out["min_feature_px16"] = round(min_feature, 2)
    out["mud16"] = round(mud, 3)

    # Structure.
    labels, n = ndimage.label(binary)
    holes = holes64
    out["components"] = int(n)
    out["holes"] = holes
    area = float(binary.sum())
    out["figure_ground_ratio"] = round(area / binary.size, 3)
    filled_area = float(ndimage.binary_fill_holes(binary).sum())
    fill_ratio = area / filled_area if filled_area else 0.0
    out["style"] = "filled" if fill_ratio > 0.8 else "outline" if fill_ratio < 0.55 else "mixed"
    out["fill_ratio"] = round(fill_ratio, 3)
    out["container_shape"] = _container(binary, labels, n)

    # Simplicity raw measures (percentile score later).
    perimeter = float((binary ^ ndimage.binary_erosion(binary)).sum())
    out["perimetric_complexity"] = round(perimeter**2 / (4 * math.pi * area), 2) if area else 0.0
    buf = io.BytesIO()
    Image.fromarray(((1 - big) * 255).astype(np.uint8)).save(buf, "PNG", optimize=True)
    out["png_bytes64"] = buf.tell()
    edges = np.hypot(ndimage.sobel(big, 0), ndimage.sobel(big, 1))
    out["edge_density"] = round(float((edges > 0.5).mean()), 4)

    # Balance: ink centroid vs canvas centre.
    if area:
        cy, cx = ndimage.center_of_mass(big)
        d = math.hypot(cy - 31.5, cx - 31.5)
        out["balance"] = round(100 * max(0.0, 1 - d / (0.15 * 64)), 1)
        out["padding_ratio"] = round(max(bbox[1] - bbox[0], bbox[3] - bbox[2]) / 64, 3) if bbox else 0.0
    else:
        out["balance"] = 0.0
        out["padding_ratio"] = 0.0

    # Consistency: crisp on a 24 grid and even stroke widths.
    ink24 = r[24][r[24] > 0.05]
    grey24 = float(((ink24 > 0.05) & (ink24 < 0.95)).mean()) if ink24.size else 1.0
    sw = strokes[strokes > 0]
    cv = float(sw.std() / sw.mean()) if sw.size > 8 and sw.mean() else 0.0
    out["consistency"] = round(100 * (0.6 * (1 - grey24) + 0.4 * max(0.0, 1 - cv)), 1)
    out["grey24"] = round(grey24, 3)
    out["stroke_cv"] = round(cv, 3)

    # Symmetry (IoU with mirrored / rotated self).
    def iou(a: np.ndarray, b: np.ndarray) -> float:
        u = (a | b).sum()
        return float((a & b).sum() / u) if u else 1.0

    sym = {"h": iou(binary, binary[:, ::-1]), "v": iou(binary, binary[::-1, :]), "r": iou(binary, binary[::-1, ::-1])}
    out["symmetry"] = {k: v >= 0.9 for k, v in sym.items()}
    out["symmetry_iou"] = {k: round(v, 3) for k, v in sym.items()}

    out["phash"] = phash(big)
    out["feature"] = _feature(big, bbox)
    return out
