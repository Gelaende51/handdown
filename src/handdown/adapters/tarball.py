"""Sources delivered as a tarball: GitHub repositories (codeload, outside the
API rate limit) and npm packages. Only SVG files are extracted."""

from __future__ import annotations

import json
import re
import sqlite3
import tarfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import httpx

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
        tgz = self._download(source_id, self.tarball_url(args))
        dest = self.cfg.raw / re.sub(r"[^\w.-]+", "_", source_id)
        license_text = ""
        found: list[Item] = []
        with tarfile.open(tgz) as tar:
            for m in tar:
                if not m.isfile():
                    continue
                p = PurePosixPath(m.name)
                rel = PurePosixPath(*p.parts[1:]) if len(p.parts) > 1 else p  # drop top-level dir
                if ".." in rel.parts or rel.is_absolute():
                    continue
                low = rel.name.lower()
                if len(rel.parts) <= 2 and low.startswith(("license", "licence", "copying")) and not license_text:
                    license_text = tar.extractfile(m).read(200_000).decode("utf-8", "replace")  # type: ignore[union-attr]
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
        self.meta[source_id] = {"license": guess_license(license_text) if license_text else None, "svgs": len(found)}
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
