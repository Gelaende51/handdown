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
from concurrent.futures import Future, ThreadPoolExecutor

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

    def __init__(self, cfg: Config, conn: sqlite3.Connection, delay: float = 0.05, workers: int = 6):
        self.cfg = cfg
        self.conn = conn
        self.delay = delay
        self.workers = workers  # parallel image requests: the black-and-white version is rendered on demand
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.meta: dict[str, dict] = {}

    def sources(self, statuses: tuple[str, ...] = ("accepted",)) -> Iterator[SourceInfo]:
        rows = self.conn.execute(f"SELECT * FROM source WHERE adapter='arasaac' AND harvest_status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        for r in rows:
            yield SourceInfo(
                id=r["id"], name=r["name"], platform_id=r["platform_id"], url=r["url"], license_spdx=LICENSE, extra=json.loads(r["adapter_args"] or "{}")
            )

    def _image(self, url: str, params: dict[str, str] | None = None) -> tuple[str | None, str | None]:
        """(wrapped image, error); runs in worker threads, so errors are returned, not logged."""
        try:
            r = self.client.get(url, params=params)
            time.sleep(self.delay)
            r.raise_for_status()
            return raster.to_svg(r.content), None
        except (httpx.HTTPError, OSError) as e:
            return None, repr(e)

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT adapter_args FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        language = args.get("language", "en")
        r = self.client.get(f"{API}/pictograms/all/{language}")
        r.raise_for_status()
        pictos = r.json()[: args.get("limit")]
        self._n = 0
        pool = ThreadPoolExecutor(self.workers)

        def window(batch: list[dict]) -> list[tuple[Future, Future]]:
            return [
                (
                    pool.submit(self._image, f"{STATIC}/{p['_id']}/{p['_id']}_300.png"),
                    pool.submit(self._image, f"{API}/pictograms/{p['_id']}", {"color": "false", "resolution": "500"}),
                )
                for p in batch
            ]

        # windows of 100 pictograms: finished images do not pile up in memory
        batches = [pictos[i : i + 100] for i in range(0, len(pictos), 100)]
        pending = window(batches[0]) if batches else []
        for k, batch in enumerate(batches):
            current, pending = pending, (window(batches[k + 1]) if k + 1 < len(batches) else [])
            yield from self._emit(source_id, zip(batch, current, strict=True), args)
        pool.shutdown()
        self.meta[source_id] = {"license": LICENSE, "pictograms": len(pictos), "images": self._n}

    def _emit(self, source_id: str, pairs: Iterator[tuple[dict, tuple[Future, Future]]], args: dict) -> Iterator[Item]:
        language = args.get("language", "en")
        for p, (colour_f, bw_f) in pairs:
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
            for (svg, err), oid, extra in ((colour_f.result(), str(pid), []), (bw_f.result(), f"{pid}/bw", ["official black and white"])):
                if err:
                    db.log_error(self.conn, source_id, oid, "harvest", err)
                elif svg:
                    self._n += 1
                    yield Item(original_id=oid, svg=svg, **{**common, "tags": common["tags"] + extra})
