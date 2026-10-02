"""Local review app: navigate the hierarchy (idea → symbol → depiction →
style group → image) with samples, and mark classification errors.
Relations within a rung (symbol ↔ symbol, idea ↔ idea) are a separate side
panel, never part of the breadcrumb or the member grids.

Standard library only. Listens on 127.0.0.1; foreign Host headers (DNS
rebinding) and cross-origin POSTs are refused. Everything is escaped, SVGs
are the sanitized normalized files shown through <img>.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import db
from .config import Config

LEVELS = ("style_group", "depiction", "symbol", "idea", "composite")
LEGACY = {"object": "symbol", "meaning": "idea"}  # rungs before symbols and ideas (2026-10-02)
KINDS = ("image", "style_group", "depiction", "symbol", "idea", "object", "meaning")
# sibling relations read from the other side
INVERSE = {"composed of": "part of", "variant of": "has variant", "derived from": "source of", "broader": "narrower"}
CSS = """
body{font:14px system-ui,sans-serif;margin:0;background:#fafaf8;color:#222}main{max-width:1100px;margin:auto;padding:12px 16px}
a{color:#2457c5;text-decoration:none}a:hover{text-decoration:underline}.crumbs{color:#666;margin:6px 0 12px}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:8px 10px;margin:8px 0}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.t{background:#fff;border:1px solid #e3e3e3;border-radius:4px;padding:3px}
.t img{display:block;width:32px;height:32px}.m{color:#666;font-size:12px}h1{font-size:20px}h2{font-size:16px;margin:4px 0}
.flag{float:right;border:1px solid #c33;color:#c33;background:#fff;border-radius:4px;cursor:pointer;font-size:12px}
dialog form label{display:block;margin:4px 0}.big img{width:96px;height:96px}
.split{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:16px;align-items:start}
@media (max-width:760px){.split{grid-template-columns:1fr}}
aside.siblings{border:1px dashed #2457c5;border-radius:8px;padding:8px 12px;background:#f2f6fd}
aside.siblings h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#2457c5}
aside.siblings li{margin:3px 0}.rel{color:#666;font-size:12px}.form{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.05em}
@media (prefers-color-scheme:dark){body{background:#161616;color:#eee}.card,.t{background:#222;border-color:#333}.t img{background:#fff}
aside.siblings{background:#1b2230;border-color:#84abf2}aside.siblings h2{color:#84abf2}a{color:#84abf2}}
"""
JS = """
function flag(kind,id){const d=document.getElementById('fb');d.dataset.kind=kind;d.dataset.id=id;
 document.getElementById('fbt').textContent=kind+' '+id;d.showModal();}
async function send(ev){ev.preventDefault();const d=document.getElementById('fb');const f=ev.target;
 const levels=[...f.querySelectorAll('input[name=l]:checked')].map(x=>x.value);
 const r=await fetch('/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({kind:d.dataset.kind,id:d.dataset.id,levels,correct:f.correct.value,note:f.note.value})});
 d.close();f.reset();document.getElementById('msg').textContent=r.ok?'saved':'error '+r.status;}
"""
DIALOG = (
    "<dialog id=fb><form onsubmit='send(event)'><b>Wrong classification: <span id=fbt></span></b>"
    + "".join(
        f"<label><input type=checkbox name=l value={lv}> {text}</label>"
        for lv, text in (
            ("style_group", "not the same drawing (style group)"),
            ("depiction", "not the same depiction (view / features)"),
            ("symbol", "wrong symbol"),
            ("idea", "wrong idea"),
            ("composite", "wrong composite parts"),
        )
    )
    + "<label>correct value <input name=correct></label><label>note <input name=note size=40></label>"
    "<button>save</button> <button type=button onclick=\"this.closest('dialog').close()\">cancel</button></form></dialog>"
)


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def q(x: Any) -> str:
    return urllib.parse.quote(str(x), safe="")


def flag(kind: str, ident: Any) -> str:
    return f"<button class=flag onclick=\"flag('{kind}','{esc(ident)}')\">⚑ wrong</button>"


def tiles(rows: list[sqlite3.Row], link: bool = True) -> str:
    out = []
    for r in rows:
        if not r["sha256"]:
            continue
        img = f"<span class=t title='{esc(r['original_name'])}'><img src='/svg/{esc(r['sha256'])}.svg' alt='' loading=lazy></span>"
        out.append(f"<a href='/image/{r['id']}'>{img}</a>" if link else img)
    return "<div class=row>" + "".join(out) + "</div>"


class Catalog:
    """Queries behind the pages."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def label(self, cid: str | None) -> str:
        if not cid:
            return "(unknown object)"
        r = self.conn.execute("SELECT label, labels FROM concept WHERE id=?", (cid,)).fetchone()
        if not r:
            return cid
        de = json.loads(r["labels"] or "{}").get("de")
        return r["label"] + (f" · {de}" if de and de != r["label"] else "")

    def group_sample(self, gid: int, n: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT p.id, p.sha256, p.original_name FROM style_member sm JOIN pictogram p ON p.id = sm.pictogram_id
               WHERE sm.style_group_id = ? ORDER BY p.id LIMIT ?""",
            (gid, n),
        ).fetchall()

    def depiction_sample(self, did: int, n: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT p.id, p.sha256, p.original_name FROM style_group g JOIN pictogram p ON p.id = g.representative_id
               WHERE g.depiction_id = ? ORDER BY g.size DESC LIMIT ?""",
            (did, n),
        ).fetchall()

    def symbol_sample(self, sid: str, n: int) -> list[sqlite3.Row]:
        """Images of a symbol's depictions, canonical form first."""
        return self.conn.execute(
            """SELECT p.id, p.sha256, p.original_name FROM symbol_depiction sd JOIN style_group g ON g.depiction_id = sd.depiction_id
               JOIN pictogram p ON p.id = g.representative_id WHERE sd.symbol_id = ?
               ORDER BY sd.form != 'canonical', g.size DESC LIMIT ?""",
            (sid, n),
        ).fetchall()

    def symbols_of(self, did: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.id, s.label, sd.form FROM symbol_depiction sd JOIN symbol s ON s.id = sd.symbol_id WHERE sd.depiction_id = ?", (did,)
        ).fetchall()

    def siblings(self, table: str, ident: str) -> list[tuple[str, str]]:
        """(relation as read from ``ident``, other end) within one rung."""
        a, b = ("symbol_a", "symbol_b") if table == "symbol_relation" else ("concept_a", "concept_b")
        out = [(r[0], r[1]) for r in self.conn.execute(f"SELECT relation, {b} FROM {table} WHERE {a} = ?", (ident,))]
        out += [(INVERSE.get(r[0], r[0]), r[1]) for r in self.conn.execute(f"SELECT relation, {a} FROM {table} WHERE {b} = ?", (ident,))]
        return sorted(set(out))

    def snapshot(self, kind: str, ident: str) -> dict[str, Any]:
        """The classification as it is now, stored with a mark."""
        c = self.conn
        if kind == "image":
            r = c.execute(
                """SELECT sm.style_group_id, g.depiction_id, d.object_id, d.view, d.varieties, d.method FROM style_member sm
                   JOIN style_group g ON g.id = sm.style_group_id JOIN depiction d ON d.id = g.depiction_id
                   WHERE sm.pictogram_id = ?""",
                (int(ident),),
            ).fetchone()
            snap = dict(r) if r else {}
            snap["style_group"] = snap.pop("style_group_id", None)
            snap["depiction"] = snap.pop("depiction_id", None)
            if snap.get("depiction"):
                snap["meanings"] = [m[0] for m in c.execute("SELECT concept_id FROM meaning_link WHERE depiction_id=?", (snap["depiction"],))]
            snap["composite"] = [dict(p) for p in c.execute("SELECT role, label FROM composition_part WHERE pictogram_id=?", (int(ident),))]
            if snap.get("depiction"):
                snap["symbols"] = [r[0] for r in c.execute("SELECT symbol_id FROM symbol_depiction WHERE depiction_id=?", (snap["depiction"],))]
            return snap
        if kind in ("style_group", "depiction"):
            did = int(ident) if kind == "depiction" else c.execute("SELECT depiction_id FROM style_group WHERE id=?", (int(ident),)).fetchone()[0]
            d = c.execute("SELECT id, object_id, view, varieties, method FROM depiction WHERE id=?", (did,)).fetchone()
            return {"depiction": did, **(dict(d) if d else {})}
        if kind == "symbol":
            return {"symbol": ident, "ideas": [r[0] for r in c.execute("SELECT concept_id FROM symbol_idea WHERE symbol_id=?", (ident,))]}
        return {"concept": ident}


def page(title: str, crumbs: str, body: str) -> bytes:
    return (
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)} · handdown</title><style>{CSS}</style><main><div class=crumbs><a href='/'>handdown</a> {crumbs}</div>"
        f"<p id=msg class=m></p>{body}{DIALOG}</main><script>{JS}</script>"
    ).encode()


class Handler(BaseHTTPRequestHandler):
    cfg: Config
    allowed_hosts: set[str]

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        return

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cfg.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.allowed_hosts

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden host")
        path = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in path.path.strip("/").split("/") if p]
        conn = self._conn()
        try:
            cat = Catalog(conn)
            if not parts:
                return self._send(200, self.index(cat, urllib.parse.parse_qs(path.query).get("q", [""])[0]))
            kind, ident = parts[0], "/".join(parts[1:])
            if kind == "svg" and re.fullmatch(r"[0-9a-f]{64}\.svg", ident):
                f = self.cfg.norm_path(ident[:-4])
                return self._send(200, f.read_bytes(), "image/svg+xml") if f.exists() else self._send(404, b"not found")
            view = {
                "idea": self.idea,
                "symbol": self.symbol,
                "meaning": self.idea,  # old links
                "object": self.object,  # the drawn object, now a depiction attribute
                "depiction": self.depiction,
                "group": self.group,
                "image": self.image,
            }.get(kind)
            if view is None:
                return self._send(404, b"not found")
            return self._send(200, view(cat, ident))
        except (ValueError, TypeError, IndexError):
            return self._send(404, b"not found")
        finally:
            conn.close()

    def do_POST(self) -> None:
        origin = self.headers.get("Origin", "")
        if not self._host_ok() or urllib.parse.urlparse(origin).netloc not in self.allowed_hosts:
            return self._send(403, b'{"ok": false}', "application/json")
        if self.path != "/feedback":
            return self._send(404, b'{"ok": false}', "application/json")
        try:
            data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 100_000)))
            kind, ident = str(data["kind"]), str(data["id"])
            levels = [LEGACY.get(lv, lv) for lv in data.get("levels", []) if LEGACY.get(lv, lv) in LEVELS]
            if kind not in KINDS or not levels:
                raise ValueError("bad kind or no level")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self._send(400, b'{"ok": false}', "application/json")
        conn = self._conn()
        try:
            context = Catalog(conn).snapshot(kind, ident)
            conn.execute(
                "INSERT INTO feedback (target_kind, target_id, levels, correct_value, note, context, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    kind,
                    ident,
                    json.dumps(levels),
                    (data.get("correct") or "")[:500] or None,
                    (data.get("note") or "")[:2000] or None,
                    json.dumps(context, default=str),
                    db.now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self._send(200, b'{"ok": true}', "application/json")

    # ---- pages ---------------------------------------------------------
    def index(self, cat: Catalog, query: str) -> bytes:
        if not cat.conn.execute("SELECT 1 FROM symbol_idea LIMIT 1").fetchone():
            return self.meanings_index(cat, query)  # symbols not formed yet
        rows = cat.conn.execute(
            """SELECT i.concept_id, COUNT(*) n FROM symbol_idea i JOIN concept k ON k.id = i.concept_id
               WHERE k.label LIKE ? GROUP BY 1 ORDER BY n DESC LIMIT 60""",
            (f"%{query}%",),
        ).fetchall()
        cards = []
        for cid, n in rows:
            sids = [r[0] for r in cat.conn.execute("SELECT symbol_id FROM symbol_idea WHERE concept_id=? LIMIT 6", (cid,))]
            sample = [x for sid in sids for x in cat.symbol_sample(sid, 1)]
            cards.append(f"<div class=card><h2><a href='/idea/{q(cid)}'>{esc(cat.label(cid))}</a> <span class=m>{n} symbols</span></h2>{tiles(sample)}</div>")
        form = f"<form><input name=q value='{esc(query)}' placeholder='search ideas'> <button>search</button></form>"
        return page("ideas", "", f"<h1>Ideas</h1>{form}{''.join(cards)}")

    def meanings_index(self, cat: Catalog, query: str) -> bytes:
        sql = """SELECT m.concept_id, COUNT(DISTINCT m.depiction_id) n FROM meaning_link m
                 JOIN concept k ON k.id = m.concept_id WHERE k.label LIKE ? GROUP BY 1 ORDER BY n DESC LIMIT 60"""
        rows = cat.conn.execute(sql, (f"%{query}%",)).fetchall()
        cards = []
        for r in rows:
            dep = cat.conn.execute("SELECT depiction_id FROM meaning_link WHERE concept_id=? LIMIT 6", (r[0],)).fetchall()
            sample = [s for d in dep for s in cat.depiction_sample(d[0], 1)]
            cards.append(
                f"<div class=card><h2><a href='/idea/{q(r[0])}'>{esc(cat.label(r[0]))}</a> <span class=m>{r[1]} depictions</span></h2>{tiles(sample)}</div>"
            )
        form = f"<form><input name=q value='{esc(query)}' placeholder='search ideas'> <button>search</button></form>"
        return page(
            "ideas", "", f"<h1>Ideas</h1><p class=m>Symbols are not formed yet: ideas are listed from the meanings of depictions.</p>{form}{''.join(cards)}"
        )

    def sibling_panel(self, cat: Catalog, table: str, ident: str) -> str:
        """Relations within one rung: a side panel apart from the hierarchy."""
        rows = cat.siblings(table, ident)
        kind = "symbol" if table == "symbol_relation" else "idea"
        if kind == "symbol":
            names = {r[0]: r[1] for r in cat.conn.execute(f"SELECT id, label FROM symbol WHERE id IN ({','.join('?' * len(rows))})", [o for _, o in rows])}
        else:
            names = {o: cat.label(o) for _, o in rows}
        items = "".join(f"<li><span class=rel>{esc(rel)}</span> <a href='/{kind}/{q(o)}'>{esc(names.get(o, o))}</a></li>" for rel, o in rows)
        title = "related symbols" if kind == "symbol" else "related ideas"
        return f"<aside class=siblings><h2>{title}</h2><p class=m>within the {kind} rung</p><ul>{items or '<li class=m>none yet</li>'}</ul></aside>"

    def idea(self, cat: Catalog, cid: str) -> bytes:
        rows = cat.conn.execute(
            """SELECT s.id, s.label, s.size, s.wikipedia, i.kind FROM symbol_idea i JOIN symbol s ON s.id = i.symbol_id
               WHERE i.concept_id = ? ORDER BY s.source_count DESC LIMIT 60""",
            (cid,),
        ).fetchall()
        if not rows:  # no symbols for it yet: the depictions named after it, with its related ideas beside
            title = cat.label(cid)
            body = f"<h1>{esc(title)} {flag('idea', cid)}</h1><div class=split><div>{self.drawn_as(cat, cid)}</div>{self.sibling_panel(cat, 'idea_relation', cid)}</div>"
            return page(title, f"› idea › {esc(title)}", body)
        cards = [
            f"<div class=card>{flag('symbol', r['id'])}<h2><a href='/symbol/{q(r['id'])}'>{esc(r['label'])}</a> "
            f"<span class=m>{esc(r['kind'])} · {r['size']} depictions{' · Wikipedia: ' + esc(r['wikipedia']) if r['wikipedia'] else ''}</span></h2>"
            f"{tiles(cat.symbol_sample(r['id'], 8))}</div>"
            for r in rows
        ]
        title = cat.label(cid)
        body = (
            f"<h1>{esc(title)} {flag('idea', cid)}</h1><div class=split><div><p class=m>symbols standing for it</p>{''.join(cards)}</div>"
            f"{self.sibling_panel(cat, 'idea_relation', cid)}</div>"
        )
        return page(title, f"› idea › {esc(title)}", body)

    def symbol(self, cat: Catalog, sid: str) -> bytes:
        s = cat.conn.execute("SELECT * FROM symbol WHERE id=?", (sid,)).fetchone()
        if s is None:
            raise ValueError(sid)
        ideas = cat.conn.execute("SELECT concept_id, kind FROM symbol_idea WHERE symbol_id=? ORDER BY confidence DESC", (sid,)).fetchall()
        crumbs = "› " + ", ".join(f"<a href='/idea/{q(i[0])}'>{esc(cat.label(i[0]))}</a>" for i in ideas) + f" › {esc(s['label'])}"
        deps = cat.conn.execute(
            """SELECT d.*, sd.form FROM symbol_depiction sd JOIN depiction d ON d.id = sd.depiction_id WHERE sd.symbol_id = ?
               ORDER BY sd.form != 'canonical', sd.form, d.source_count DESC LIMIT 80""",
            (sid,),
        ).fetchall()
        cards = [
            f"<div class=card>{flag('depiction', d['id'])}<span class=form>{esc(d['form'])}</span><h2><a href='/depiction/{d['id']}'>view {esc(d['view'])}, "
            f"{esc(', '.join(json.loads(d['varieties'] or '[]')) or 'plain')}</a> <span class=m>{d['size']} images, {d['source_count']} sources</span></h2>"
            f"{tiles(cat.depiction_sample(d['id'], 8))}</div>"
            for d in deps
        ]
        kinds = ", ".join(f"<a href='/idea/{q(i[0])}'>{esc(cat.label(i[0]))}</a> <span class=m>({esc(i[1])})</span>" for i in ideas)
        wiki = (
            f" · <a href='https://en.wikipedia.org/wiki/{q(s['wikipedia'].replace(' ', '_'))}' rel=noreferrer>Wikipedia: {esc(s['wikipedia'])}</a>"
            if s["wikipedia"]
            else ""
        )
        obj = f"drawn object: <a href='/object/{q(s['object_id'])}'>{esc(cat.label(s['object_id']))}</a>" if s["object_id"] else ""
        body = (
            f"<h1>{esc(s['label'])} {flag('symbol', sid)}</h1><p>stands for: {kinds or '–'}</p><p class=m>{obj}{wiki} · formed by {esc(s['method'])}</p>"
            f"<div class=split><div>{''.join(cards)}</div>{self.sibling_panel(cat, 'symbol_relation', sid)}</div>"
        )
        return page(s["label"], crumbs, body)

    def meaning(self, cat: Catalog, mid: str) -> bytes:
        title = cat.label(mid)
        return page(title, f"› idea › {esc(title)}", f"<h1>{esc(title)} {flag('idea', mid)}</h1>{self.drawn_as(cat, mid)}")

    def drawn_as(self, cat: Catalog, mid: str) -> str:
        """The objects and depictions named after a concept (before symbols exist)."""
        from .hierarchy.analysis import applied_to, drawn_as

        cards = []
        for o in drawn_as(cat.conn, mid):
            sample = [s for d in o["depictions"][:8] for s in cat.depiction_sample(d["id"], 1)][:8]
            link = f"<a href='/object/{q(o['object_id'])}'>{esc(o['label'])}</a>" if o["object_id"] else esc(o["label"])
            deps = " ".join(
                f"<a class=m href='/depiction/{d['id']}'>[{esc(d['view'])}, {esc(', '.join(d['varieties']) or 'plain')}]</a>" for d in o["depictions"][:8]
            )
            cards.append(
                f"<div class=card><h2>{link} <span class=m>{o['share']:.0%} of sources ({o['sources']})</span></h2>{tiles(sample)}<div>{deps}</div></div>"
            )
        applied = applied_to(cat.conn, mid)[:30]
        extra = "<p class=m>applied to: " + ", ".join(esc(o["label"]) for o in applied) + "</p>" if applied else ""
        return f"<p class=m>drawn as</p>{''.join(cards)}{extra}"

    def object(self, cat: Catalog, oid: str) -> bytes:
        from .hierarchy.analysis import used_to_mean

        deps = cat.conn.execute("SELECT * FROM depiction WHERE object_id=? ORDER BY source_count DESC LIMIT 60", (oid,)).fetchall()
        cards = [
            f"<div class=card>{flag('depiction', d['id'])}<h2><a href='/depiction/{d['id']}'>view {esc(d['view'])}, "
            f"{esc(', '.join(json.loads(d['varieties'] or '[]')) or 'plain')}</a> <span class=m>{d['size']} images, {d['source_count']} sources, {esc(d['method'])}</span></h2>"
            f"{tiles(cat.depiction_sample(d['id'], 8))}</div>"
            for d in deps
        ]
        means = used_to_mean(cat.conn, oid)[:30]
        mlinks = ", ".join(f"<a href='/idea/{q(c)}'>{esc(lbl)}</a> ({n})" for c, lbl, n in means)
        title = cat.label(oid)
        return page(title, f"› drawn object › {esc(title)}", f"<h1>drawn object: {esc(title)}</h1><p>used to mean: {mlinks}</p>{''.join(cards)}")

    def depiction(self, cat: Catalog, did: str) -> bytes:
        d = cat.conn.execute("SELECT * FROM depiction WHERE id=?", (int(did),)).fetchone()
        if d is None:
            raise ValueError(did)
        groups = cat.conn.execute("SELECT * FROM style_group WHERE depiction_id=? ORDER BY size DESC", (d["id"],)).fetchall()
        cards = [
            f"<div class=card>{flag('style_group', g['id'])}<h2><a href='/group/{g['id']}'>style group {g['id']}</a> "
            f"<span class=m>{g['size']} images, styles {esc(g['styles'])}</span></h2>{tiles(cat.group_sample(g['id'], 6))}</div>"
            for g in groups
        ]
        means = cat.conn.execute("SELECT concept_id, source FROM meaning_link WHERE depiction_id=?", (d["id"],)).fetchall()
        mlinks = ", ".join(f"<a href='/idea/{q(m[0])}'>{esc(cat.label(m[0]))}</a> <span class=m>({esc(m[1])})</span>" for m in means)
        obj = f"<a href='/object/{q(d['object_id'])}'>{esc(cat.label(d['object_id']))}</a>" if d["object_id"] else "(unknown)"
        syms = cat.symbols_of(d["id"])
        crumbs = "".join(f"› <a href='/symbol/{q(x['id'])}'>{esc(x['label'])}</a> " for x in syms) + f"› depiction {d['id']}"
        forms = ", ".join(f"{esc(x['form'])} of <a href='/symbol/{q(x['id'])}'>{esc(x['label'])}</a>" for x in syms)
        head = f"<h1>view {esc(d['view'])}, {esc(', '.join(json.loads(d['varieties'] or '[]')) or 'plain')} {flag('depiction', d['id'])}</h1>"
        info = f"<p>{forms or 'no symbol yet'}</p><p class=m>drawn object: {obj} · named meanings: {mlinks or '–'}</p>"
        return page(f"depiction {d['id']}", crumbs, f"{head}{info}{''.join(cards)}")

    def group(self, cat: Catalog, gid: str) -> bytes:
        g = cat.conn.execute("SELECT * FROM style_group WHERE id=?", (int(gid),)).fetchone()
        if g is None:
            raise ValueError(gid)
        images = cat.group_sample(g["id"], 500)
        crumbs = f"› <a href='/depiction/{g['depiction_id']}'>depiction {g['depiction_id']}</a> › style group {g['id']}"
        return page(f"style group {g['id']}", crumbs, f"<h1>style group {g['id']} {flag('style_group', g['id'])}</h1>{tiles(images)}")

    def image(self, cat: Catalog, pid: str) -> bytes:
        p = cat.conn.execute(
            "SELECT p.*, s.name AS sname, s.license_spdx FROM pictogram p JOIN source s ON s.id = p.source_id WHERE p.id=?", (int(pid),)
        ).fetchone()
        if p is None:
            raise ValueError(pid)
        snap = cat.snapshot("image", str(p["id"]))
        crumbs = ""
        if snap.get("depiction"):
            syms = cat.symbols_of(snap["depiction"])
            crumbs = (
                "".join(f"› <a href='/symbol/{q(x['id'])}'>{esc(x['label'])}</a> " for x in syms)
                + f"› <a href='/depiction/{snap['depiction']}'>depiction {snap['depiction']}</a> "
                f"› <a href='/group/{snap['style_group']}'>style group {snap['style_group']}</a>"
            )
        sizes = "".join(f"<span class=t><img src='/svg/{esc(p['sha256'])}.svg' style='width:{s}px;height:{s}px' alt=''></span>" for s in (16, 24, 48, 96))
        means = ", ".join(f"<a href='/idea/{q(m)}'>{esc(cat.label(m))}</a>" for m in snap.get("meanings", []))
        parts = ", ".join(f"{esc(x['role'])}: {esc(x['label'])}" for x in snap.get("composite", []))
        body = (
            f"<h1>{esc(p['original_name'])} {flag('image', p['id'])}</h1><div class='row big'>{sizes}</div>"
            f"<p>source: {esc(p['sname'])} ({esc(p['license_spdx'])}) · <a href='{esc(p['original_url'])}' rel=noreferrer>original</a></p>"
            f"<p>means: {means or '–'}</p><p>composite: {parts or '–'}</p>"
            f"<p class=m>view {esc(snap.get('view'))} · features {esc(snap.get('varieties'))} · method {esc(snap.get('method'))}</p>"
        )
        return page(p["original_name"] or str(p["id"]), crumbs, body)


def make_server(cfg: Config, port: int = 8765) -> ThreadingHTTPServer:
    db.connect(cfg.db_path).close()  # schema, including the feedback table
    handler = type("BoundHandler", (Handler,), {"cfg": cfg, "allowed_hosts": set()})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    real = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real}", f"localhost:{real}"}
    return server


def feedback_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    by_level: Counter[str] = Counter()
    by_method: Counter[str] = Counter()
    for levels, context in conn.execute("SELECT levels, context FROM feedback WHERE status = 'open'"):
        for lv in json.loads(levels):
            by_level[LEGACY.get(lv, lv)] += 1
        by_method[json.loads(context or "{}").get("method") or "?"] += 1
    return {"open": sum(1 for _ in conn.execute("SELECT 1 FROM feedback WHERE status='open'")), "by_level": dict(by_level), "by_method": dict(by_method)}
