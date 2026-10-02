import json
import threading
import urllib.error
import urllib.request

import pytest

from handdown.hierarchy import group as hg


@pytest.fixture
def app(tmp_path, monkeypatch):
    from test_hierarchy_group import _catalog  # tests/ is on sys.path (no package)

    from handdown import review

    cfg, conn = _catalog(tmp_path, monkeypatch)
    hg.run(conn, log=lambda *_: None)
    conn.execute("UPDATE pictogram SET original_name = original_name || '<script>x</script>' WHERE id = (SELECT MIN(id) FROM pictogram)")
    conn.commit()
    server = review.make_server(cfg, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", conn
    server.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode()


def post(url, data, origin):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_hierarchy_pages_show_samples_and_escape(app):
    base, conn = app
    status, index = get(base + "/")
    assert status == 200 and "/idea/" in index  # before symbols are formed: ideas from the depictions' meanings
    meaning = conn.execute("SELECT concept_id FROM meaning_link LIMIT 1").fetchone()[0]
    _, page = get(f"{base}/idea/{urllib.request.quote(meaning, safe='')}")
    assert ("<img" in page and "/object/" in page) or "/depiction/" in page
    dep = conn.execute("SELECT id FROM depiction LIMIT 1").fetchone()[0]
    _, page = get(f"{base}/depiction/{dep}")
    assert "/group/" in page and "<img" in page
    img = conn.execute("SELECT MIN(id) FROM pictogram").fetchone()[0]
    _, page = get(f"{base}/image/{img}")
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "/depiction/" in page  # breadcrumb up the hierarchy
    status, svg = get(f"{base}/svg/{conn.execute('SELECT sha256 FROM pictogram WHERE id=?', (img,)).fetchone()[0]}.svg")
    assert status == 200 and svg.startswith("<svg")


def test_feedback_is_stored_with_snapshot(app):
    base, conn = app
    img = conn.execute("SELECT MIN(id) FROM pictogram").fetchone()[0]
    status, body = post(
        base + "/feedback", {"kind": "image", "id": str(img), "levels": ["object", "meaning"], "correct": "mug", "note": "it is a mug"}, origin=base
    )
    assert status == 200 and body["ok"]
    row = conn.execute("SELECT target_kind, levels, correct_value, context FROM feedback").fetchone()
    assert row[0] == "image" and json.loads(row[1]) == ["symbol", "idea"] and row[2] == "mug"  # old level names map to the new rungs
    assert "depiction" in json.loads(row[3])  # snapshot of the current classification


def test_foreign_origin_and_host_are_refused(app):
    base, _ = app
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/feedback", {"kind": "image", "id": "1", "levels": ["object"]}, origin="https://evil.example")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/", headers={"Host": "evil.example"})
    assert e.value.code == 403


def test_feedback_summary(app):
    from handdown import review

    base, conn = app
    img = conn.execute("SELECT MIN(id) FROM pictogram").fetchone()[0]
    post(base + "/feedback", {"kind": "image", "id": str(img), "levels": ["object"]}, origin=base)
    summary = review.feedback_summary(conn)
    assert summary["by_level"]["symbol"] == 1


def _symbols(conn):
    from handdown import symbols

    symbols.form(conn, min_sources=1, log=lambda *_: None)
    a, b = [r[0] for r in conn.execute("SELECT id FROM symbol ORDER BY id LIMIT 2")]
    conn.execute("INSERT INTO symbol_relation (symbol_a, symbol_b, relation) VALUES (?, ?, 'composed of')", (a, b))
    idea = conn.execute("SELECT concept_id FROM symbol_idea WHERE symbol_id=?", (a,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO concept (id, label) VALUES ('term:broader-idea', 'broader idea')")
    conn.execute("INSERT INTO idea_relation VALUES (?, 'term:broader-idea', 'broader', 'wordnet')", (idea,))
    conn.commit()
    return a, b, idea


def test_idea_and_symbol_pages_follow_the_rungs(app):
    base, conn = app
    a, _b, idea = _symbols(conn)
    _, index = get(base + "/")
    assert "/idea/" in index
    _, page = get(f"{base}/idea/{urllib.request.quote(idea, safe='')}")
    assert f"/symbol/{urllib.request.quote(a, safe='')}" in page and "<img" in page
    assert any(k in page for k in ("convention", "resemblance"))  # the kind of sign
    _, page = get(f"{base}/symbol/{urllib.request.quote(a, safe='')}")
    assert "/depiction/" in page and "canonical" in page
    assert f"/idea/{urllib.request.quote(idea, safe='')}" in page.split("</div>", 1)[0]  # breadcrumb up to the idea
    dep = conn.execute("SELECT depiction_id FROM symbol_depiction WHERE symbol_id=?", (a,)).fetchone()[0]
    _, page = get(f"{base}/depiction/{dep}")
    assert f"/symbol/{urllib.request.quote(a, safe='')}" in page and "drawn object" in page


def test_siblings_are_a_separate_panel_with_named_relations(app):
    base, conn = app
    a, b, idea = _symbols(conn)
    _, page = get(f"{base}/symbol/{urllib.request.quote(a, safe='')}")
    aside = page.split("<aside class=siblings", 1)[1].split("</aside>", 1)[0]
    assert "composed of" in aside and f"/symbol/{urllib.request.quote(b, safe='')}" in aside
    _, page = get(f"{base}/symbol/{urllib.request.quote(b, safe='')}")
    assert "part of" in page.split("<aside class=siblings", 1)[1]  # the inverse, read from the other side
    _, page = get(f"{base}/idea/{urllib.request.quote(idea, safe='')}")
    assert "broader" in page.split("<aside class=siblings", 1)[1] and "/idea/term%3Abroader-idea" in page
    _, page = get(f"{base}/idea/term%3Abroader-idea")
    assert "narrower" in page.split("<aside class=siblings", 1)[1]


def test_old_routes_still_answer(app):
    base, conn = app
    meaning = conn.execute("SELECT concept_id FROM meaning_link LIMIT 1").fetchone()[0]
    obj = conn.execute("SELECT object_id FROM depiction WHERE object_id IS NOT NULL LIMIT 1").fetchone()[0]
    assert get(f"{base}/meaning/{urllib.request.quote(meaning, safe='')}")[0] == 200
    assert get(f"{base}/object/{urllib.request.quote(obj, safe='')}")[0] == 200


@pytest.fixture
def sourcing(tmp_path, monkeypatch):
    from handdown import db, review
    from handdown.config import Config

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name, kind, found_via) VALUES ('github', 'GitHub', 'code-host', 'seed'), ('iconify', 'Iconify', 'aggregator', 'seed')")
    c.execute(
        "INSERT INTO search_log (id, query, engine, seed) VALUES (1, 'topic:icons', 'github', 'seed'), (2, 'readme:a/list', 'readme', 'source:gh:a/list')"
    )
    c.execute(
        "INSERT INTO source (id, platform_id, name, harvest_status, adapter, license_spdx, found_via) VALUES "
        "('gh:a/list', 'github', 'awesome list', 'rejected', 'git-svg', 'MIT', 'search:1'), "
        "('gh:b/icons', 'github', 'b icons', 'harvested', 'git-svg', 'MIT', 'search:2'), "
        "('iconify:mdi', 'iconify', 'Material Design Icons', 'harvested', 'iconify', 'Apache-2.0', 'platform:iconify')"
    )
    for pid, src in ((1, "gh:b/icons"), (2, "gh:b/icons"), (3, "iconify:mdi")):
        c.execute(
            "INSERT INTO pictogram (id, source_id, original_id, original_name, sha256, measured_at, svg_valid) VALUES (?, ?, ?, ?, ?, 't', 1)",
            (pid, src, f"i{pid}", f"icon {pid}", f"{pid:064x}"),
        )
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (10, NULL, 'rules')")
    gid = c.execute("INSERT INTO style_group (depiction_id, representative_id, size) VALUES (10, 1, 1) RETURNING id").fetchone()[0]
    c.execute("INSERT INTO style_member VALUES (?, 1)", (gid,))
    c.commit()
    server = review.make_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", c
    server.shutdown()


def test_sourcing_map_shows_platforms_and_where_they_were_found(sourcing):
    base, _ = sourcing
    _, page = get(base + "/sources")
    assert "/platform/github" in page and "/platform/iconify" in page
    assert "readme" in page.lower() and "github" in page  # discovery channels and platform-to-platform edges
    _, page = get(base + "/platform/github")
    assert "/source/gh%3Ab%2Ficons" in page and "harvested" in page and "rejected" in page


def test_source_page_links_discovery_pictograms_and_progress(sourcing):
    base, _ = sourcing
    _, page = get(base + "/source/gh%3Ab%2Ficons")
    assert "/source/gh%3Aa%2Flist" in page  # found in the README of the awesome list
    assert "/image/1" in page and "/image/2" in page
    assert "grouped" in page and "1 of 2" in page  # one of two pictograms is in a style group
    _, page = get(base + "/source/gh%3Aa%2Flist")
    assert "/source/gh%3Ab%2Ficons" in page  # what this source led to
    _, page = get(base + "/image/1")
    assert "/source/gh%3Ab%2Ficons" in page
