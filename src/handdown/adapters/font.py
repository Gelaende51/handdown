"""Glyphs of monochrome fonts (symbol, emoji, pictographic-script and music
fonts) extracted to SVG with fontTools. Names come from the Unicode
character database, falling back to the glyph name."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import httpx
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

MAX_FONT = 60 * 1024 * 1024


class FontAdapter:
    name = "font"
    min_items = 10

    def __init__(self, cfg: Config, conn: sqlite3.Connection):
        self.cfg = cfg
        self.conn = conn
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted",)) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='font' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(
                id=r["id"],
                name=r["name"],
                platform_id=r["platform_id"],
                url=r["url"],
                license_spdx=r["license_spdx"],
                domain=r["domain"],
                extra=json.loads(r["adapter_args"] or "{}"),
            )

    def _download(self, source_id: str, url: str) -> Path:
        dest = self.cfg.raw / "fonts" / (re.sub(r"[^\w.-]+", "_", source_id) + Path(url).suffix.split("?")[0])
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_bytes(1 << 20):
                data += chunk
                if len(data) > MAX_FONT:
                    raise ValueError("font too large")
        dest.write_bytes(data)
        return dest

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        path = self._download(source_id, args["url"])
        font = TTFont(path, lazy=True)
        glyphs = font.getGlyphSet()
        upm = font["head"].unitsPerEm
        ranges = [tuple(int(x, 16) for x in r.split("-")) for r in args.get("ranges", [])]
        seen: set[str] = set()
        n = 0
        rel = str(path.relative_to(self.cfg.root)) if path.is_relative_to(self.cfg.root) else str(path)
        for cp, gname in sorted(font.getBestCmap().items()):
            if ranges and not any(lo <= cp <= hi for lo, hi in ranges):
                continue
            if gname in seen or unicodedata.category(chr(cp)) in ("Zs", "Cc", "Cf"):
                continue
            seen.add(gname)
            glyph = glyphs[gname]
            bp = BoundsPen(glyphs)
            glyph.draw(bp)
            if bp.bounds is None:
                continue  # empty glyph (space, format character)
            pen = SVGPathPen(glyphs)
            glyph.draw(pen)
            d = pen.getCommands()
            # Frame by the drawn bounds, not the line box: pictographic-script
            # and music fonts place glyphs far outside ascent/descent.
            x0, y0, x1, y1 = bp.bounds
            pad = 0.08 * max(x1 - x0, y1 - y0, upm * 0.05)
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0 - pad:g} {-y1 - pad:g} {x1 - x0 + 2 * pad:g} {y1 - y0 + 2 * pad:g}">'
                f'<path transform="scale(1 -1)" d="{d}"/></svg>'
            )
            try:
                uname = unicodedata.name(chr(cp)).lower()
            except ValueError:
                uname = gname
            n += 1
            yield Item(
                original_id=f"U+{cp:04X}",
                name=uname,
                svg=svg,
                format="glyph",
                raw_path=f"{rel}#{gname}",
                unicode_codepoint=f"U+{cp:04X}",
                tags=[gname] if gname != uname else [],
            )
        self.meta[source_id] = {"glyphs": n}
