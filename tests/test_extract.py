import io

import numpy as np
from PIL import Image, ImageDraw

from handdown import raster
from handdown.composition import extract
from handdown.metrics import render

SEPARATE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M13 2h9v9h-9z"/><path d="M1 7.4 2.4 6 18 21.6 16.6 23z"/></svg>'
SUBPATHS = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M13 2h9v9h-9z M1 7.4 2.4 6 18 21.6 16.6 23z"/></svg>'
CROSSING = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M5 5h14v14H5z M2 3.4 3.4 2 22 20.6 20.6 22z" fill-rule="nonzero"/></svg>'


def _top_right_only(svg):
    ink = render(svg, 24) > 0.5
    return ink[2:11, 13:22].mean() > 0.8 and ink[12:, :12].sum() == 0


def test_subpaths_are_split_into_absolute_paths():
    subs = extract.split_subpaths("M0 0h4v4z m10 0 h4v4z")
    assert len(subs) == 2 and subs[1].startswith("M10")


def test_vector_parts_from_separate_paths_and_from_subpaths():
    for svg in (SEPARATE, SUBPATHS):
        parts = extract.extract(svg, "bell-off", [], False, None)
        assert [(p.part_no, p.role, p.method) for p in parts] == [(0, "base", "vector"), (1, "negation", "vector")]
        assert _top_right_only(parts[0].svg)
        assert render(parts[1].svg, 24)[2:11, 13:22].sum() == 0  # the slash without the base


def test_raster_composites_fall_back_to_masks():
    img = Image.new("L", (96, 96), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((52, 8, 88, 44), fill=0)
    d.polygon([(4, 30), (10, 24), (72, 86), (66, 92)], fill=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    composite = raster.monochrome(raster.to_svg(buf.getvalue())).svg
    parts = extract.extract(composite, "bell-off", [], False, None)
    assert [(p.role, p.method) for p in parts] == [("base", "mask"), ("negation", "mask")]
    base = np.asarray(raster.decode(parts[0].svg).convert("L")) < 128
    assert base.any() and raster.is_raster(parts[0].svg)
    h, w = base.shape
    assert abs(w - h) <= 2  # cropped to the part (a square), not the whole canvas


def test_a_slash_drawn_through_the_base_cannot_be_separated_by_rules():
    parts = extract.extract(CROSSING, "bell-off", [], False, None)
    assert parts and all(p.method is None for p in parts)
    assert {p.reason for p in parts} <= {"shares ink with another part", "no geometry"}


def _composite_catalog(tmp_path, monkeypatch, svg=SEPARATE):
    from handdown import db
    from handdown.config import Config

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    norm = tmp_path / "data" / "norm" / "c.svg"
    norm.parent.mkdir(parents=True)
    norm.write_text(svg)
    c.execute(
        "INSERT INTO pictogram (id, source_id, original_id, original_name, norm_path, svg_valid)"
        " VALUES (1, 's', 'bell-off.svg', 'bell-off', 'data/norm/c.svg', 1)"
    )
    c.execute("INSERT INTO concept (id, label) VALUES ('wn:bell.n.01', 'bell')")
    c.execute("INSERT INTO composition VALUES (1, 'generic', 'mark', 'glyph', 0, 0.9, 'rules', 't')")
    c.execute(
        "INSERT INTO composition_part (pictogram_id, part_no, role, label, concept_id)"
        " VALUES (1, 0, 'base', NULL, 'wn:bell.n.01'), (1, 1, 'negation', 'slash', NULL)"
    )
    c.commit()
    return c, cfg


def test_extracted_parts_become_marked_pictograms(tmp_path, monkeypatch):
    c, cfg = _composite_catalog(tmp_path, monkeypatch)
    assert extract.run(c, cfg, workers=1) == {"composites": 1, "parts": 2, "extracted": 2, "not_separable": 0, "skipped": 0}
    rows = c.execute(
        "SELECT original_id, original_name, derived_from, part_no, extraction, format, measured_at FROM pictogram WHERE source_id=? ORDER BY part_no",
        (extract.DERIVED,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("s/bell-off.svg#part0", "bell", 1, 0, "vector", "svg", None),
        ("s/bell-off.svg#part1", "slash", 1, 1, "vector", "svg", None),
    ]
    assert [r[0] for r in c.execute("SELECT extraction FROM composition_part ORDER BY part_no")] == ["vector", "vector"]
    assert c.execute("SELECT COUNT(*) FROM classification WHERE field='part'").fetchone()[0] == 2
    assert extract.run(c, cfg, workers=1)["composites"] == 0  # done once


def test_parts_link_to_depictions(tmp_path, monkeypatch):
    c, cfg = _composite_catalog(tmp_path, monkeypatch, svg=CROSSING)
    extract.run(c, cfg, workers=1)
    assert {r[0] for r in c.execute("SELECT extraction FROM composition_part")} <= {"not separable: shares ink with another part", "not separable: no geometry"}
    # the bell depiction (largest of the concept) stands in for the part that could not be cut out
    c.execute("INSERT INTO depiction (id, object_id, method, size) VALUES (50, 'wn:bell.n.01', 'rules', 3), (51, 'wn:bell.n.01', 'rules', 9)")
    c.commit()
    assert extract.link_parts(c) == {"from_extraction": 0, "from_concept": 1}
    assert c.execute("SELECT depiction_id FROM composition_part WHERE part_no=0").fetchone()[0] == 51


def test_a_cut_out_part_links_through_its_own_style_group(tmp_path, monkeypatch):
    c, cfg = _composite_catalog(tmp_path, monkeypatch)
    extract.run(c, cfg, workers=1)
    part0 = c.execute("SELECT id FROM pictogram WHERE source_id=? AND part_no=0", (extract.DERIVED,)).fetchone()[0]
    c.execute("INSERT INTO depiction (id, object_id, method, size) VALUES (70, 'wn:bell.n.01', 'rules', 1)")
    gid = c.execute("INSERT INTO style_group (depiction_id, representative_id, size) VALUES (70, ?, 1) RETURNING id", (part0,)).fetchone()[0]
    c.execute("INSERT INTO style_member VALUES (?, ?)", (gid, part0))
    c.commit()
    assert extract.link_parts(c)["from_extraction"] == 1
    assert c.execute("SELECT depiction_id FROM composition_part WHERE part_no=0").fetchone()[0] == 70
