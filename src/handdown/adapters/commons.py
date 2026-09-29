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

from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

API = "https://commons.wikimedia.org/w/api.php"
MAX_SVG = 2_000_000


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip() or None


class CommonsAdapter:
    name = "commons"
    min_items = 1

    def __init__(self, cfg: Config, conn: sqlite3.Connection, delay: float = 0.2):
        self.cfg = cfg
        self.conn = conn
        self.delay = delay
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT + " contact via repository issues"}, timeout=120, follow_redirects=True)
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted", "blocked-network")) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='commons' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(id=r["id"], name=r["name"], platform_id="commons", url=r["url"], domain=r["domain"], extra=json.loads(r["adapter_args"] or "{}"))

    def _get(self, **params: str) -> dict:
        params = {"format": "json", "formatversion": "2", **params}
        for attempt in range(5):
            r = self.client.get(API, params=params)
            if r.status_code in (429, 503):
                time.sleep(2**attempt * 5)
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            return r.json()
        r.raise_for_status()
        return {}

    def _members(self, category: str, depth: int, limit: int) -> list[str]:
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
                    elif t.lower().endswith(".svg"):
                        files.append(t)
                if "continue" not in data:
                    break
                cont = {"cmcontinue": data["continue"]["cmcontinue"]}
        return list(dict.fromkeys(files))[:limit]

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT url, adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        category = args.get("category") or "Category:" + unquote(row["url"].rsplit("Category:", 1)[1]).replace("_", " ")
        titles = self._members(category, int(args.get("depth", 2)), int(args.get("limit", 20000)))
        for i in range(0, len(titles), 50):
            data = self._get(
                action="query",
                prop="imageinfo|categories",
                iiprop="url|extmetadata|mime|size",
                clshow="!hidden",
                cllimit="max",
                titles="|".join(titles[i : i + 50]),
            )
            for page in data.get("query", {}).get("pages", []):
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("mime") != "image/svg+xml" or info.get("size", 0) > MAX_SVG:
                    continue
                r = self.client.get(info["url"])
                time.sleep(self.delay)
                if r.status_code != 200:
                    continue
                meta = info.get("extmetadata") or {}
                val = {k: _text((v or {}).get("value")) for k, v in meta.items()}.get
                title = page["title"].removeprefix("File:")
                yield Item(
                    license=val("LicenseShortName"),
                    author=val("Artist"),
                    original_id=title,
                    name=re.sub(r"\.svg$", "", title, flags=re.I),
                    svg=r.text,
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
