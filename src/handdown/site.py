"""Static HTML export (no server, no external requests).

Rendered from the database, like the vault, and including vault ``notes``.
Normalized SVGs are sanitized, so they are safe to inline as <img> sources;
they are hard-linked into ``site/svg`` to save space.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import Config
from .vault import SCORE_COLS

UI = {
    "en": {
        "title": "Pictogram catalog",
        "search": "Search meanings…",
        "domain": "Domain",
        "all": "all",
        "min_sources": "min. sources",
        "sort": "Sort",
        "by_sources": "most sources",
        "by_score": "best score",
        "by_name": "name",
        "size": "Size",
        "bw": "pure black/white",
        "menubar": "menu bar mock-up",
        "depiction": "Depiction",
        "convention": "of sources draw it this way",
        "variants": "variants",
        "sources": "sources",
        "pictograms": "pictograms",
        "back": "← catalog",
        "notes": "Notes",
        "looks_like": "Looks like",
        "license": "license",
        "source": "source",
        "style": "style",
        "shown": "shown",
        "definition": "Definition",
        "labels": "Labels",
        "broader": "Broader",
    },
    "de": {
        "title": "Piktogramm-Katalog",
        "search": "Bedeutungen suchen…",
        "domain": "Bereich",
        "all": "alle",
        "min_sources": "min. Quellen",
        "sort": "Sortierung",
        "by_sources": "meiste Quellen",
        "by_score": "beste Wertung",
        "by_name": "Name",
        "size": "Größe",
        "bw": "rein schwarz/weiß",
        "menubar": "Menüleisten-Vorschau",
        "depiction": "Darstellung",
        "convention": "der Quellen zeichnen es so",
        "variants": "Varianten",
        "sources": "Quellen",
        "pictograms": "Piktogramme",
        "back": "← Katalog",
        "notes": "Notizen",
        "looks_like": "Ähnelt",
        "license": "Lizenz",
        "source": "Quelle",
        "style": "Stil",
        "shown": "angezeigt",
        "definition": "Definition",
        "labels": "Bezeichnungen",
        "broader": "Oberbegriff",
    },
}

CSS = """
:root{--bg:#fafaf8;--fg:#1a1a1a;--muted:#666;--line:#ddd;--card:#fff;--accent:#2457c5;--ink:#000;--paper:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#151515;--fg:#eee;--muted:#aaa;--line:#333;--card:#1e1e1e;--accent:#8fb0ff}}
:root[data-theme=dark]{--bg:#151515;--fg:#eee;--muted:#aaa;--line:#333;--card:#1e1e1e;--accent:#8fb0ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
header,main{max-width:1200px;margin:0 auto;padding:12px 16px}a{color:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.controls input[type=search]{flex:1 1 240px;padding:8px;font-size:16px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
select,button{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;text-decoration:none;color:inherit;display:flex;flex-direction:column;align-items:center;gap:4px}
.card:focus-visible,.card:hover{outline:2px solid var(--accent)}
.card small{color:var(--muted)}
.tile{background:var(--paper);display:inline-flex;align-items:center;justify-content:center;border-radius:4px;padding:4px}
.tile img{display:block}
.bw .tile img{filter:contrast(100) grayscale(1)}
.sheet{display:flex;gap:12px;align-items:end;flex-wrap:wrap}
.menubar{display:flex;gap:10px;background:#ececec;padding:4px 10px;border-radius:6px;align-items:center;flex-wrap:wrap}
.menubar img{width:16px;height:16px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px;text-align:left}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.cluster{border-top:1px solid var(--line);margin-top:20px;padding-top:8px}
.muted{color:var(--muted)}.scroll{overflow-x:auto}
"""

INDEX_JS = """
const T = JSON.parse(document.getElementById('ui').textContent);
let lang = (localStorage.getItem('lang') || navigator.language || 'en').slice(0,2); if (!T[lang]) lang = 'en';
const $ = s => document.querySelector(s);
function tr(){ document.querySelectorAll('[data-t]').forEach(e => e.textContent = T[lang][e.dataset.t]);
  $('#q').placeholder = T[lang].search; document.documentElement.lang = lang; }
let data = [];
fetch('data/concepts.json').then(r => r.json()).then(d => { data = d; draw(); }).catch(() => {
  $('#grid').textContent = 'Open via a local web server (python -m http.server) so data/concepts.json can load.'; });
function label(c){ return (c.labels && c.labels[lang]) || c.label; }
function draw(){
  const q = $('#q').value.trim().toLowerCase(), dom = $('#dom').value, ms = +$('#ms').value, sort = $('#sort').value;
  let r = data.filter(c => c.s >= ms && (!dom || c.d === dom) &&
      (!q || c.label.toLowerCase().includes(q) || Object.values(c.labels||{}).some(l => l.toLowerCase().includes(q))));
  if (sort === 'score') r.sort((a,b) => (b.b||0)-(a.b||0)); else if (sort === 'name') r.sort((a,b) => label(a).localeCompare(label(b)));
  const g = $('#grid'); g.innerHTML = '';
  for (const c of r.slice(0, 600)) {
    const a = document.createElement('a'); a.className = 'card'; a.href = 'c/' + c.f + '.html';
    a.innerHTML = `<span class="tile"><img src="svg/${c.r}.svg" width="48" height="48" alt=""></span><b></b><small>${c.s} ${T[lang].sources} · ${c.k} ${T[lang].depiction}</small>`;
    a.querySelector('b').textContent = label(c); g.appendChild(a);
  }
  $('#count').textContent = `${Math.min(r.length,600)} / ${r.length} ${T[lang].shown}`;
}
['#q','#dom','#ms','#sort'].forEach(s => $(s).addEventListener('input', draw));
$('#lang').value = lang; $('#lang').addEventListener('change', e => { lang = e.target.value; try{localStorage.setItem('lang', lang)}catch(_){}; tr(); draw(); });
tr();
"""

CONCEPT_JS = """
const $ = s => document.querySelector(s);
$('#size').addEventListener('input', e => document.querySelectorAll('.v img').forEach(i => { i.width = i.height = +e.target.value; }));
$('#bw').addEventListener('change', e => document.body.classList.toggle('bw', e.target.checked));
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _link(src: str, dst: Path) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def export(conn: sqlite3.Connection, cfg: Config, min_sources: int = 2, log: Any = print, lang: str = "en") -> int:
    out = cfg.site
    (out / "c").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    t = UI[lang]
    concepts = conn.execute(
        """SELECT k.*, COUNT(DISTINCT p.source_id) AS n_src, COUNT(DISTINCT p.id) AS n
           FROM concept k JOIN depiction_cluster c ON c.concept_id = k.id
           JOIN cluster_member m ON m.cluster_id = c.id JOIN pictogram p ON p.id = m.pictogram_id
           GROUP BY k.id HAVING n_src >= ? ORDER BY n_src DESC""",
        (min_sources,),
    ).fetchall()
    domains = dict(
        conn.execute(
            """SELECT c.concept_id, s.domain FROM depiction_cluster c JOIN cluster_member m ON m.cluster_id=c.id
           JOIN pictogram p ON p.id=m.pictogram_id JOIN source s ON s.id=p.source_id
           GROUP BY c.concept_id, s.domain ORDER BY COUNT(*)"""
        ).fetchall()
    )
    notes = dict(conn.execute("SELECT substr(target, 9), value FROM override WHERE field='notes' AND target LIKE 'concept:%'").fetchall())
    files = {k["id"]: f"{i}" for i, k in enumerate(concepts)}
    index = []
    for i, k in enumerate(concepts):
        clusters = conn.execute("SELECT * FROM depiction_cluster WHERE concept_id=? ORDER BY source_count DESC, size DESC", (k["id"],)).fetchall()
        body, best, best_sha = _concept_body(conn, cfg, k, clusters, files, t, notes.get(k["id"]))
        (out / "c" / f"{files[k['id']]}.html").write_text(_page(esc(k["label"]), body, CONCEPT_JS, "../"))
        index.append(
            {
                "f": files[k["id"]],
                "label": k["label"],
                "labels": json.loads(k["labels"] or "{}"),
                "s": k["n_src"],
                "n": k["n"],
                "k": len(clusters),
                "d": domains.get(k["id"]) or "misc",
                "r": best_sha,
                "b": best,
            }
        )
        if i % 2000 == 0:
            log(f"  html pages: {i}/{len(concepts)}")
    (out / "data" / "concepts.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    doms = sorted({c["d"] for c in index})
    controls = (
        '<div class="controls"><input id="q" type="search" autofocus aria-label="search">'
        '<label><span data-t="domain"></span> <select id="dom"><option value="" data-t="all"></option>'
        + "".join(f"<option>{esc(d)}</option>" for d in doms)
        + '</select></label><label><span data-t="min_sources"></span> <input id="ms" type="number" min="1" value="'
        + str(min_sources)
        + '" style="width:4em"></label>'
        '<label><span data-t="sort"></span> <select id="sort"><option value="sources" data-t="by_sources"></option>'
        '<option value="score" data-t="by_score"></option><option value="name" data-t="by_name"></option></select></label>'
        '<select id="lang" aria-label="language">' + "".join(f"<option>{code}</option>" for code in UI) + "</select>"
        '<span id="count" class="muted"></span></div><div id="grid" class="grid"></div>'
    )
    head = f'<h1 data-t="title"></h1><script type="application/json" id="ui">{json.dumps(UI, ensure_ascii=False)}</script>'
    (out / "index.html").write_text(_page(t["title"], head + controls, INDEX_JS, ""))
    return len(concepts)


def _concept_body(
    conn: sqlite3.Connection, cfg: Config, k: sqlite3.Row, clusters: list[sqlite3.Row], files: dict[str, str], t: dict[str, str], note: str | None
) -> tuple[str, float | None, str | None]:
    labels = json.loads(k["labels"] or "{}")
    parts = [f'<p><a href="../index.html">{t["back"]}</a></p><h1>{esc(k["label"])}</h1>']
    if k["definition"]:
        parts.append(f'<p class="muted">{esc(k["definition"])}</p>')
    if k["parent_id"] in files:
        parts.append(f'<p>{t["broader"]}: <a href="{files[k["parent_id"]]}.html">{esc(k["parent_id"])}</a></p>')
    if len(labels) > 1:
        parts.append(f'<p class="muted">{t["labels"]}: ' + " · ".join(f"{esc(a)}: {esc(b)}" for a, b in labels.items()) + "</p>")
    if note:
        try:
            note = json.loads(note)
        except (json.JSONDecodeError, TypeError):
            pass
        parts.append(f"<h2>{t['notes']}</h2><p>{esc(note)}</p>")
    parts.append(
        f'<div class="controls"><label>{t["size"]} <input id="size" type="range" min="12" max="64" value="24"></label>'
        f'<label><input id="bw" type="checkbox"> {t["bw"]}</label></div>'
    )
    best: float | None = None
    best_sha: str | None = None
    menubar = []
    for n, c in enumerate(clusters, 1):
        rows = conn.execute(
            """SELECT p.id, p.original_name, p.original_url, p.norm_path, p.sha256, p.style, s.name AS sname, s.license_spdx,
                      (SELECT value FROM rating r WHERE r.pictogram_id=p.id AND r.metric='combined' ORDER BY is_override DESC LIMIT 1) AS comb
               FROM cluster_member m JOIN pictogram p ON p.id=m.pictogram_id JOIN source s ON s.id=p.source_id
               WHERE m.cluster_id=? ORDER BY comb DESC""",
            (c["id"],),
        ).fetchall()
        scores: dict[int, dict[str, float]] = defaultdict(dict)
        ids = [r["id"] for r in rows]
        for j in range(0, len(ids), 900):
            part = ids[j : j + 900]
            for pid, metric, value in conn.execute(
                f"SELECT pictogram_id, metric, value FROM rating WHERE value IS NOT NULL AND pictogram_id IN ({','.join('?' * len(part))}) ORDER BY is_override",
                part,
            ):
                scores[pid][metric] = value
        rep = next((r for r in rows if r["id"] == c["representative_id"]), rows[0])
        for r in rows:
            if r["norm_path"] and r["sha256"]:
                _link(str(cfg.resolve(r["norm_path"])), cfg.site / "svg" / f"{r['sha256']}.svg")
        if (rep["comb"] is not None and (best is None or rep["comb"] > best)) or best_sha is None:
            best, best_sha = rep["comb"], rep["sha256"]
        menubar.append(f'<img src="../svg/{rep["sha256"]}.svg" alt="{esc(c["description"] or n)}">')
        sheet = "".join(f'<span class="tile"><img src="../svg/{rep["sha256"]}.svg" width="{s}" height="{s}" alt=""></span>' for s in (12, 16, 24, 48))
        pairs = conn.execute("SELECT concept_b FROM confusion_pair WHERE cluster_id=?", (c["id"],)).fetchall()
        looks = ""
        if pairs:
            looks = f"<p>{t['looks_like']}: " + ", ".join(f'<a href="{files[b]}.html">{esc(b)}</a>' if b in files else esc(b) for (b,) in pairs) + "</p>"
        head_cells = "".join(f"<th>{esc(x)}</th>" for x in SCORE_COLS)
        trs = []
        for r in rows[:200]:
            s = scores[r["id"]]
            cells = "".join(f'<td class="n">{s[x]:.0f}</td>' if x in s else "<td>–</td>" for x in SCORE_COLS)
            name = f'<a href="{esc(r["original_url"])}" rel="noreferrer">{esc(r["original_name"])}</a>' if r["original_url"] else esc(r["original_name"])
            trs.append(
                f'<tr class="v"><td><span class="tile"><img src="../svg/{r["sha256"]}.svg" width="24" height="24" alt="" loading="lazy"></span></td>'
                f"<td>{name}</td><td>{esc(r['sname'])}</td><td>{esc(r['license_spdx'])}</td><td>{esc(r['style'])}</td>{cells}</tr>"
            )
        parts.append(
            f'<section class="cluster"><h2>{t["depiction"]} {n}: {esc(c["description"] or "")}</h2>'
            f"<p><b>{c['convention_strength']:.0f}%</b> {t['convention']} ({c['source_count']} {t['sources']}, {c['size']} {t['variants']})</p>"
            f'<div class="sheet">{sheet}</div>{looks}<div class="scroll"><table><thead><tr><th></th><th>name</th>'
            f"<th>{t['source']}</th><th>{t['license']}</th><th>{t['style']}</th>{head_cells}</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table></div>"
            + (f'<p class="muted">+{len(rows) - 200} {t["variants"]}</p>' if len(rows) > 200 else "")
            + "</section>"
        )
    parts.insert(1, f'<p>{t["menubar"]}:</p><div class="menubar">{"".join(menubar)}</div>')
    parts += _combinations_html(conn, cfg, k["id"])
    return "".join(parts), best, best_sha


def _combinations_html(conn: sqlite3.Connection, cfg: Config, concept_id: str) -> list[str]:
    from .composition.analysis import combinations

    groups = combinations(conn, concept_id)
    if not groups:
        return []
    out = ["<h2>Combinations</h2>"]
    for key in sorted(groups):
        tiles = []
        for m in groups[key][:48]:
            src = cfg.resolve(m["norm_path"])
            if src is None:
                continue
            _link(str(src), cfg.site / "svg" / f"{src.stem}.svg")
            title = f"{m['original_name']} · {m['fit']} · {m['font_type']}"
            tiles.append(f'<span class="tile" title="{esc(title)}"><img src="../svg/{src.stem}.svg" width="24" height="24" alt="" loading="lazy"></span>')
        out.append(f'<h3>{esc(key)} ({len(groups[key])})</h3><div class="sheet">{"".join(tiles)}</div>')
    return out


def _page(title: str, body: str, js: str, root: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline' 'self'\">"
        f"<title>{title}</title><style>{CSS}</style></head><body><header></header><main>{body}</main>"
        f"<script>{js}</script></body></html>"
    )
