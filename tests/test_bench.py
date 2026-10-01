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
    # seen in the first run: filler words and the object word not last
    assert bench.match("folder icon", "wn:folder.n.02") == "exact"
    assert bench.match("cloud upload", "wn:cloud.n.01") == "exact"
    assert bench.match("arrow left", "wn:arrow.n.01") == "exact"
    assert bench.match("phone call", "wn:telephone.n.01") == "exact"
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


def _named_catalog(c):
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    names = {1: ["coffee-cup", "cup-hot", "mug-outline"], 2: ["star-filled", "favorite-star"]}
    gold = {1: "wn:cup.n.01", 2: "wn:star.n.05"}
    pid = 0
    for did, members in names.items():
        c.execute("INSERT INTO depiction (id, object_id, method) VALUES (?, ?, 'ai')", (did, gold[did]))
        gid = c.execute(
            "INSERT INTO style_group (depiction_id, representative_id, size) VALUES (?, ?, ?) RETURNING id", (did, pid + 1, len(members))
        ).fetchone()[0]
        for name in members:
            pid += 1
            c.execute("INSERT INTO pictogram (id, source_id, original_id, original_name, svg_valid) VALUES (?,?,?,?,1)", (pid, "s", name, name))
            c.execute("INSERT INTO style_member VALUES (?, ?)", (gid, pid))
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (3, 'term:app', 'ai')")  # not WordNet: skipped
    c.commit()


def test_text_items_offer_name_derived_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _named_catalog(c)
    items = {i["key"]: i for i in bench.text_items(c, n=10, max_options=6)}
    assert set(items) == {1, 2}
    cup = items[1]
    assert "coffee cup" in cup["state"] and "mug outline" in cup["state"]
    assert "wn:cup.n.01" in cup["options"] and len(cup["options"]) <= 6
    assert cup["options"]["wn:cup.n.01"].startswith("cup: ")
    assert cup["rules"] == "wn:coffee_cup.n.01"  # the name rules' pick ("coffee cup"), for comparison
    assert next(iter(cup["options"])) == cup["rules"]
    assert "wn:star.n.05" in items[2]["options"] or "wn:star.n.01" in items[2]["options"]


def test_text_run_and_score(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _named_catalog(c)
    items = bench.text_items(c, n=10, max_options=6)
    out = tmp_path / "results" / "text.jsonl"  # a missing folder is created

    def predict(batch):
        return [("wn:cup.n.01", 0.95) for _ in batch]

    assert bench.run_text(items, predict, "fake", out, batch=1) == 2
    assert bench.run_text(items, predict, "fake", out, batch=1) == 0  # resumed
    results = [json.loads(line) for line in out.read_text().splitlines()]
    report = bench.score_text(c, items, results)
    assert report["fake"]["n"] == 2 and report["fake"]["exact"] == 0.5 and report["fake"]["exact_at_0.9"] == 0.5
    assert report["rules"]["n"] == 2
    assert report["fake"]["coverage"] == report["rules"]["coverage"]


def test_backend_options_and_timings_reach_the_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    ask, batch, size = bench.backend("ollama:qwen3-vl:4b-instruct@size=128,threads=4")
    assert (ask.model, ask.options["num_thread"], batch, size) == ("qwen3-vl:4b-instruct", 4, 1, 128)
    assert bench.backend("ollama:moondream")[0].options.get("num_thread") is None

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
    sample = [{"key": 10 + i, "source_id": "s", "original_id": f"i{i}"} for i in (3, 1, 2)]
    out = tmp_path / "res.jsonl"
    stats = bench.run(c, cfg, sample, lambda images: [("square", {"prompt_tokens": 70})] * len(images), "m", out, limit=2)
    assert stats == {"answered": 2, "missing": 0}
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["key"] for r in rows] == [11, 12]  # the first keys, so variants answer the same images
    assert rows[0]["answer"] == "square" and rows[0]["prompt_tokens"] == 70


def test_llamacpp_backend_caps_image_tokens_and_reads_timings():
    import httpx
    from PIL import Image

    ask, batch, size = bench.backend("llamacpp:Qwen/Qwen3-VL-2B-Instruct-GGUF@tokens=64,threads=4")
    assert (batch, size) == (1, 256)
    args = ask.command()
    assert args[args.index("-hf") + 1] == "Qwen/Qwen3-VL-2B-Instruct-GGUF"
    assert args[args.index("--image-min-tokens") + 1] == "64" and args[args.index("--image-max-tokens") + 1] == "64"
    assert args[args.index("-t") + 1] == "4"

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        body = {"choices": [{"message": {"content": "A coffee cup."}}], "usage": {"prompt_tokens": 90}, "timings": {"prompt_ms": 1500.0, "predicted_ms": 200.0}}
        return httpx.Response(200, json=body)

    ask.client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    ask.started = True  # no server in tests
    answers = ask([Image.new("RGB", (8, 8), "white")])
    assert answers == [("coffee cup", {"prompt_eval": 1.5, "eval": 0.2, "prompt_tokens": 90})]
    assert seen[0]["temperature"] == 0 and seen[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vlm_jobs_cover_depictions_without_object_per_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)  # 30 Claude-assessed depictions (with objects) and one rules depiction without pictograms
    c.execute("INSERT INTO pictogram (id, source_id, original_id, svg_valid) VALUES (5000, 's2', 'loose', 1)")
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (900, NULL, 'rules')")
    c.execute("INSERT INTO style_group (depiction_id, representative_id, size) VALUES (900, 5000, 2)")
    c.commit()
    jobs = bench.vlm_jobs(c)
    assert set(jobs) == {"iconify"}
    items, sources = jobs["iconify"]
    assert items == [{"key": 900, "source_id": "s2", "original_id": "loose"}]
    assert [s["id"] for s in sources] == ["s2"]


def test_present_only_keeps_items_of_harvested_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)
    items = [{"key": 1, "source_id": "s1", "original_id": "x"}, {"key": 2, "source_id": "elsewhere", "original_id": "y"}]
    assert bench.present_only(c, items) == items[:1]


def test_vlm_apply_sets_objects_only_where_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (1, NULL, 'rules'), (2, NULL, 'rules'), (3, 'wn:key.n.01', 'ai'), (4, NULL, 'rules')")
    c.commit()
    answers = [
        {"key": 1, "model": "m", "answer": "coffee cup icon"},
        {"key": 2, "model": "m", "answer": "down arrow in circle"},
        {"key": 3, "model": "m", "answer": "house"},  # already has an object: kept
        {"key": 4, "model": "m", "error": "missing"},
    ]
    assert bench.vlm_apply(c, answers) == {"answers": 3, "set": 2}
    rows = {r[0]: tuple(r[1:]) for r in c.execute("SELECT id, object_id, method, description FROM depiction")}
    assert rows[1] == ("wn:coffee_cup.n.01", "vlm", "vlm m: coffee cup icon")
    assert rows[2][:2] == ("wn:arrow.n.01", "vlm")
    assert rows[3][:2] == ("wn:key.n.01", "ai") and rows[4][0] is None
    assert c.execute("SELECT 1 FROM concept WHERE id='wn:arrow.n.01'").fetchone()
