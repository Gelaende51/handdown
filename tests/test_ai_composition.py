import json

from handdown import ai, db
from handdown.config import Config


def test_ai_composition_overwrites_rule_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p', 'p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s', 'p', 's')")
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="9" height="9"/></svg>')
    conn.execute("INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid) VALUES (1,'s','a','bell-off',?,1)", (str(svg),))
    conn.execute("INSERT INTO composition VALUES (1,'generic','mark','glyph',1,0.5,'rules','t')")
    answer = {
        "1": {
            "parts": [{"role": "base", "label": "bell"}, {"role": "negation", "label": "slash"}],
            "relations": [[0, 1, "crossing"]],
            "kind": "generic",
            "font_type": "mark",
            "fit": "glyph",
        }
    }
    monkeypatch.setattr(ai, "ask", lambda *a, **k: (answer, {"usage": {}, "session_id": "x", "total_cost_usd": 0}))
    out = ai.run_composition(conn, tmp_path / "w", limit=10, log=lambda *_: None)
    assert out["assessed"] == 1
    assert tuple(conn.execute("SELECT method, conflict FROM composition WHERE pictogram_id=1").fetchone()) == ("ai", 0)
    assert [r[0] for r in conn.execute("SELECT role FROM composition_part ORDER BY part_no")] == ["base", "negation"]
    assert conn.execute("SELECT relation FROM composition_relation").fetchone()[0] == "crossing"
    row = conn.execute("SELECT field, value, method, model, run, raw FROM classification").fetchone()
    assert tuple(row[:5]) == ("composition", "generic", "ai", "sonnet", "x") and json.loads(row[5])["parts"][1]["label"] == "slash"


def test_ai_composition_skips_malformed_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    conn = db.connect(Config().db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p', 'p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s', 'p', 's')")
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="9" height="9"/></svg>')
    conn.execute("INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid) VALUES (1,'s','a','x',?,1)", (str(svg),))
    conn.execute("INSERT INTO composition VALUES (1,'generic','mark','glyph',1,0.5,'rules','t')")
    monkeypatch.setattr(ai, "ask", lambda *a, **k: ({"1": {"parts": "nonsense"}}, {"usage": {}, "session_id": "y"}))
    assert ai.run_composition(conn, tmp_path / "w", limit=10, log=lambda *_: None)["assessed"] == 0
    assert conn.execute("SELECT method FROM composition").fetchone()[0] == "rules"
