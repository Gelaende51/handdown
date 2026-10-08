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


FADED = (  # a desktop icon theme: light grey at 30 % for dark panels
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="#dfdfdf" fill-opacity=".3" d="M2 2h12v12H2z"/></svg>'
)
TILE = (  # an app icon: white symbol on an orange tile
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="4" fill="#ff9800"/><path fill="#fff" d="M8 6h8v12H8z"/></svg>'
)
EMPTY = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"><rect x="2" y="2" width="20" height="20"/></svg>'


def test_empty_black_and_white_falls_back_to_contrast():
    from handdown.metrics import render

    r = pipeline.normalize_any(FADED)
    assert r.extra.get("fallback") == "contrast" and render(r.svg, 64).max() > 0.5
    # a light tile needs no fallback (the darkest colour is the ink); its symbol stays apart: tile ink, symbol paper
    r = pipeline.normalize_any(TILE)
    tile = render(r.svg, 64)
    assert "fallback" not in r.extra and tile[32, 32] < 0.5 < tile[4, 32]
    # nothing drawn in the original either: stays empty, no fallback
    assert "fallback" not in pipeline.normalize_any(EMPTY).extra
    # a normal icon never takes the fallback, and its colour copy is not kept
    assert pipeline.normalize_any(INK).extra == {"duotone": False}
