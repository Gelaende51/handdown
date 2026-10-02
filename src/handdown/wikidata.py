"""Link WordNet concepts to Wikidata: QIDs and labels in many languages
(German among them, which the Open Multilingual WordNet lacks).

Path: NLTK synset (WordNet 3.0 offset) -> Collaborative Interlingual Index
(globalwordnet/cili on GitHub) -> WordNet 3.1 id -> Wikidata property P8814
("WordNet 3.1 Synset ID") -> item and labels.

``fetch`` needs query.wikidata.org and runs on GitHub Actions; its output is
Wikidata content (CC0), so it may be committed. ``apply`` runs locally.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from .config import USER_AGENT

SPARQL = "https://query.wikidata.org/sparql"
API = "https://www.wikidata.org/w/api.php"
CILI = "https://raw.githubusercontent.com/globalwordnet/cili/master/ili-map-{}.tab"
LANGS = ("en", "de", "fr", "es", "it", "pt", "nl", "pl", "cs", "sv", "da", "fi", "tr", "ru", "uk", "el", "ar", "he", "hi", "ja", "zh", "ko", "id", "vi", "th")


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True)


def fetch(out: Path, log: Any = print) -> int:
    """All Wikidata items with a WordNet 3.1 synset id, with labels (JSONL)."""
    c = _client()
    r = c.get(SPARQL, params={"query": "SELECT ?item ?wn WHERE { ?item wdt:P8814 ?wn }", "format": "json"})
    r.raise_for_status()
    pairs: dict[str, list[str]] = {}
    for b in r.json()["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[1]
        pairs.setdefault(qid, []).append(b["wn"]["value"])
    log(f"{len(pairs)} items with P8814")
    qids = sorted(pairs)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w") as f:
        for i in range(0, len(qids), 50):
            batch = qids[i : i + 50]
            for attempt in range(5):
                resp = c.get(API, params={"action": "wbgetentities", "ids": "|".join(batch), "props": "labels", "languages": "|".join(LANGS), "format": "json"})
                if resp.status_code in (429, 503):
                    time.sleep(5 * 2**attempt)
                    continue
                resp.raise_for_status()
                break
            for qid, ent in resp.json().get("entities", {}).items():
                labels = {k: v["value"] for k, v in (ent.get("labels") or {}).items()}
                for wn in pairs.get(qid, []):
                    f.write(json.dumps({"wn31": wn, "qid": qid, "labels": labels}, ensure_ascii=False, sort_keys=True) + "\n")
                    n += 1
            time.sleep(0.1)
            if i % 5000 == 0:
                log(f"  {i}/{len(qids)}")
    return n


def _cili(version: str, client: httpx.Client) -> dict[str, str]:
    """pwn offset-pos -> ili id (or reverse for 3.1)."""
    r = client.get(CILI.format(version))
    r.raise_for_status()
    out = {}
    for line in r.text.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def apply(conn: sqlite3.Connection, path: Path, log: Any = print) -> int:
    """Set wikidata_qid and add Wikidata labels to WordNet concepts.
    Existing labels are kept; Wikidata fills the languages that are missing."""
    from .concepts import wordnet

    wn = wordnet()
    c = _client()
    ili30 = _cili("pwn30", c)  # "00001740-a" -> "i1"
    ili31 = {v: k for k, v in _cili("pwn31", c).items()}  # "i1" -> "00001740-a"
    wd: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line:
            rec = json.loads(line)
            wd[rec["wn31"]] = rec
    n = 0
    for cid, synset, labels_json in conn.execute("SELECT id, wordnet_synset, labels FROM concept WHERE wordnet_synset IS NOT NULL").fetchall():
        s = wn.synset(synset)
        pos = "a" if s.pos() == "s" else s.pos()
        key30 = f"{s.offset():08d}-{pos}"
        ili = ili30.get(key30) or ili30.get(f"{s.offset():08d}-{s.pos()}")
        rec = wd.get(ili31.get(ili, "")) if ili else None
        if not rec:
            continue
        labels = json.loads(labels_json or "{}")
        for lang, label in rec["labels"].items():
            labels.setdefault(lang, label)
        conn.execute("UPDATE concept SET wikidata_qid=?, labels=? WHERE id=?", (rec["qid"], json.dumps(labels, ensure_ascii=False), cid))
        n += 1
    conn.commit()
    log(f"{n} concepts linked to Wikidata")
    return n


SYMBOL = "Q80071"  # symbol: the class tree whose members are the catalog's symbols (symbols.py)


def fetch_symbols(out: Path, client: httpx.Client | None = None, chunk: int = 200, log: Any = print) -> int:
    """Wikidata items that are symbols (instance or subclass of a class below
    *symbol*) and have an English Wikipedia article, with label and aliases
    (JSONL). The class tree is read first and the members fetched in chunks of
    classes, which keeps each query under the service's time limit."""
    c = client or _client()

    def query(q: str) -> list[dict]:
        for attempt in range(5):
            r = c.get(SPARQL, params={"query": q, "format": "json"})
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * 2**attempt)
                continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        r.raise_for_status()
        return []

    classes = sorted({b["c"]["value"].rsplit("/", 1)[1] for b in query(f"SELECT DISTINCT ?c WHERE {{ ?c wdt:P279* wd:{SYMBOL} }}")})
    log(f"{len(classes)} classes below symbol")
    items: dict[str, dict] = {}
    for i in range(0, len(classes), chunk):
        values = " ".join(f"wd:{q}" for q in classes[i : i + chunk])
        rows = query(
            f"""SELECT ?item ?article ?label (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases) WHERE {{
                  VALUES ?c {{ {values} }}
                  ?item wdt:P31|wdt:P279 ?c .
                  ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> .
                  OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
                  OPTIONAL {{ ?item skos:altLabel ?alias FILTER(LANG(?alias) = "en") }}
                }} GROUP BY ?item ?article ?label"""
        )
        for b in rows:
            qid = b["item"]["value"].rsplit("/", 1)[1]
            title = b["article"]["value"].rsplit("/wiki/", 1)[1].replace("_", " ")
            aliases = [a for a in b.get("aliases", {}).get("value", "").split("|") if a]
            items[qid] = {"qid": qid, "label": b.get("label", {}).get("value", title), "aliases": aliases, "wikipedia": unquote(title)}
        time.sleep(1)
        log(f"  {min(i + chunk, len(classes))}/{len(classes)} classes, {len(items)} items")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for qid in sorted(items):
            f.write(json.dumps(items[qid], ensure_ascii=False, sort_keys=True) + "\n")
    return len(items)
