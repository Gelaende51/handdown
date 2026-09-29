"""Snowball discovery of new sources.

Engines reachable from the dev container: the GitHub search API and the npm
registry search. Queries are templated from domains x languages x
platform vocabulary; every query is logged in ``search_log`` and every
candidate records the query (or source) it came from. Harvested sources'
READMEs are mined for links to further sources ("awesome" lists,
"alternatives" sections).

Web-wide searches done outside this module (e.g. by an agent with a web
search tool) are recorded through :func:`record_candidate` with
``engine="websearch"`` so the audit trail stays in one place.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

import httpx

from . import db
from .config import USER_AGENT

GITHUB_SEARCH = "https://api.github.com/search/repositories"
NPM_SEARCH = "https://registry.npmjs.org/-/v1/search"

# Word for "pictogram"/"icon" in many languages, used to widen searches.
TERMS = {
    "en": ["pictogram", "pictograms", "icon set", "svg icons", "icon font", "symbols svg", "signage symbols"],
    "de": ["piktogramme", "symbole svg"],
    "fr": ["pictogrammes", "icônes svg"],
    "es": ["pictogramas", "iconos svg"],
    "it": ["pittogrammi", "icone svg"],
    "pt": ["pictogramas", "ícones svg"],
    "nl": ["pictogrammen"],
    "pl": ["piktogramy", "ikony svg"],
    "ru": ["пиктограммы", "иконки svg"],
    "ja": ["ピクトグラム", "アイコン svg"],
    "zh": ["图标 svg", "象形图"],
    "ko": ["픽토그램", "아이콘 svg"],
    "tr": ["piktogram", "ikon svg"],
    "cs": ["piktogramy"],
    "sv": ["piktogram"],
}
DOMAINS = [
    "",
    "safety",
    "hazard",
    "ghs",
    "iso 7010",
    "iso 7001",
    "wayfinding",
    "signage",
    "transport",
    "aac",
    "accessibility",
    "communication board",
    "medical",
    "healthcare",
    "map",
    "weather",
    "laundry care",
    "packaging",
    "sport",
    "olympic",
    "emoji",
    "dingbats",
    "traffic sign",
    "electrical symbols",
    "public information",
    "isotype",
    "sign language",
    "education",
    "food",
    "agriculture",
    "science",
    "chemistry",
    "music",
    "game",
    "military symbols",
    "maritime",
    "aviation",
    "railway",
    "braille",
    "blissymbols",
    "hand drawn",
    "pixel",
    "1-bit",
]
GITHUB_TOPICS = [
    "icons",
    "icon-set",
    "svg-icons",
    "pictograms",
    "pictogram",
    "icon-font",
    "iconset",
    "icon-pack",
    "icons-pack",
    "emoji",
    "symbols",
    "glyphs",
    "dingbats",
    "open-source-icons",
    "free-icons",
    "icon-library",
    "signage",
    "wayfinding",
    "aac",
    "pixel-icons",
]


def seed_queries() -> list[tuple[str, str]]:
    """(engine, query) pairs, most productive first."""
    out: list[tuple[str, str]] = []
    for t in GITHUB_TOPICS:
        out.append(("github", f"topic:{t} stars:>=5"))
    for d in DOMAINS:
        for t in TERMS["en"][:3]:
            out.append(("github", f"{d} {t}".strip()))
    for lang, terms in TERMS.items():
        if lang != "en":
            for t in terms:
                out.append(("github", t))
    for d in DOMAINS[:20]:
        out.append(("npm", f"{d} icons svg".strip()))
    for kw in ("icons", "svg-icons", "pictograms", "icon-font", "iconset", "emoji-svg"):
        out.append(("npm", f"keywords:{kw}"))
    return out


class Discoverer:
    def __init__(self, conn: sqlite3.Connection, client: httpx.Client | None = None, log: Any = print):
        self.conn = conn
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
        self.log = log
        self.known = self._known_urls()

    def _known_urls(self) -> set[str]:
        urls = set()
        for (u,) in self.conn.execute("SELECT url FROM source WHERE url IS NOT NULL"):
            urls.add(norm_url(u))
        return urls

    def done(self, engine: str, query: str) -> bool:
        return self.conn.execute("SELECT 1 FROM search_log WHERE engine=? AND query=?", (engine, query)).fetchone() is not None

    def log_search(self, engine: str, query: str, seed: str, n: int, notes: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO search_log (query, engine, seed, run_at, candidates_found, notes) VALUES (?,?,?,?,?,?) RETURNING id",
            (query, engine, seed, db.now(), n, notes),
        )
        return cur.fetchone()[0]

    # ---- engines -------------------------------------------------------
    def github(self, query: str, pages: int = 1) -> list[dict[str, Any]]:
        repos = []
        for page in range(1, pages + 1):
            r = self.client.get(GITHUB_SEARCH, params={"q": query, "per_page": 100, "page": page, "sort": "stars"})
            if r.status_code == 403 or r.status_code == 429:
                wait = int(r.headers.get("x-ratelimit-reset", time.time() + 60)) - int(time.time()) + 2
                self.log(f"  github rate limit, waiting {max(wait, 5)} s")
                time.sleep(max(wait, 5))
                r = self.client.get(GITHUB_SEARCH, params={"q": query, "per_page": 100, "page": page, "sort": "stars"})
            r.raise_for_status()
            items = r.json().get("items", [])
            repos += items
            if len(items) < 100:
                break
            time.sleep(6.5)  # 10 searches/minute unauthenticated
        return repos

    def npm(self, query: str) -> list[dict[str, Any]]:
        r = self.client.get(NPM_SEARCH, params={"text": query, "size": 250})
        r.raise_for_status()
        return r.json().get("objects", [])

    # ---- candidates ----------------------------------------------------
    def run_query(self, engine: str, query: str, seed: str = "seed") -> int:
        if self.done(engine, query):
            return 0
        found = 0
        if engine == "github":
            results = self.github(query)
            sid = self.log_search(engine, query, seed, 0)
            for repo in results:
                found += self._github_candidate(repo, f"search:{sid}")
            time.sleep(6.5)
        elif engine == "npm":
            results = self.npm(query)
            sid = self.log_search(engine, query, seed, 0)
            for obj in results:
                found += self._npm_candidate(obj, f"search:{sid}")
        else:
            raise ValueError(engine)
        self.conn.execute("UPDATE search_log SET candidates_found=? WHERE id=?", (found, sid))
        self.conn.commit()
        return found

    def _github_candidate(self, repo: dict[str, Any], found_via: str) -> int:
        url = repo["html_url"]
        if norm_url(url) in self.known or repo.get("fork") or (repo.get("archived") and repo.get("stargazers_count", 0) < 20):
            return 0
        lic = (repo.get("license") or {}).get("spdx_id")
        return record_candidate(
            self.conn,
            self.known,
            id=f"gh:{repo['full_name']}".lower(),
            platform_id="github",
            name=repo["name"],
            url=url,
            license_spdx=None if lic in (None, "NOASSERTION") else lic,
            author=repo["owner"]["login"],
            found_via=found_via,
            adapter="git-svg",
            adapter_args={"repo": repo["full_name"], "branch": repo.get("default_branch"), "size_kb": repo.get("size")},
            popularity={"stars": repo.get("stargazers_count"), "forks": repo.get("forks_count"), "pushed_at": repo.get("pushed_at")},
            notes=(repo.get("description") or "")[:500] + (f" [topics: {', '.join(repo.get('topics') or [])}]" if repo.get("topics") else ""),
        )

    def _npm_candidate(self, obj: dict[str, Any], found_via: str) -> int:
        p = obj["package"]
        repo_url = (p.get("links") or {}).get("repository")
        if repo_url and norm_url(repo_url) in self.known:
            return 0
        text = " ".join([p.get("description") or ""] + (p.get("keywords") or [])).lower()
        # Framework wrappers (react/vue components) usually repackage a set
        # that is also published as plain SVG; keep them, but mark it.
        wrapper = any(w in text for w in ("react", "vue", "angular", "svelte", "solid-js", "web component"))
        return record_candidate(
            self.conn,
            self.known,
            id=f"npm:{p['name']}".lower(),
            platform_id="npm",
            name=p["name"],
            url=(p.get("links") or {}).get("npm"),
            license_spdx=p.get("license"),
            author=(p.get("publisher") or {}).get("username"),
            found_via=found_via,
            adapter="npm-svg",
            adapter_args={"package": p["name"], "version": p.get("version"), "repository": repo_url, "wrapper": wrapper},
            popularity={"monthly_downloads": (obj.get("downloads") or {}).get("monthly"), "dependents": obj.get("dependents")},
            notes=(p.get("description") or "")[:500],
        )

    # ---- link mining ---------------------------------------------------
    def mine_readme(self, source_id: str) -> int:
        row = self.conn.execute("SELECT url FROM source WHERE id=?", (source_id,)).fetchone()
        if not row or not row[0]:
            return 0
        m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", row[0])
        if not m:
            return 0
        owner, repo = m.group(1), m.group(2).removesuffix(".git")
        query = f"readme:{owner}/{repo}"
        if self.done("readme", query):
            return 0
        text = ""
        for name in ("README.md", "readme.md", "README.markdown", "README.rst", "README"):
            r = self.client.get(f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{name}")
            if r.status_code == 200:
                text = r.text
                break
        sid = self.log_search("readme", query, f"source:{source_id}", 0)
        found = 0
        for o, n in set(re.findall(r"https?://github\.com/([\w.-]+)/([\w.-]+)", text)):
            n = n.removesuffix(".git")
            if o.lower() in ("sponsors", "orgs", "topics", "features", "marketplace") or (o, n) == (owner, repo):
                continue
            url = f"https://github.com/{o}/{n}"
            found += record_candidate(
                self.conn,
                self.known,
                id=f"gh:{o}/{n}".lower(),
                platform_id="github",
                name=n,
                url=url,
                author=o,
                found_via=f"search:{sid}",
                adapter="git-svg",
                adapter_args={"repo": f"{o}/{n}"},
                notes=f"linked from README of {source_id}",
            )
        self.conn.execute("UPDATE search_log SET candidates_found=? WHERE id=?", (found, sid))
        self.conn.commit()
        return found


def norm_url(url: str) -> str:
    u = url.lower().strip().removesuffix("/").removesuffix(".git")
    u = re.sub(r"^git\+", "", u)
    u = re.sub(r"^(https?://)?(www\.)?", "", u)
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"/(tree|blob)/[^/]+.*$", "", u)
    return u


def record_candidate(conn: sqlite3.Connection, known: set[str] | None = None, **fields: Any) -> int:
    """Insert a candidate source unless its URL or id is already known."""
    url = fields.get("url")
    if url and known is not None and norm_url(url) in known:
        return 0
    if conn.execute("SELECT 1 FROM source WHERE id=?", (fields["id"],)).fetchone():
        return 0
    conn.execute(
        "INSERT OR IGNORE INTO platform (id, name, found_via, first_seen) VALUES (?, ?, ?, ?)",
        (fields["platform_id"], fields["platform_id"], fields.get("found_via"), db.now()[:10]),
    )
    row = {"harvest_status": "candidate", "accessed_at": db.now(), **fields}
    db.upsert(conn, "source", row, ("id",))
    if url and known is not None:
        known.add(norm_url(url))
    return 1


def run(conn: sqlite3.Connection, queries: Iterable[tuple[str, str]] | None = None, limit: int | None = None, mine: bool = True, log: Any = print) -> int:
    d = Discoverer(conn, log=log)
    total = 0
    qs = list(queries or seed_queries())
    for n, (engine, q) in enumerate(qs[:limit] if limit else qs):
        try:
            got = d.run_query(engine, q)
        except httpx.HTTPError as e:
            log(f"  {engine} '{q}': {e}")
            continue
        total += got
        if got:
            log(f"  [{n + 1}/{len(qs)}] {engine} '{q}': +{got}")
    if mine:
        for (sid,) in conn.execute("SELECT id FROM source WHERE harvest_status='harvested' AND url LIKE '%github.com%'").fetchall():
            try:
                total += d.mine_readme(sid)
            except httpx.HTTPError as e:
                log(f"  readme {sid}: {e}")
    return total
