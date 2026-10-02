"""Wikimedia Commons categories: SVG files with per-file license, author and
description from ``extmetadata``. Walks subcategories to a limited depth.

Commons is blocked in the dev container; this adapter runs on GitHub Actions
(see ``.github/workflows/harvest.yml``).
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from urllib.parse import unquote

import httpx

from .. import db, raster
from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

API = "https://commons.wikimedia.org/w/api.php"
MAX_SVG = 2_000_000
FORMATS = ("svg", "png", "gif")  # JPEG on Commons is photographs
MIMES = {"image/svg+xml": "svg", "image/png": "png", "image/gif": "gif"}
THUMB = 512  # rasters wider than this are fetched as Commons thumbnails


def _text(value: object) -> str | None:
    """extmetadata values are HTML strings, but also numbers or
    per-language dicts ({"en": ..., "de": ...})."""
    if isinstance(value, dict):
        value = value.get("en") or next(iter(value.values()), None)
    if value is None or value == "":
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", str(value))).strip() or None


class CommonsAdapter:
    name = "commons"
    min_items = 1

    def __init__(self, cfg: Config, conn: sqlite3.Connection, delay: float = 0.5):
        self.cfg = cfg
        self.conn = conn
        self.delay = delay
        # Wikimedia's User-Agent policy: identify the tool and a contact URL.
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted", "blocked-network")) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='commons' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(id=r["id"], name=r["name"], platform_id="commons", url=r["url"], domain=r["domain"], extra=json.loads(r["adapter_args"] or "{}"))

    def _fetch(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        """GET with backoff on 429/503, honouring Retry-After."""
        for attempt in range(8):
            r = self.client.get(url, params=params)
            if r.status_code not in (429, 503):
                time.sleep(self.delay)
                return r
            retry = r.headers.get("retry-after", "")
            time.sleep(float(retry) if retry.isdigit() else min(300, 5 * 2**attempt))
        return r

    def _get(self, **params: str) -> dict:
        # maxlag: back off when Wikimedia's replicas lag (API etiquette)
        r = self._fetch(API, {"format": "json", "formatversion": "2", "maxlag": "5", **params})
        r.raise_for_status()
        data = r.json()
        if data.get("error", {}).get("code") == "maxlag":
            time.sleep(10)
            return self._get(**params)
        return data

    def _members(self, category: str, depth: int, limit: int, formats: tuple[str, ...] = FORMATS) -> list[str]:
        files: list[str] = []
        seen = {category}
        queue = [(category, 0)]
        while queue and len(files) < limit:
            cat, d = queue.pop(0)
            cont: dict[str, str] = {}
            while True:
                data = self._get(action="query", list="categorymembers", cmtitle=cat, cmtype="file|subcat", cmlimit="500", **cont)
                for m in data.get("query", {}).get("categorymembers", []):
                    t = m["title"]
                    if t.startswith("Category:") and d < depth and t not in seen:
                        seen.add(t)
                        queue.append((t, d + 1))
                    elif t.lower().endswith(tuple("." + f for f in formats)):
                        files.append(t)
                if "continue" not in data:
                    break
                cont = {"cmcontinue": data["continue"]["cmcontinue"]}
        return list(dict.fromkeys(files))[:limit]

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT url, adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        category = args.get("category") or "Category:" + unquote(row["url"].rsplit("Category:", 1)[1]).replace("_", " ")
        formats = tuple(args.get("formats", FORMATS))  # e.g. ["png", "gif"] to add rasters to a harvested category
        titles = self._members(category, int(args.get("depth", 2)), int(args.get("limit", 20000)), formats)
        for i in range(0, len(titles), 50):
            data = self._get(
                action="query",
                prop="imageinfo|categories",
                iiprop="url|extmetadata|mime|size",
                iiurlwidth=str(THUMB),  # rasters come as thumbnails
                clshow="!hidden",
                cllimit="max",
                titles="|".join(titles[i : i + 50]),
            )
            for page in data.get("query", {}).get("pages", []):
                info = (page.get("imageinfo") or [{}])[0]
                kind = MIMES.get(info.get("mime", ""))
                if kind not in formats or (kind == "svg" and info.get("size", 0) > MAX_SVG):
                    continue
                is_raster = kind != "svg"
                url = info.get("thumburl") if is_raster and info.get("width", 0) > THUMB else info["url"]
                thumb = is_raster and info.get("width", 0) > THUMB
                dims = (info.get("thumbwidth", 0), info.get("thumbheight", 0)) if thumb else (info.get("width", 0), info.get("height", 0))
                if is_raster and not raster.icon_like(*dims):
                    continue  # banners, maps, photographs of signs
                try:
                    r = self._fetch(url)
                except httpx.HTTPError as e:
                    db.log_error(self.conn, source_id, page["title"], "harvest", repr(e))
                    continue
                if r.status_code != 200:
                    db.log_error(self.conn, source_id, page["title"], "harvest", f"HTTP {r.status_code}")
                    continue
                meta = info.get("extmetadata") or {}
                val = {k: _text((v or {}).get("value")) for k, v in meta.items()}.get
                title = page["title"].removeprefix("File:")
                try:
                    svg = raster.to_svg(r.content) if is_raster else r.text
                except OSError as e:  # PIL cannot read it
                    db.log_error(self.conn, source_id, page["title"], "harvest", repr(e))
                    continue
                yield Item(
                    license=val("LicenseShortName"),
                    author=val("Artist"),
                    original_id=title,
                    name=re.sub(r"\.(svg|png|gif)$", "", title, flags=re.I),
                    svg=svg,
                    format="raster" if is_raster else "svg",
                    url=info.get("descriptionurl"),
                    categories=[c["title"].removeprefix("Category:") for c in page.get("categories", [])],
                    description=json.dumps(
                        {
                            "description": val("ImageDescription"),
                            "license": val("LicenseShortName"),
                            "license_url": val("LicenseUrl"),
                            "artist": val("Artist"),
                            "date": val("DateTimeOriginal"),
                            "credit": val("Credit"),
                            "usage_terms": val("UsageTerms"),
                        },
                        ensure_ascii=False,
                    ),
                )
