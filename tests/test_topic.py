from handdown import bench, db, topic
from handdown.config import Config


def _catalog(c):
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name, adapter, harvest_status) VALUES ('gh:x/sprites','p','s','git-svg','harvested')")
    ids = {"icons/pokemon/pikachu.png": 1, "pokemon-gen8/regular/eevee.png": 2, "items/key-item/bike.png": 3, "misc/ribbon/gold.png": 4}
    for oid, pid in ids.items():
        c.execute("INSERT INTO pictogram (id, source_id, original_id, format, svg_valid) VALUES (?, 'gh:x/sprites', ?, 'raster', 1)", (pid, oid))
    c.commit()


def test_mark_by_source_and_pattern_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)
    n = topic.mark(c, "gh:x/sprites", ["icons/pokemon/*", "pokemon-gen*/*"], "game character sprites")
    assert n == 2
    off = {r[0]: r[1] for r in c.execute("SELECT original_id, topic FROM pictogram WHERE topic IS NOT NULL")}
    assert off == {"icons/pokemon/pikachu.png": "off-topic: game character sprites", "pokemon-gen8/regular/eevee.png": "off-topic: game character sprites"}
    assert topic.mark(c, "gh:x/sprites", ["*"], None) == 4  # undo: everything on topic again
    assert c.execute("SELECT COUNT(*) FROM pictogram WHERE topic IS NOT NULL").fetchone()[0] == 0


def test_off_topic_pictograms_are_not_labelled(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    _catalog(c)
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (10, NULL, 'rules'), (11, NULL, 'rules')")
    # depiction 10: the sprite is the larger group, the on-topic icon the smaller; depiction 11: only a sprite
    c.execute("INSERT INTO style_group (depiction_id, representative_id, size) VALUES (10, 1, 5), (10, 3, 1), (11, 2, 3)")
    c.commit()
    topic.mark(c, "gh:x/sprites", ["icons/pokemon/*", "pokemon-gen*/*"], "game character sprites")
    items = bench.vlm_jobs(c)["git-svg"][0]
    assert [(i["key"], i["original_id"]) for i in items] == [(10, "items/key-item/bike.png")]
