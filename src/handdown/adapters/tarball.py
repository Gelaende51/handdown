"""Sources delivered as a tarball: GitHub repositories (codeload, outside the
API rate limit) and npm packages. Only SVG files are extracted."""

from __future__ import annotations

import io
import json
import re
import sqlite3
import tarfile
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import httpx
from PIL import Image

from .. import raster
from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

MAX_TARBALL = 400 * 1024 * 1024
MAX_SVG = 300 * 1024
SKIP_DIRS = {
    "node_modules",
    "test",
    "tests",
    "__tests__",
    "spec",
    ".github",
    "website",
    "site",
    "demo",
    "demos",
    "examples",
    "example",
    "docs-src",
    "screenshots",
    "preview",
    "previews",
    "banner",
    "assets-readme",
    "fixtures",
    "vendor",
    "build-tools",
    "scripts",
}
LICENSE_PATTERNS = [
    ("CC0-1.0", r"CC0|Creative Commons Zero|public domain dedication"),
    ("MIT", r"\bMIT License\b|Permission is hereby granted, free of charge"),
    ("Apache-2.0", r"Apache License,?\s+Version 2\.0"),
    ("OFL-1.1", r"SIL OPEN FONT LICENSE"),
    ("ISC", r"\bISC License\b"),
    ("CC-BY-SA-4.0", r"Attribution-ShareAlike 4\.0"),
    ("CC-BY-4.0", r"Attribution 4\.0 International"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
    ("GPL-2.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 2"),
    ("BSD-3-Clause", r"Redistribution and use in source and binary forms"),
    ("Unlicense", r"This is free and unencumbered software released into the public domain"),
]


def guess_license(text: str) -> str | None:
    for spdx, pat in LICENSE_PATTERNS:
        if re.search(pat, text, re.I):
            return spdx
    return None


RASTER_EXT = (".png", ".gif", ".bmp", ".ico")
MAX_RASTER = 2 * 1024 * 1024
# size folders and suffixes of one icon drawn at several sizes ("16/save.png", "save-32x32.png", retina copies with an @2x suffix)
SIZE_DIR = re.compile(r"^(\d{1,4}(x\d{1,4})?(@\dx)?|\d{1,4}px|scalable|(drawable|mipmap)-\w+|[xm]*hdpi|ldpi)$", re.I)
SIZE_SUFFIX = re.compile(r"([-_.@]?(\d{1,4}x\d{1,4}|\d{1,4}px)|[-_.](16|20|22|24|32|36|48|64|72|96|128|144|192|256|512|1024)|@\dx)$", re.I)


def _raster_key(rel: PurePosixPath) -> tuple[tuple[str, ...], str]:
    return tuple(p.lower() for p in rel.parts[:-1] if not SIZE_DIR.match(p)), SIZE_SUFFIX.sub("", rel.stem.lower())


def _skip(parts: tuple[str, ...]) -> bool:
    return any(p.lower() in SKIP_DIRS or p.startswith(".") for p in parts)


class TarballAdapter:
    """Base for git-svg and npm-svg: subclasses provide the tarball URL."""

    name = "tarball"
    min_items = 10  # fewer SVGs than this: not a pictogram set

    def __init__(self, cfg: Config, conn: sqlite3.Connection):
        self.cfg = cfg
        self.conn = conn
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True)
        self.meta: dict[str, dict] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[Path]] = {}
        self._queue: list[SourceInfo] = []
        self._window = 0

    def prefetch(self, infos: list[SourceInfo], workers: int = 6, window: int = 12) -> None:
        """Download tarballs in the background, at most ``window`` ahead of
        extraction so pending downloads cannot fill the disk."""
        self._pool = ThreadPoolExecutor(workers)
        self._queue = list(infos)
        self._window = window
        self._pump()

    def _pump(self) -> None:
        while self._pool and self._queue and len(self._futures) < self._window:
            info = self._queue.pop(0)
            try:
                url = self.tarball_url(dict(info.extra))
            except Exception:  # resolved again (and reported) in items()
                continue
            self._futures[info.id] = self._pool.submit(self._download, info.id, url)

    def tarball_url(self, args: dict) -> str:
        raise NotImplementedError

    def sources(self, statuses: tuple[str, ...] = ("accepted",)) -> Iterator[SourceInfo]:
        rows = self.conn.execute(
            f"SELECT * FROM source WHERE adapter=? AND harvest_status IN ({','.join('?' * len(statuses))})",
            (self.name, *statuses),
        ).fetchall()
        for r in rows:
            yield SourceInfo(
                id=r["id"],
                name=r["name"],
                platform_id=r["platform_id"],
                url=r["url"],
                license_spdx=r["license_spdx"],
                license_url=r["license_url"],
                author=r["author"],
                domain=r["domain"],
                found_via=r["found_via"],
                notes=r["notes"],
                extra=json.loads(r["adapter_args"] or "{}"),
            )

    def _download(self, source_id: str, url: str) -> Path:
        dest = self.cfg.raw / re.sub(r"[^\w.-]+", "_", source_id)
        tgz = dest.with_suffix(".tar.gz")
        if tgz.exists():
            return tgz
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with self.client.stream("GET", url) as r:
            r.raise_for_status()
            tmp = tgz.with_suffix(".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > MAX_TARBALL:
                        raise ValueError(f"tarball exceeds {MAX_TARBALL >> 20} MB")
                    f.write(chunk)
        tmp.rename(tgz)
        return tgz

    def items(self, source_id: str) -> Iterator[Item]:
        row = self.conn.execute("SELECT adapter_args, url FROM source WHERE id=?", (source_id,)).fetchone()
        args = json.loads(row["adapter_args"] or "{}")
        include = [p.strip("/") for p in args.get("include", [])]
        fut = self._futures.pop(source_id, None)
        self._pump()
        tgz = fut.result() if fut else self._download(source_id, self.tarball_url(args))
        dest = self.cfg.raw / re.sub(r"[^\w.-]+", "_", source_id)
        license_text = ""
        found: list[Item] = []
        rasters: dict[tuple[tuple[str, ...], str], tuple[tarfile.TarInfo, PurePosixPath]] = {}
        with tarfile.open(tgz) as tar:
            members = [m for m in tar if m.isfile()]
            svg_stems = {PurePosixPath(m.name).stem.lower() for m in members if m.name.lower().endswith(".svg")}
            for m in members:
                p = PurePosixPath(m.name)
                rel = PurePosixPath(*p.parts[1:]) if len(p.parts) > 1 else p  # drop top-level dir
                if ".." in rel.parts or rel.is_absolute():
                    continue
                low = rel.name.lower()
                if len(rel.parts) <= 2 and low.startswith(("license", "licence", "copying")) and not license_text:
                    license_text = tar.extractfile(m).read(200_000).decode("utf-8", "replace")  # type: ignore[union-attr]
                    continue
                if low.endswith(RASTER_EXT) and m.size <= MAX_RASTER and not _skip(rel.parts[:-1]) and rel.stem.lower() not in svg_stems:
                    if not include or any(str(rel).startswith(i) for i in include):
                        key = _raster_key(rel)  # one icon in several sizes: keep the largest file
                        if key not in rasters or m.size > rasters[key][0].size:
                            rasters[key] = (m, rel)
                    continue
                if not low.endswith(".svg") or m.size > MAX_SVG or _skip(rel.parts[:-1]):
                    continue
                if include and not any(str(rel).startswith(i) for i in include):
                    continue
                data = tar.extractfile(m).read()  # type: ignore[union-attr]
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                try:
                    svg = data.decode("utf-8")
                except UnicodeDecodeError:
                    svg = data.decode("latin-1")
                found.append(
                    Item(
                        original_id=str(rel),
                        name=rel.stem,
                        svg=svg,
                        url=self.file_url(args, str(rel)),
                        raw_path=str(out.relative_to(self.cfg.root)) if out.is_relative_to(self.cfg.root) else str(out),
                        categories=[c for c in rel.parts[:-1] if c.lower() not in ("svg", "svgs", "icons", "src", "dist", "assets")],
                    )
                )
            svgs = len(found)
            for m, rel in rasters.values():
                data = tar.extractfile(m).read()  # type: ignore[union-attr]
                try:
                    with Image.open(io.BytesIO(data)) as img:
                        size = img.size
                    if not raster.icon_like(*size):
                        continue
                    svg = raster.to_svg(data)
                except (OSError, ValueError, Image.DecompressionBombError):
                    continue  # not an image PIL can read
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                found.append(
                    Item(
                        original_id=str(rel),
                        name=rel.stem,
                        svg=svg,
                        format="raster",
                        url=self.file_url(args, str(rel)),
                        raw_path=str(out.relative_to(self.cfg.root)) if out.is_relative_to(self.cfg.root) else str(out),
                        categories=[
                            c
                            for c in rel.parts[:-1]
                            if c.lower() not in ("png", "pngs", "icons", "src", "dist", "assets", "img", "images") and not SIZE_DIR.match(c)
                        ],
                    )
                )
        self.meta[source_id] = {"license": guess_license(license_text) if license_text else None, "svgs": svgs, "rasters": len(found) - svgs}
        if license_text:
            (dest / "LICENSE.txt").parent.mkdir(parents=True, exist_ok=True)
            (dest / "LICENSE.txt").write_text(license_text)
        tgz.unlink()  # the extracted SVGs and LICENSE are the raw copy; saves disk
        yield from found

    def file_url(self, args: dict, rel: str) -> str | None:
        return None


class GitSvgAdapter(TarballAdapter):
    name = "git-svg"

    def tarball_url(self, args: dict) -> str:
        return f"https://codeload.github.com/{args['repo']}/tar.gz/{args.get('branch') or 'HEAD'}"

    def file_url(self, args: dict, rel: str) -> str | None:
        return f"https://github.com/{args['repo']}/blob/{args.get('branch') or 'HEAD'}/{rel}"


class NpmSvgAdapter(TarballAdapter):
    name = "npm-svg"

    def tarball_url(self, args: dict) -> str:
        meta = self.client.get(f"https://registry.npmjs.org/{args['package']}/latest").json()
        args["version"] = meta.get("version")
        return meta["dist"]["tarball"]

    def file_url(self, args: dict, rel: str) -> str | None:
        return f"https://www.npmjs.com/package/{args['package']}?activeTab=code#{rel}"
