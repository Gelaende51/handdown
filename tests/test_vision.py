import json

import pytest

from handdown import db
from handdown.config import Config


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from handdown.concepts import wordnet

    try:
        wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    return db.connect(Config().db_path)


def test_vocabulary_objects_views_features(conn):
    from handdown import vision

    rows = [
        ("wn:cup.n.01", "cup", "cup.n.01"),
        ("wn:download.v.01", "download", "download.v.01"),
        ("wn:mug.n.04", "mug", "mug.n.04"),
        ("term:mug", "mug", None),
    ]
    conn.executemany("INSERT INTO concept (id, label, wordnet_synset) VALUES (?,?,?)", rows)
    labels = vision.build_vocabulary(conn)
    objects = [lab for lab in labels if lab["kind"] == "object"]
    assert {lab["id"] for lab in objects} >= {"wn:cup.n.01"}
    assert not any(lab["id"] == "wn:download.v.01" for lab in objects)
    assert len({lab["text"] for lab in labels}) == len(labels)  # deduplicated texts
    assert {lab["kind"] for lab in labels} == {"object", "view", "feature"}
    assert all(json.dumps(lab) for lab in labels)


class FakeModels:
    """Deterministic stand-in for DINOv2/SigLIP: features from pixel means."""

    def __init__(self, labels):
        self.kinds = {k: [lab for lab in labels if lab["kind"] == k] for k in ("object", "view", "feature")}

    def image_features(self, images):
        import numpy as np

        m = np.array([[np.asarray(im, dtype=np.float32).mean() / 255.0] for im in images])
        return {"dinov2": np.repeat(m, 384, axis=1), "siglip": np.repeat(m, 768, axis=1)}

    def label_scores(self, siglip, kind):
        import numpy as np

        n, k = len(siglip), len(self.kinds[kind])
        scores = np.full((n, k), 0.01, dtype=np.float32)
        scores[:, 0] = 0.9  # first label of each kind wins
        return scores


def _catalog(root, monkeypatch):
    from test_e2e import Fixture  # tests/ is on sys.path (no package)

    from handdown import pipeline

    monkeypatch.setenv("HANDDOWN_ROOT", str(root))
    cfg = Config()
    c = db.connect(cfg.db_path)
    pipeline.harvest(c, Fixture())
    pipeline.process(c, cfg, workers=1)
    pipeline.dedupe(c)
    return cfg, c


def test_embed_export_import_roundtrip(tmp_path, monkeypatch):
    from handdown import shard, vision

    cfg, c = _catalog(tmp_path / "a", monkeypatch)
    labels = [
        {"id": "wn:cup.n.01", "kind": "object", "text": "a pictogram of a cup"},
        {"id": "view:side", "kind": "view", "text": "a pictogram seen from the side"},
        {"id": "feature:steam", "kind": "feature", "text": "a pictogram with steam"},
    ]
    n = vision.embed(c, cfg, labels, FakeModels(labels), batch=2)
    assert n == c.execute("SELECT COUNT(*) FROM pictogram WHERE duplicate_of IS NULL AND svg_valid=1").fetchone()[0]
    assert c.execute("SELECT COUNT(*) FROM embedding WHERE model='dinov2'").fetchone()[0] == n
    assert c.execute("SELECT COUNT(*) FROM vision_label WHERE kind='object' AND rank=1").fetchone()[0] == n
    key = tmp_path / "k" / "age.key"
    pub = shard.keygen(key)
    out = tmp_path / "vision.jsonl.gz"
    vision.export_vision(c, out, [pub])
    enc = tmp_path / "vision.jsonl.gz.age"
    assert enc.exists() and not out.exists()

    cfg2, c2 = _catalog(tmp_path / "b", monkeypatch)
    stats = vision.import_vision(c2, cfg2, enc, key)
    assert stats["matched"] == n and stats["unknown"] == 0
    assert c2.execute("SELECT COUNT(*) FROM embedding").fetchone()[0] == 2 * n


def test_import_counts_unknown_rows(tmp_path, monkeypatch):
    import gzip

    from handdown import vision

    cfg, c = _catalog(tmp_path, monkeypatch)
    p = tmp_path / "v.jsonl.gz"
    with gzip.open(p, "wt") as f:
        f.write(json.dumps({"source_id": "nope", "original_id": "x", "emb": {}, "labels": []}) + "\n")
    assert vision.import_vision(c, cfg, p, None) == {"matched": 0, "unknown": 1}


def test_blank_render_is_skipped(tmp_path, monkeypatch):
    from handdown import vision

    cfg, c = _catalog(tmp_path, monkeypatch)
    pid = c.execute("SELECT id FROM pictogram WHERE duplicate_of IS NULL LIMIT 1").fetchone()[0]
    path = cfg.resolve(c.execute("SELECT norm_path FROM pictogram WHERE id=?", (pid,)).fetchone()[0])
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>')
    labels = [{"id": "o", "kind": "object", "text": "t"}, {"id": "v", "kind": "view", "text": "v"}, {"id": "f", "kind": "feature", "text": "f"}]
    vision.embed(c, cfg, labels, FakeModels(labels))
    assert c.execute("SELECT COUNT(*) FROM embedding WHERE pictogram_id=?", (pid,)).fetchone()[0] == 0
