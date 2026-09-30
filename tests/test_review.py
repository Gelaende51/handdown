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
    assert status == 200 and "/meaning/" in index
    meaning = conn.execute("SELECT concept_id FROM meaning_link LIMIT 1").fetchone()[0]
    _, page = get(f"{base}/meaning/{urllib.request.quote(meaning, safe='')}")
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
    assert row[0] == "image" and json.loads(row[1]) == ["object", "meaning"] and row[2] == "mug"
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
    assert summary["by_level"]["object"] == 1
