import json

from handdown import db, provenance
from handdown.config import Config


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    for pid in (1, 2, 3):
        c.execute("INSERT INTO pictogram (id, source_id, original_id, svg_valid) VALUES (?, 's', ?, 1)", (pid, f"icon{pid}"))
    return c


def test_record_keeps_the_stable_pictogram_identity_and_ignores_repeats(tmp_path, monkeypatch):
    c = _conn(tmp_path, monkeypatch)
    row = dict(pictogram_id=2, field="object", value="wn:cup.n.01", raw="coffee cup", method="vlm", model="m@tokens=64", input="image", run="42")
    assert provenance.record(c, [row, row, {**row, "value": "wn:mug.n.04"}]) == 2
    got = c.execute("SELECT source_id, original_id, value, input, created_at FROM classification ORDER BY id").fetchall()
    assert [tuple(r[:4]) for r in got] == [("s", "icon2", "wn:cup.n.01", "image"), ("s", "icon2", "wn:mug.n.04", "image")]
    assert got[0][4]  # timestamped


def test_backfill_recovers_claude_probe_and_benchmark_classifications(tmp_path, monkeypatch):
    c = _conn(tmp_path, monkeypatch)
    c.execute("INSERT INTO depiction (id, object_id, view, varieties, description, method) VALUES (10, 'wn:cup.n.01', 'side', '[\"steam\"]', 'mug', 'ai')")
    c.execute("INSERT INTO depiction (id, object_id, description, method) VALUES (11, 'wn:key.n.01', 'probe p=0.97', 'probe')")
    c.execute("INSERT INTO style_group (id, depiction_id, representative_id, size, assessed_at) VALUES (1, 10, 1, 2, '2026-09-29T21:28:58+00:00')")
    c.execute("INSERT INTO style_group (id, depiction_id, representative_id, size) VALUES (2, 11, 2, 1)")
    c.execute("INSERT INTO concept (id, label) VALUES ('wn:coffee.n.01', 'coffee')")
    c.execute("INSERT INTO meaning_link VALUES (10, 'wn:coffee.n.01', 'ai', 0.7)")
    c.commit()
    bench_dir = tmp_path / "bench"
    (bench_dir / "results").mkdir(parents=True)
    (bench_dir / "sample.jsonl").write_text(json.dumps({"key": 10, "source_id": "s", "original_id": "icon1"}) + "\n")
    (bench_dir / "results" / "777.jsonl").write_text(
        json.dumps({"key": 10, "model": "ollama:x", "answer": "mug", "seconds": 3.0})
        + "\n"
        + json.dumps({"key": 10, "model": "ollama:y", "error": "missing"})
        + "\n"
    )
    (bench_dir / "text.jsonl").write_text(json.dumps({"key": 10, "state": "coffee; mug", "options": {"wn:cup.n.01": "cup"}, "rules": None}) + "\n")
    (bench_dir / "results" / "text-888.jsonl").write_text(json.dumps({"key": 10, "model": "laya", "answer": "wn:cup.n.01", "p": 0.6}) + "\n")

    counts = provenance.backfill(c, bench_dir)
    assert counts == {"ai": 4, "probe": 1, "bench": 1, "text": 1}
    assert provenance.backfill(c, bench_dir) == {"ai": 0, "probe": 0, "bench": 0, "text": 0}  # repeatable
    rows = {(r["method"], r["field"]): r for r in c.execute("SELECT * FROM classification")}
    ai = rows[("ai", "object")]
    assert (ai["original_id"], ai["value"], ai["raw"], ai["input"], ai["created_at"]) == ("icon1", "wn:cup.n.01", "mug", "image", "2026-09-29T21:28:58+00:00")
    assert rows[("ai", "view")]["value"] == "side" and rows[("ai", "varieties")]["value"] == '["steam"]'
    assert rows[("ai", "meaning")]["value"] == "wn:coffee.n.01"
    assert rows[("probe", "object")]["confidence"] == 0.97 and rows[("probe", "object")]["original_id"] == "icon2"
    bench = rows[("bench", "object")]
    assert (bench["model"], bench["run"], bench["raw"], bench["original_id"]) == ("ollama:x", "777", "mug", "icon1")
    text = rows[("laya", "object")]
    assert (text["input"], text["confidence"], json.loads(text["context"])["text"]) == ("text", 0.6, "coffee; mug")
