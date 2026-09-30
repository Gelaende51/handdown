"""Iconify collections from the ``@iconify/json`` npm package.

Iconify is an aggregator: each collection is recorded as its own source with
the original author, license and URL, and ``iconify`` as the platform.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from collections import defaultdict
from collections.abc import Iterator

import httpx

from ..config import USER_AGENT, Config
from .base import Item, SourceInfo

REGISTRY = "https://registry.npmjs.org/@iconify/json/latest"
DOMAIN_BY_CATEGORY = {
    "Logos": "logo",
    "Flags / Maps": "flag-map",
    "Emoji": "emoji",
    "Programming": "programming",
    "Thematic": "thematic",
}


class IconifyAdapter:
    name = "iconify"

    def __init__(self, cfg: Config, conn: sqlite3.Connection | None = None):
        self.cfg = cfg
        self.conn = conn
        self.base = cfg.raw / "iconify"
        self.pkg = self.base / "package"

    def fetch(self) -> str:
        """Download and unpack the latest package unless already present."""
        meta = httpx.get(REGISTRY, headers={"User-Agent": USER_AGENT}, timeout=60).json()
        version = meta["version"]
        marker = self.base / "VERSION"
        if marker.exists() and marker.read_text().strip() == version and self.pkg.exists():
            return version
        self.base.mkdir(parents=True, exist_ok=True)
        tgz = self.base / f"iconify-json-{version}.tgz"
        with httpx.stream("GET", meta["dist"]["tarball"], headers={"User-Agent": USER_AGENT}, timeout=600, follow_redirects=True) as r:
            r.raise_for_status()
            with tgz.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        with tarfile.open(tgz) as tar:
            tar.extractall(self.base, filter="data")  # blocks path traversal
        marker.write_text(version)
        return version

    def _collections(self) -> dict[str, dict]:
        return json.loads((self.pkg / "collections.json").read_text())

    def sources(self, statuses: tuple[str, ...] | None = None) -> Iterator[SourceInfo]:
        """All collections; on a runner with a job list, only the Iconify
        sources that the job list marked accepted."""
        version = (self.base / "VERSION").read_text().strip() if (self.base / "VERSION").exists() else None
        wanted = None
        if self.conn is not None:
            ids = {r[0] for r in self.conn.execute("SELECT id FROM source WHERE adapter='iconify' AND harvest_status='accepted'")}
            wanted = ids or None
        for prefix, info in self._collections().items():
            if wanted is not None and f"iconify:{prefix}" not in wanted:
                continue
            author = info.get("author", {})
            lic = info.get("license", {})
            cat = info.get("category")
            yield SourceInfo(
                id=f"iconify:{prefix}",
                name=info.get("name", prefix),
                platform_id="iconify",
                url=author.get("url"),
                license_spdx=lic.get("spdx") or lic.get("title"),
                license_url=lic.get("url"),
                author=author.get("name"),
                author_url=author.get("url"),
                version=info.get("version"),
                domain=DOMAIN_BY_CATEGORY.get(cat or "", "ui" if cat and cat.startswith(("UI", "Material")) else None),
                category=cat,
                grid_size=info.get("height"),
                found_via="platform:iconify",
                extra={
                    "iconify_prefix": prefix,
                    "iconify_package_version": version,
                    "palette": info.get("palette"),
                    "iconify_tags": info.get("tags"),
                    "total": info.get("total"),
                },
            )

    def items(self, source_id: str) -> Iterator[Item]:
        prefix = source_id.split(":", 1)[1]
        path = self.pkg / "json" / f"{prefix}.json"
        data = json.loads(path.read_text())
        dw, dh = data.get("width", 16), data.get("height", 16)
        dl, dt = data.get("left", 0), data.get("top", 0)
        cats: dict[str, list[str]] = defaultdict(list)
        for cat, names in (data.get("categories") or {}).items():
            for n in names:
                cats[n].append(cat)
        synonyms: dict[str, list[str]] = defaultdict(list)
        for alias, a in (data.get("aliases") or {}).items():
            parent = a.get("parent")
            if parent and set(a) == {"parent"}:  # pure renames only, not transformed variants
                synonyms[parent].append(alias)
        chars = {v: k for k, v in (data.get("chars") or {}).items()}
        rel = path.relative_to(self.cfg.root) if path.is_relative_to(self.cfg.root) else path
        for name, icon in data["icons"].items():
            w, h = icon.get("width", dw), icon.get("height", dh)
            left, top = icon.get("left", dl), icon.get("top", dt)
            body = icon["body"]
            transform = _transform(icon, w, h)
            if transform:
                body = f'<g transform="{transform}">{body}</g>'
            xlink = ' xmlns:xlink="http://www.w3.org/1999/xlink"' if "xlink:" in body else ""
            svg = f'<svg xmlns="http://www.w3.org/2000/svg"{xlink} viewBox="{left} {top} {w} {h}">{body}</svg>'
            yield Item(
                original_id=name,
                name=name,
                svg=svg,
                url=f"https://icon-sets.iconify.design/{prefix}/{name}/",
                raw_path=f"{rel}#{name}",
                tags=synonyms.get(name, []),
                categories=cats.get(name, []),
                unicode_codepoint=chars.get(name),
                hidden=bool(icon.get("hidden")),
            )


def _transform(icon: dict, w: float, h: float) -> str:
    parts = []
    rotate = icon.get("rotate", 0) % 4
    if icon.get("hFlip"):
        parts.append(f"translate({w} 0) scale(-1 1)")
    if icon.get("vFlip"):
        parts.append(f"translate(0 {h}) scale(1 -1)")
    if rotate:
        parts.append(f"rotate({rotate * 90} {w / 2} {h / 2})")
    return " ".join(parts)
