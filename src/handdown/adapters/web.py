"""Web pages without an API (docs/superpowers/specs/2026-10-02-raster-pictograms-design.md).

A conservative crawl: from the start pages it follows links that stay on the
same site under the start path, up to ``depth`` and ``max_pages``, and takes
SVG, PNG and GIF files and zip archives of them (opened, their images taken).
Logos, favicons and banners are left out. Adapter args:
``start`` (list of pages or files, default the source URL), ``depth`` (2), ``max_pages`` (300),
``max_items`` (20000), ``scope`` (path prefix, default the start path),
``exclude`` (regex for assets to skip)."""

from __future__ import annotations

import io
import json
import re
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from PIL import Image

from .. import db, raster
from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

ASSET = re.compile(r"\.(svg|png|gif|zip)$", re.I)
EXCLUDE = r"(logo|favicon|banner|sprite|button|avatar|header|footer|social|share|flag-icon)"
MAX_ASSET = 20 * 1024 * 1024  # zips can hold a whole set


class WebAdapter:
    name = "web"
    min_items = 1

    def __init__(self, cfg: Config, conn: sqlite3.Connection, delay: float = 0.5):
        self.cfg = cfg
        self.conn = conn
        self.delay = delay
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted",)) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='web' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(
                id=r["id"],
                name=r["name"],
                platform_id=r["platform_id"],
                url=r["url"],
                license_spdx=r["license_spdx"],
                extra=json.loads(r["adapter_args"] or "{}"),
            )

    def _get(self, source_id: str, url: str) -> httpx.Response | None:
        try:
            r = self.client.get(url)
            time.sleep(self.delay)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            db.log_error(self.conn, source_id, url, "harvest", repr(e))
            return None

    @staticmethod
    def _links(base: str, html: str) -> list[str]:
        found = re.findall(r"""(?:href|src)\s*=\s*["']([^"'#]+)["']""", html, re.I)
        return [urldefrag(urljoin(base, u))[0] for u in found]

    def _item(self, url: str, name: str, data: bytes) -> Item | None:
        ext = name.rsplit(".", 1)[-1].lower()
        stem = PurePosixPath(name).stem
        if ext == "svg":
            try:
                return Item(original_id=url, name=stem, svg=data.decode("utf-8"), url=url.split("#")[0])
            except UnicodeDecodeError:
                return None
        try:
            with Image.open(io.BytesIO(data)) as img:
                if not raster.icon_like(*img.size):
                    return None
            return Item(original_id=url, name=stem, svg=raster.to_svg(data), format="raster", url=url.split("#")[0])
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT url, adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        starts = args.get("start") or [row["url"]]
        host = urlparse(starts[0]).netloc
        scope = args.get("scope") or urlparse(starts[0]).path.rsplit("/", 1)[0] + "/"
        exclude = re.compile(args.get("exclude", EXCLUDE), re.I)
        depth, max_pages, max_items = int(args.get("depth", 2)), int(args.get("max_pages", 300)), int(args.get("max_items", 20000))
        seen: set[str] = set(starts)
        assets: list[str] = [u for u in starts if ASSET.search(urlparse(u).path)]  # files named directly
        queue = [(u, 0) for u in starts if u not in assets]
        pages = 0
        while queue and pages < max_pages:
            url, d = queue.pop(0)
            r = self._get(source_id, url)
            pages += 1
            if r is None or "html" not in r.headers.get("content-type", "html"):
                continue
            for link in self._links(url, r.text):
                p = urlparse(link)
                if link in seen or p.netloc != host:
                    continue
                seen.add(link)
                if ASSET.search(p.path):
                    if not exclude.search(PurePosixPath(p.path).name):
                        assets.append(link)
                elif d < depth and p.path.startswith(scope) and p.scheme in ("http", "https"):
                    queue.append((link, d + 1))
        n = 0
        for url in assets:
            if n >= max_items:
                break
            r = self._get(source_id, url)
            if r is None or len(r.content) > MAX_ASSET:
                continue
            members = [(url, PurePosixPath(urlparse(url).path).name, r.content)]
            if url.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        members = [
                            (f"{url}#{m}", m, z.read(m))
                            for m in z.namelist()
                            if re.search(r"\.(svg|png|gif)$", m, re.I) and not m.startswith("__MACOSX") and not exclude.search(PurePosixPath(m).name)
                        ]
                except zipfile.BadZipFile:
                    continue
            for member_url, name, data in members:
                item = self._item(member_url, name, data)
                if item is not None:
                    n += 1
                    yield item
        self.meta[source_id] = {"pages": pages, "assets": len(assets), "items": n}
