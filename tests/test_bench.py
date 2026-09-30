import json

from handdown import bench, db
from handdown.config import Config

SQUARE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16"/></svg>'


def _catalog(c, sources=6, per_source=5):
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    pid = 0
    for s in range(sources):
        adapter = "font" if s == 0 else "iconify"
        c.execute("INSERT INTO source (id, platform_id, name, adapter, harvest_status) VALUES (?,?,?,?,'harvested')", (f"s{s}", "p", f"s{s}", adapter))
        for _ in range(per_source):
            pid += 1
            did = c.execute("INSERT INTO depiction (object_id, method) VALUES ('wn:cup.n.01', 'ai') RETURNING id").fetchone()[0]
            for extra, size in ((0, 1), (1000, 3)):  # the larger style group is the representative
                c.execute("INSERT INTO pictogram (id, source_id, original_id, svg_valid) VALUES (?,?,?,1)", (pid + extra, f"s{s}", f"icon{pid + extra}"))
                c.execute("INSERT INTO style_group (depiction_id, representative_id, size) VALUES (?,?,?)", (did, pid + extra, size))
    c.execute("INSERT INTO depiction (object_id, method) VALUES ('wn:key.n.01', 'rules')")  # not Claude-assessed
    c.commit()


def test_sample_caps_per_source_skips_slow_adapters_and_uses_largest_group(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)
    sample, sources = bench.make_sample(c, n=12, per_source=3, seed=1)
    assert len(sample) == 12
    assert all(r["source_id"] != "s0" for r in sample)  # font adapter excluded
    per = {}
    for r in sample:
        per[r["source_id"]] = per.get(r["source_id"], 0) + 1
    assert max(per.values()) <= 3
    assert all(int(r["original_id"][4:]) > 1000 for r in sample)
    assert {s["id"] for s in sources} == set(per)
    assert bench.make_sample(c, n=12, per_source=3, seed=1)[0] == sample  # seeded


def test_prepare_leaves_only_the_sample_to_process(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)
    sample = [{"key": 1, "source_id": "s1", "original_id": "icon1006"}]
    assert bench.prepare(c, sample) == 59
    pending = c.execute("SELECT source_id, original_id FROM pictogram WHERE measured_at IS NULL").fetchall()
    assert [tuple(r) for r in pending] == [("s1", "icon1006")]


def test_run_writes_one_answer_per_key_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    norm = tmp_path / "data" / "norm" / "sq.svg"
    norm.parent.mkdir(parents=True)
    norm.write_text(SQUARE)
    for i in (1, 2, 3):
        c.execute("INSERT INTO pictogram (id, source_id, original_id, norm_path, svg_valid) VALUES (?,?,?,?,1)", (i, "s", f"i{i}", "data/norm/sq.svg"))
    c.commit()
    sample = [{"key": 10 + i, "source_id": "s", "original_id": f"i{i}"} for i in (1, 2, 3, 4)]
    calls = []

    def ask(images):
        calls.append(len(images))
        assert images[0].size == (256, 256)
        return ["square"] * len(images)

    out = tmp_path / "res.jsonl"
    stats = bench.run(c, cfg, sample, ask, "test:m", out, batch=2)
    assert stats == {"answered": 3, "missing": 1} and calls == [2, 1]
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert {r["key"] for r in rows} == {11, 12, 13, 14}
    assert next(r for r in rows if r["key"] == 14)["error"] == "missing"
    assert all(r["model"] == "test:m" for r in rows)
    assert bench.run(c, cfg, sample, ask, "test:m", out, batch=2) == {"answered": 0, "missing": 0}  # resumed


def test_answers_are_cleaned():
    assert bench.clean(" **Coffee cup.** ") == "coffee cup"
    assert bench.clean("<think>hm</think>A key") == "key"


def test_match_levels():
    assert bench.match("coffee cup", "wn:cup.n.01") == "exact"
    assert bench.match("floppy disk", "wn:diskette.n.01") == "exact"
    assert bench.match("mug", "wn:cup.n.01") == "near"
    assert bench.match("elephant", "wn:cup.n.01") == "miss"
    assert bench.match("", "wn:cup.n.01") == "miss"
    assert bench.match("check mark", "term:checkmarks") == "exact"  # Claude's terms outside WordNet
    assert bench.match("green checkmark", "term:checkmark") == "exact"
    assert bench.match("cross", "term:checkmark") == "miss"


def test_score_summarises_per_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (1, 'wn:cup.n.01', 'ai'), (2, 'wn:key.n.01', 'ai')")
    results = [
        {"key": 1, "model": "a", "answer": "cup", "seconds": 2.0},
        {"key": 2, "model": "a", "answer": "house", "seconds": 4.0},
        {"key": 1, "model": "b", "error": "missing"},
    ]
    report = bench.score(c, results)
    assert report["a"]["n"] == 2 and report["a"]["exact"] == 0.5 and report["a"]["seconds"] == 3.0
    assert report["a"]["misses"] == [("wn:key.n.01", "house")]
    assert report["b"]["n"] == 0
