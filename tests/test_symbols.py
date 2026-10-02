from handdown import db, symbols
from handdown.config import Config

# depiction id: (object, [(meaning, source, confidence)], [sources of its pictograms])
DEPICTIONS = {
    1: ("wn:diskette.n.01", [("term:save", "name", 0.9)], ["s1", "s2", "s3"]),
    2: ("wn:diskette.n.01", [("term:save", "name", 0.9)], ["s4"]),
    3: ("wn:arrow.n.01", [("term:download", "name", 0.9)], ["s1"]),  # too few sources: joins the literal arrow
    4: ("wn:arrow.n.01", [("term:upload", "name", 0.9)], ["s1", "s2", "s3"]),
    5: ("wn:mug.n.04", [("wn:mug.n.04", "name", 1.0)], ["s1", "s2"]),  # literal
    6: ("wn:tray.n.01", [("term:save", "ai", 0.7)], ["s5", "s6", "s7"]),
    7: ("wn:padlock.n.01", [("wn:lock.v.01", "name", 0.9)], ["s1", "s2", "s3"]),
    8: ("wn:padlock.n.01", [("wn:unlock.v.01", "name", 0.9)], ["s1", "s2", "s3"]),
    9: ("wn:bell.n.01", [("term:mute", "name", 0.9)], ["s1", "s2", "s3"]),  # a composite: bell with a slash
    10: ("wn:bell.n.01", [("wn:bell.n.01", "name", 1.0)], ["s1", "s2", "s3"]),
    11: ("wn:arrow.n.01", [("wn:arrow.n.01", "name", 1.0)], ["s8"]),
}
LABELS = {
    "wn:diskette.n.01": "floppy disk",
    "term:save": "save",
    "wn:arrow.n.01": "arrow",
    "term:download": "download",
    "term:upload": "upload",
    "wn:mug.n.04": "mug",
    "wn:tray.n.01": "tray",
    "wn:padlock.n.01": "padlock",
    "wn:lock.v.01": "lock",
    "wn:unlock.v.01": "unlock",
    "wn:bell.n.01": "bell",
    "term:mute": "mute",
    "wn:drinking_vessel.n.01": "drinking vessel",
}


def _catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    for s in {s for _, _, srcs in DEPICTIONS.values() for s in srcs}:
        c.execute("INSERT INTO source (id, platform_id, name) VALUES (?, 'p', ?)", (s, s))
    for cid, label in LABELS.items():
        c.execute("INSERT INTO concept (id, label) VALUES (?, ?)", (cid, label))
    pid = 0
    for did, (obj, meanings, srcs) in DEPICTIONS.items():
        c.execute("INSERT INTO depiction (id, object_id, method, size, source_count) VALUES (?, ?, 'rules', ?, ?)", (did, obj, len(srcs), len(srcs)))
        gid = c.execute("INSERT INTO style_group (depiction_id, size) VALUES (?, ?) RETURNING id", (did, len(srcs))).fetchone()[0]
        for s in srcs:
            pid += 1
            c.execute("INSERT INTO pictogram (id, source_id, original_id) VALUES (?, ?, ?)", (pid, s, f"{did}-{pid}"))
            c.execute("INSERT INTO style_member VALUES (?, ?)", (gid, pid))
        for concept, source, conf in meanings:
            c.execute("INSERT INTO meaning_link VALUES (?, ?, ?, ?)", (did, concept, source, conf))
    # depiction 9's first pictogram is a composite whose base part shows depiction 10 (the bell)
    composite = c.execute(
        "SELECT m.pictogram_id FROM style_member m JOIN style_group g ON g.id = m.style_group_id WHERE g.depiction_id = 9 LIMIT 1"
    ).fetchone()[0]
    c.execute("INSERT INTO composition (pictogram_id, kind, method) VALUES (?, 'generic', 'rules')", (composite,))
    c.execute(
        "INSERT INTO composition_part (pictogram_id, part_no, role, depiction_id) VALUES (?, 0, 'base', 10), (?, 1, 'negation', NULL)", (composite, composite)
    )
    c.commit()
    return c


def test_symbols_form_from_object_and_leading_idea(tmp_path, monkeypatch):
    c = _catalog(tmp_path, monkeypatch)
    symbols.form(c, min_sources=3)
    members = {}
    for sid, did, form in c.execute("SELECT symbol_id, depiction_id, form FROM symbol_depiction"):
        members.setdefault(sid, {})[did] = form
    labels = {r[0]: r[1] for r in c.execute("SELECT id, label FROM symbol")}
    by_label = {labels[s]: m for s, m in members.items()}
    assert by_label["floppy disk (save)"] == {1: "canonical", 2: "variant"}
    assert by_label["arrow"] == {3: "variant", 11: "canonical"} or by_label["arrow"] == {3: "canonical", 11: "variant"}
    assert by_label["arrow (upload)"] == {4: "canonical"}
    assert by_label["mug"] == {5: "canonical"}
    assert "tray (save)" in by_label and "padlock (lock)" in by_label and "padlock (unlock)" in by_label


def test_ideas_and_kinds(tmp_path, monkeypatch):
    c = _catalog(tmp_path, monkeypatch)
    symbols.form(c, min_sources=3)
    ideas = {(r[0], r[1]): r[2] for r in c.execute("SELECT s.label, i.concept_id, i.kind FROM symbol_idea i JOIN symbol s ON s.id = i.symbol_id")}
    assert ideas[("floppy disk (save)", "term:save")] == "convention"
    assert ideas[("mug", "wn:mug.n.04")] == "resemblance"


def test_sibling_relations(tmp_path, monkeypatch):
    c = _catalog(tmp_path, monkeypatch)
    symbols.form(c, min_sources=3)
    rel = {
        (r[0], r[1], r[2])
        for r in c.execute("SELECT a.label, b.label, r.relation FROM symbol_relation r JOIN symbol a ON a.id = r.symbol_a JOIN symbol b ON b.id = r.symbol_b")
    }
    assert ("tray (save)", "floppy disk (save)", "same idea") in rel or ("floppy disk (save)", "tray (save)", "same idea") in rel
    assert ("padlock (lock)", "padlock (unlock)", "opposite of") in rel or ("padlock (unlock)", "padlock (lock)", "opposite of") in rel
    assert ("bell (mute)", "bell", "composed of") in rel


def test_idea_relations_from_wordnet(tmp_path, monkeypatch):
    c = _catalog(tmp_path, monkeypatch)
    symbols.form(c, min_sources=3)
    symbols.idea_relations(c, ["wn:mug.n.04", "wn:drinking_vessel.n.01", "wn:arrow.n.01"])
    rel = {tuple(r) for r in c.execute("SELECT concept_a, concept_b, relation FROM idea_relation")}
    assert ("wn:mug.n.04", "wn:drinking_vessel.n.01", "broader") in rel  # concept_b is broader than concept_a
    assert not any("wn:arrow.n.01" in r[:2] for r in rel)


def test_rerun_keeps_claude_and_manual_symbols(tmp_path, monkeypatch):
    c = _catalog(tmp_path, monkeypatch)
    symbols.form(c, min_sources=3)
    c.execute("INSERT INTO symbol (id, label, method) VALUES ('sym:heart-symbol', 'heart symbol', 'manual')")
    c.commit()
    symbols.form(c, min_sources=3)
    assert c.execute("SELECT COUNT(*) FROM symbol WHERE id='sym:heart-symbol'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM symbol WHERE label='floppy disk (save)'").fetchone()[0] == 1


def test_wikidata_symbol_list_is_fetched_in_class_chunks(tmp_path):
    import json

    import httpx

    from handdown import wikidata

    queries = []

    def handler(req):
        q = req.url.params.get("query", "")
        queries.append(q)
        if "wdt:P279* wd:Q80071" in q and "VALUES" not in q:
            return httpx.Response(200, json={"results": {"bindings": [{"c": {"value": f"http://www.wikidata.org/entity/Q{n}"}} for n in (80071, 1, 2)]}})
        rows = [
            {
                "item": {"value": "http://www.wikidata.org/entity/Q1131868"},
                "article": {"value": "https://en.wikipedia.org/wiki/Heart_symbol"},
                "label": {"value": "heart symbol"},
                "aliases": {"value": "love heart|♥"},
            }
        ]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    out = tmp_path / "symbols.jsonl"
    n = wikidata.fetch_symbols(out, client=httpx.Client(transport=httpx.MockTransport(handler)), chunk=2, log=lambda *_: None)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert n == 1 and rows == [{"qid": "Q1131868", "label": "heart symbol", "aliases": ["love heart", "♥"], "wikipedia": "Heart symbol"}]
    assert sum("VALUES" in q for q in queries) == 2  # 3 classes in chunks of 2


def test_symbols_link_to_wikipedia_by_label(tmp_path, monkeypatch):
    import json

    c = _catalog(tmp_path, monkeypatch)
    c.execute("INSERT INTO concept (id, label) VALUES ('wn:heart.n.01', 'heart'), ('term:love', 'love')")
    c.execute("INSERT INTO symbol (id, label, object_id, lead_id, method) VALUES ('sym:heart-love', 'heart (love)', 'wn:heart.n.01', 'term:love', 'rules')")
    c.execute("INSERT INTO symbol (id, label, object_id, method) VALUES ('sym:mug', 'mug', 'wn:mug.n.04', 'rules')")
    c.commit()
    listing = tmp_path / "symbols.jsonl"
    listing.write_text(json.dumps({"qid": "Q1131868", "label": "heart symbol", "aliases": ["love heart"], "wikipedia": "Heart symbol"}) + "\n")
    assert symbols.link_wikipedia(c, listing) == 1
    assert tuple(c.execute("SELECT wikidata_qid, wikipedia FROM symbol WHERE id='sym:heart-love'").fetchone()) == ("Q1131868", "Heart symbol")
    assert c.execute("SELECT wikidata_qid FROM symbol WHERE id='sym:mug'").fetchone()[0] is None


def test_wikidata_symbols_takes_the_output_file_as_an_argument():
    from typer.testing import CliRunner

    from handdown.cli import app

    result = CliRunner().invoke(app, ["wikidata-symbols", "--help"])
    assert result.exit_code == 0 and "OUT" in result.output.upper()
