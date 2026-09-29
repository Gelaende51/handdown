from handdown import ai, db
from handdown.config import Config


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    conn = db.connect(Config().db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="9" height="9"/></svg>')
    for pid in (1, 2):
        conn.execute(
            "INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid) VALUES (?,?,?,?,?,1)",
            (pid, "s", str(pid), "coffee", str(svg)),
        )
    conn.execute(
        "INSERT INTO depiction (id, name_concept_id, object_id, view, varieties, method, representative_id, size, source_count)"
        " VALUES (1,'wn:coffee.n.01',NULL,'unknown','[]','rules',1,2,1)"
    )
    conn.execute("INSERT INTO style_group (id, depiction_id, representative_id, size, source_count, styles) VALUES (1,1,1,1,1,'{}'), (2,1,2,1,1,'{}')")
    conn.execute("INSERT INTO style_member VALUES (1,1), (2,2)")
    return conn


def test_hierarchy_answers_set_object_and_split_varieties(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    answer = {
        "1": {"object": "mug", "view": "side", "features": ["steam"], "meanings": ["coffee", "break"]},
        "2": {"object": "mug", "view": "side", "features": [], "meanings": ["coffee"]},
    }
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    out = ai.run_hierarchy(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 2
    deps = conn.execute("SELECT object_id, view, varieties FROM depiction ORDER BY id").fetchall()
    assert len(deps) == 2  # the steam variety split off
    assert {d[2] for d in deps} == {"[]", '["steam"]'}
    assert all(d[1] == "side" and "mug" in d[0] for d in deps)
    assert conn.execute("SELECT COUNT(*) FROM meaning_link WHERE source='ai'").fetchone()[0] >= 2


def test_hierarchy_ignores_bad_answers(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    answer = {"1": {"object": "mug", "view": "sideways-ish", "features": "steam"}, "2": "nonsense"}
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    out = ai.run_hierarchy(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 1
    assert conn.execute("SELECT view FROM depiction WHERE id=1").fetchone()[0] == "unknown"


def test_siblings_are_assessed_in_later_runs(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    # same view and features as the depiction: updated in place, no split
    answer = {"1": {"object": "mug", "view": "unknown", "features": [], "meanings": ["coffee"]}}
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    assert ai.run_hierarchy(conn, tmp_path / "w", limit=1, log=lambda *_: None)["assessed"] == 1
    assert ai.run_hierarchy(conn, tmp_path / "w", limit=1, log=lambda *_: None)["assessed"] == 1  # the sibling
    assert ai.run_hierarchy(conn, tmp_path / "w", limit=1, log=lambda *_: None)["assessed"] == 0  # nothing left


def test_parallel_calls_give_the_same_result(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    answer = {"1": {"object": "mug", "view": "side", "features": [], "meanings": ["coffee"]}}
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "h"}))
    monkeypatch.setattr(ai, "BATCH", 1)
    out = ai.run_hierarchy(conn, tmp_path / "w", limit=10, workers=2, log=lambda *_: None)
    assert out["assessed"] == 2
    assert conn.execute("SELECT COUNT(*) FROM style_group WHERE assessed_at IS NULL").fetchone()[0] == 0
