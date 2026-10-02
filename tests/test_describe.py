import json

from handdown import db, describe
from handdown.config import Config

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/></svg>'


def _catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    (tmp_path / "data" / "norm").mkdir(parents=True)
    (tmp_path / "data" / "norm" / "a.svg").write_text(SVG)
    for pid, name, has_text in ((1, "toilet-sign", 1), (2, "parking", 1), (3, "house", 0)):
        c.execute(
            "INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, sha256, svg_valid, measured_at, has_text)"
            " VALUES (?,?,?,?,?,?,1,'t',?)",
            (pid, "s", f"i{pid}", name, "data/norm/a.svg", f"{pid:064x}", has_text),
        )
    c.commit()
    return c, cfg


ANSWER = {
    "1": {
        "text": [{"text": "WC", "script": "latin", "position": "bottom"}, {"text": "男", "script": "han", "position": "top left"}],
        "tags": ["direction:left", "corners:rounded", "feature:person", "frame:square", "view:front", "mood:happy", "feature:small icon"],
        "residual": "two figures separated by a vertical bar",
        "description": "A square sign with two standing figures above the letters WC.",
        "interpretation": "Toilets for men and women.",
    },
    "2": {"text": [], "tags": ["feature:car"], "residual": "", "description": "A car seen from the side.", "interpretation": "Parking."},
}


def test_describe_stores_text_groups_tags_and_descriptions(tmp_path, monkeypatch):
    from handdown import ai

    c, cfg = _catalog(tmp_path, monkeypatch)
    seen = []

    def fake_ask(image, text, system, workdir, model, effort):
        seen.append((model, effort, text))
        return ANSWER, {"usage": {}, "total_cost_usd": 0.02, "session_id": "d1"}

    monkeypatch.setattr(ai, "ask", fake_ask)
    stats = describe.run(c, cfg, [1, 2], batch=10, workdir=tmp_path / "w")
    assert stats == {"described": 2, "text_groups": 2, "tags": 6, "rejected_tags": 1, "cost_usd": 0.02}
    assert "direction" in seen[0][2] and "corners" in seen[0][2]  # the vocabulary goes with the request
    groups = c.execute("SELECT group_no, text, script, position FROM pictogram_text WHERE pictogram_id=1 ORDER BY group_no").fetchall()
    assert [tuple(g) for g in groups] == [(0, "WC", "latin", "bottom"), (1, "男", "han", "top left")]
    d = c.execute("SELECT residual, description, interpretation, tags FROM pictogram_description WHERE pictogram_id=1").fetchone()
    assert "mood:happy" in d[0]  # outside the vocabulary: kept as residual text
    assert d[1].startswith("A square sign") and d[2] == "Toilets for men and women."
    assert "feature:badge" not in json.loads(d[3]) and "feature:person" in json.loads(d[3])
    assert "feature:small icon" not in json.loads(d[3])  # normalised: "small icon" carries no feature
    tags = {r[0] for r in c.execute("SELECT tag FROM pictogram_tag WHERE pictogram_id=1")}
    assert {"direction:left", "frame:square", "text:latin", "text:han", "feature:text", "described"} <= tags
    assert c.execute("SELECT COUNT(*) FROM classification WHERE method='ai' AND field IN ('text','description','tag')").fetchone()[0] >= 3


def test_search_finds_text_names_and_descriptions(tmp_path, monkeypatch):
    from handdown import ai

    c, cfg = _catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (ANSWER, {"usage": {}, "total_cost_usd": 0, "session_id": "d1"}))
    describe.run(c, cfg, [1, 2], batch=10, workdir=tmp_path / "w")
    assert [r["pictogram_id"] for r in describe.search(c, "WC")] == [1]
    assert [r["pictogram_id"] for r in describe.search(c, "男")] == [1]
    assert [r["pictogram_id"] for r in describe.search(c, "parking")] == [2]
    assert describe.search(c, "nothing-like-this") == []


def test_sample_mixes_text_composites_and_random(tmp_path, monkeypatch):
    c, _cfg = _catalog(tmp_path, monkeypatch)
    c.execute("INSERT INTO composition (pictogram_id, kind, method) VALUES (3, 'generic', 'rules')")
    c.commit()
    ids = describe.sample(c, n=3, seed=1)
    assert set(ids) == {1, 2, 3}


def test_tag_build_keeps_description_tags(tmp_path, monkeypatch):
    from handdown import ai, tags

    c, cfg = _catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (ANSWER, {"usage": {}, "total_cost_usd": 0, "session_id": "d1"}))
    describe.run(c, cfg, [1, 2], batch=10, workdir=tmp_path / "w")
    tags.build(c, cfg, workers=1, min_count=1)
    got = {r[0] for r in c.execute("SELECT tag FROM pictogram_tag WHERE pictogram_id=1")}
    assert {"direction:left", "text:han", "described", "feature:person"} <= got


def test_review_shows_text_groups_descriptions_and_search(tmp_path, monkeypatch):
    import threading
    import urllib.parse
    import urllib.request

    from handdown import ai, review

    c, cfg = _catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (ANSWER, {"usage": {}, "total_cost_usd": 0, "session_id": "d1"}))
    describe.run(c, cfg, [1, 2], batch=10, workdir=tmp_path / "w")
    server = review.make_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(url):
        with urllib.request.urlopen(base + url) as r:
            return r.read().decode()

    try:
        page = get("/image/1")
        assert "WC" in page and "男" in page and "text part" in page  # text groups as compound parts
        assert "Toilets for men and women." in page and "two figures separated" in page
        assert "/browse?tag=direction%3Aleft" in page  # tags link to the filter
        page = get("/search?q=" + urllib.parse.quote("WC"))
        assert "/image/1" in page and "/image/2" not in page
        assert "/image/2" in get("/search?q=parking")
    finally:
        server.shutdown()
