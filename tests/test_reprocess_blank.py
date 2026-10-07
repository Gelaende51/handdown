from handdown import db, pipeline
from handdown.config import Config

INK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>'
BLANK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M2 2h20v20H2z"/></svg>'


def test_blank_pictograms_are_found_and_sent_through_the_pipeline_again(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p', 'p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s', 'p', 's')")
    (tmp_path / "data" / "norm").mkdir(parents=True)
    for name, svg in (("ink", INK), ("blank", BLANK)):
        (tmp_path / "data" / "norm" / f"{name}.svg").write_text(svg)
    c.execute(
        """INSERT INTO pictogram (id, source_id, original_id, norm_path, svg_valid, duplicate_of, measured_at) VALUES
           (1, 's', 'drawn', 'data/norm/ink.svg', 1, NULL, 't'),
           (2, 's', 'light-green', 'data/norm/blank.svg', 1, NULL, 't'),
           (3, 's', 'light-green-copy', 'data/norm/blank.svg', 1, 2, 't'),
           (4, 's', 'embedded', 'data/norm/blank.svg', 1, NULL, 't')"""
    )
    c.execute("INSERT INTO embedding VALUES (4, 'siglip', x'00')")  # has an embedding: not blank when it was made
    c.execute("INSERT INTO depiction (id, object_id, method) VALUES (9, NULL, 'rules')")
    c.execute("INSERT INTO style_group (id, depiction_id, representative_id, size) VALUES (90, 9, 2, 2)")
    c.execute("INSERT INTO style_member (style_group_id, pictogram_id) VALUES (90, 2), (90, 3)")
    c.commit()
    ids = pipeline.blank_pictograms(c, cfg)
    assert ids == [2, 3]
    out = pipeline.reprocess(c, ids)
    assert out["pictograms"] == 2 and out["memberships"] == 2 and out["depictions_removed"] == 1
    assert [r[0] for r in c.execute("SELECT id FROM pictogram WHERE measured_at IS NULL ORDER BY id")] == [2, 3]
