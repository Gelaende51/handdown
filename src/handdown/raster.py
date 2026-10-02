"""Pixel pictograms (docs/superpowers/specs/2026-10-02-raster-pictograms-design.md).

A raster image is wrapped in a minimal SVG with the image as a data URI, so
everything that renders SVG (resvg) handles it unchanged. The wrapper of the
original is the raw copy; ``monochrome`` derives the 1-bit pictogram."""

from __future__ import annotations

import base64
import io
import re

import numpy as np
from PIL import Image

from .normalize import NormResult

MAX_SIDE = 1024  # originals are kept up to this size
PIXEL_ART = 64  # up to this size, render without smoothing
MARKER = 'data-handdown="raster"'
USES = ("gradient", "partial_opacity", "stroke", "text", "clip", "mask", "transform", "use")


def icon_like(w: int, h: int) -> bool:
    """Plausibly an icon: not a spacer, banner or screenshot."""
    return min(w, h) >= 12 and max(w, h) <= 1600 and max(w, h) <= 2 * min(w, h)


def _wrap(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    w, h = img.size
    style = ' style="image-rendering:pixelated"' if max(w, h) <= PIXEL_ART else ""
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return f'<svg xmlns="http://www.w3.org/2000/svg" {MARKER} viewBox="0 0 {w} {h}"><image width="{w}" height="{h}"{style} href="{uri}"/></svg>'


def to_svg(data: bytes) -> str:
    """The original as a wrapper: PNG, first frame of an animation, the largest
    image of an icon file, at most ``MAX_SIDE`` px on the long side."""
    img = Image.open(io.BytesIO(data))
    img.seek(0)
    img = img.convert("RGBA")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return _wrap(img)


def is_raster(svg: str) -> bool:
    return MARKER in svg[:200]


def decode(svg: str) -> Image.Image:
    m = re.search(r'href="data:image/png;base64,([^"]+)"', svg)
    if not m:
        raise ValueError("no embedded PNG")
    return Image.open(io.BytesIO(base64.b64decode(m.group(1))))


def _otsu(lum: np.ndarray) -> float:
    from skimage.filters import threshold_otsu

    return float(threshold_otsu(lum)) if lum.max() - lum.min() > 1 else 128.0


def monochrome(raw: str) -> NormResult:
    """The 1-bit pictogram of a raster original: composited on white,
    thresholded (Otsu). A shape held only by the alpha channel (a light glyph
    for dark themes) is taken from the alpha mask. Dark full-frame backgrounds
    stay as they are: a sign frame carries meaning."""
    img = decode(raw).convert("RGBA")
    rgba = np.asarray(img, dtype=np.float32)
    alpha = rgba[..., 3] / 255.0
    rgb = rgba[..., :3] * alpha[..., None] + 255.0 * (1 - alpha[..., None])
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    ink = lum < _otsu(lum)
    if alpha.min() < 0.98:
        shape = alpha > 0.5
        if shape.mean() > 0.02 and ink.mean() < 0.2 * shape.mean():
            ink = shape
    mono = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("1")
    opaque = alpha > 0.5
    colours = rgb[opaque] if opaque.any() else rgb.reshape(-1, 3)
    saturated = float((colours.max(axis=-1) - colours.min(axis=-1) > 24).mean())
    mid = float(((lum > 48) & (lum < 208)).mean())
    count = len(np.unique((colours // 8).astype(np.uint8).reshape(-1, 3), axis=0))
    w, h = img.size
    return NormResult(
        svg=_wrap(mono),
        viewbox=(0.0, 0.0, float(w), float(h)),
        color_class="native" if saturated < 0.02 and mid < 0.05 else "threshold",
        color_count=int(count),
        uses={**dict.fromkeys(USES, False), "raster": True},
        removed=[],
        stroke_width=None,
        stroke_caps=None,
        fill_rule=None,
        node_count=0,
        path_count=0,
        has_text=False,
        extra={"size": [w, h], "threshold_ink": round(float(ink.mean()), 3)},
    )


def render_rgb(svg: str, size: int) -> Image.Image:
    """A colour render on white (the classifiers' view of an original)."""
    import resvg_py

    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size, background="#ffffff", skip_system_fonts=True)
    img = Image.open(io.BytesIO(bytes(png))).convert("RGB")
    return img if img.size == (size, size) else img.resize((size, size))
