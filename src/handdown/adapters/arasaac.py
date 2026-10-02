"""ARASAAC (Aragonese Centre for Augmentative and Alternative Communication):
AAC pictograms by Sergio Palao, CC BY-NC-SA 4.0, via the public API
(https://github.com/Arasaac/public-api, config/openapi_v1.yml).

Each pictogram comes twice: the colour image as the original (the catalog
derives its own 1-bit version) and ARASAAC's own black-and-white version."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator

import httpx

from .. import db, raster
from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

API = "https://api.arasaac.org/v1"
STATIC = "https://static.arasaac.org/pictograms"
LICENSE = "CC-BY-NC-SA-4.0"
AUTHOR = "Sergio Palao; ARASAAC (Gobierno de Aragón)"


class ArasaacAdapter:
    name = "arasaac"
    min_items = 1

    def __init__(self, cfg: Config, conn: sqlite3.Connection, delay: float = 0.05):
        self.cfg = cfg
        self.conn = conn
        self.delay = delay
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted",)) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='arasaac' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(id=r["id"], name=r["name"], platform_id=r["platform_id"], url=r["url"], license_spdx=LICENSE, extra=json.loads(r["adapter_args"] or "{}"))

    def _image(self, source_id: str, url: str, params: dict[str, str] | None = None) -> str | None:
        try:
            r = self.client.get(url, params=params)
            time.sleep(self.delay)
            r.raise_for_status()
            return raster.to_svg(r.content)
        except (httpx.HTTPError, OSError) as e:
            db.log_error(self.conn, source_id, url, "harvest", repr(e))
            return None

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        language = args.get("language", "en")
        r = self.client.get(f"{API}/pictograms/all/{language}")
        r.raise_for_status()
        pictos = r.json()[: args.get("limit")]
        n = 0
        for p in pictos:
            pid = p["_id"]
            words = [k["keyword"] for k in p.get("keywords", []) if k.get("keyword")]
            meta = {k: p.get(k) for k in ("keywords", "synsets", "categories", "tags", "schematic", "sex", "violence", "desc", "lastUpdated")}
            common = dict(
                name=words[0] if words else str(pid),
                format="raster",
                url=f"https://arasaac.org/pictograms/{language}/{pid}",
                tags=words[1:] + [f"wordnet31:{s}" for s in p.get("synsets", [])] + list(p.get("tags", [])),
                categories=list(p.get("categories", [])),
                description=json.dumps(meta, ensure_ascii=False),
                license=LICENSE,
                author=AUTHOR,
            )
            colour = self._image(source_id, f"{STATIC}/{pid}/{pid}_300.png")
            if colour:
                n += 1
                yield Item(original_id=str(pid), svg=colour, **common)
            bw = self._image(source_id, f"{API}/pictograms/{pid}", {"color": "false", "resolution": "500"})
            if bw:
                n += 1
                yield Item(original_id=f"{pid}/bw", svg=bw, **{**common, "tags": common["tags"] + ["official black and white"]})
        self.meta[source_id] = {"license": LICENSE, "pictograms": len(pictos), "images": n}
