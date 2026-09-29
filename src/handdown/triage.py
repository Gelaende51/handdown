"""Sort candidate sources into accepted / rejected before any download.

Heuristic and explainable: every decision is written to the source's notes.
Harvesting is the final check (tarball adapters reject sets with fewer than
10 SVGs), so the heuristic only needs to keep obvious non-sets out and avoid
fetching a set Iconify already provided.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

POSITIVE = {
    "icon": 2,
    "icons": 3,
    "iconset": 3,
    "icon-set": 3,
    "pictogram": 4,
    "pictograms": 4,
    "piktogramm": 4,
    "piktogramme": 4,
    "pictogramas": 4,
    "pictogrammes": 4,
    "symbol": 1,
    "symbols": 2,
    "glyph": 1,
    "glyphs": 2,
    "svg": 1,
    "emoji": 1,
    "dingbat": 3,
    "dingbats": 3,
    "signage": 3,
    "wayfinding": 3,
    "signs": 1,
    "icon-font": 3,
    "iconfont": 3,
    "aac": 1,
    "map-icons": 3,
    "ピクトグラム": 4,
    "アイコン": 3,
    "图标": 3,
    "пиктограммы": 4,
    "иконки": 3,
    "픽토그램": 4,
    "아이콘": 3,
}
NEGATIVE = {
    "react": 3,
    "vue": 3,
    "angular": 3,
    "svelte": 3,
    "flutter": 2,
    "component": 2,
    "components": 2,
    "cli": 2,
    "picker": 3,
    "cheat": 3,
    "converter": 3,
    "plugin": 2,
    "extension": 2,
    "generator": 2,
    "editor": 2,
    "framework": 3,
    "sdk": 3,
    "api": 1,
    "bot": 3,
    "dashboard": 1,
    "loader": 2,
    "loaders": 2,
    "animation": 1,
    "animated": 2,
    "tool": 1,
    "tools": 1,
    "app": 1,
    "wrapper": 3,
    "parser": 3,
    "mvvm": 5,
    "architecture": 3,
    "subscription": 5,
    "books": 5,
    "sensitive": 5,
    "terminal": 3,
    "ls": 3,
    "wallpaper": 3,
    "theme": 1,
    "badges": 3,
    "shields": 3,
    "logo": 1,
    "logos": 1,
    "brand": 1,
    "brands": 1,
    "font-patcher": 5,
    "patcher": 4,
}
LINK_LIST = re.compile(r"\bawesome\b|curated list|list of|collection of links|resources", re.I)
MAX_SIZE_KB = 300_000


def _words(text: str) -> list[str]:
    return re.findall(r"[\w\-ぁ-んァ-ン一-龯가-힣а-яё]+", text.lower())


def line_score(text: str) -> int:
    words = _words(text)
    return sum(POSITIVE.get(w, 0) for w in words) - sum(NEGATIVE.get(w, 0) for w in words)


def iconify_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Squashed Iconify set names/prefixes -> source id."""
    out = {}
    for sid, name in conn.execute("SELECT id, name FROM source WHERE platform_id='iconify'"):
        prefix = sid.split(":", 1)[1]
        for key in (name, prefix):
            sq = re.sub(r"[^a-z0-9]", "", key.lower())
            if len(sq) >= 5:
                out[sq] = sid
    return out


def decide(row: sqlite3.Row, iconify: dict[str, str]) -> tuple[str, str]:
    args = json.loads(row["adapter_args"] or "{}")
    pop = json.loads(row["popularity"] or "{}")
    text = " ".join(filter(None, [row["name"], row["notes"], row["id"]]))
    score = line_score(text)
    repo_sq = re.sub(r"[^a-z0-9]", "", (row["name"] or "").lower())
    for key in (repo_sq, repo_sq.removesuffix("icons"), repo_sq + "icons"):
        if key in iconify:
            return "rejected", f"triage: same set as {iconify[key]}"
    if LINK_LIST.search(text) and "awesome" in text.lower():
        return "rejected", "triage: link list (README mined for sources)"
    if (args.get("size_kb") or 0) > MAX_SIZE_KB:
        return "rejected", f"triage: repository too large ({args['size_kb'] // 1000} MB)"
    if args.get("wrapper"):
        score -= 2
    stars = pop.get("stars") or 0
    downloads = pop.get("monthly_downloads") or 0
    mined = "linked from README" in (row["notes"] or "")
    if score >= 3 and (stars >= 3 or downloads >= 100 or row["platform_id"] == "npm" or mined):
        return "accepted", f"triage: score {score}"
    return "rejected", f"triage: score {score}"


def run(conn: sqlite3.Connection, log: Any = print) -> dict[str, int]:
    iconify = iconify_names(conn)
    counts = {"accepted": 0, "rejected": 0}
    link_lists = []
    for row in conn.execute("SELECT * FROM source WHERE harvest_status='candidate' AND adapter IN ('git-svg','npm-svg')").fetchall():
        status, note = decide(row, iconify)
        counts[status] += 1
        if "link list" in note:
            link_lists.append(row["id"])
        conn.execute(
            "UPDATE source SET harvest_status=?, notes=COALESCE(notes || ' | ', '') || ? WHERE id=?",
            (status, note, row["id"]),
        )
    conn.commit()
    if link_lists:
        from .discover import Discoverer

        d = Discoverer(conn, log=log)
        mined = 0
        for sid in link_lists:
            try:
                mined += d.mine_readme(sid)
            except Exception as e:  # network hiccups must not stop triage
                log(f"  readme {sid}: {e}")
        log(f"  mined {len(link_lists)} link lists: +{mined} candidates")
        counts["mined"] = mined
    return counts
