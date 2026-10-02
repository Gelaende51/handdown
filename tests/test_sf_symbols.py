from handdown.metrics import render
from handdown.sf_symbols import extract, is_template

TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -550 3300 3300">
 <g id="Notes"><rect height="2200" id="artboard" width="3300" x="0" y="0" fill="#fff"/>
  <text transform="matrix(1 0 0 1 263 322)" font-size="13">Weight/Scale Variations</text></g>
 <g id="Guides"><line x1="263" x2="3036" y1="292" y2="292" stroke="#000"/></g>
 <g id="Symbols">
  <g id="Black-S" transform="matrix(1 0 0 1 2800 696)"><path d="M0 0h80v80H0z"/></g>
  <g id="Regular-S" transform="matrix(1 0 0 1 1400 696)"><path d="M0 0h80v80H0z M100 0h20v80h-20z"/></g>
  <g id="Ultralight-S" transform="matrix(1 0 0 1 200 696)"><path d="M0 0h80v80H0z"/></g>
 </g>
</svg>"""


def test_sf_symbols_template_becomes_its_regular_variant():
    assert is_template(TEMPLATE) and not is_template('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>')
    svg = extract(TEMPLATE)
    assert "Weight/Scale" not in svg and "Black-S" not in svg and "Regular-S" in svg
    ink = render(svg, 64)
    assert 0.3 < ink.mean() < 0.95  # the glyph fills the view instead of a speck on a 3300-unit artboard
    assert render(TEMPLATE, 64).mean() < 0.01


def test_normalize_uses_the_variant():
    from handdown.normalize import normalize

    r = normalize(TEMPLATE)
    assert "Weight/Scale" not in r.svg and not r.has_text
    assert render(r.svg, 64).mean() > 0.3


def test_reprocess_source_clears_measurements_and_placement(tmp_path, monkeypatch):
    from handdown import db, pipeline
    from handdown.config import Config

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    c = db.connect(Config().db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('bad','p','bad'), ('ok','p','ok')")
    for pid, src in ((1, "bad"), (2, "bad"), (3, "ok")):
        c.execute("INSERT INTO pictogram (id, source_id, original_id, measured_at, normalized_at) VALUES (?, ?, ?, 't', 't')", (pid, src, str(pid)))
    c.execute("INSERT INTO depiction (id, method) VALUES (10, 'rules'), (11, 'rules')")
    c.execute("INSERT INTO style_group (id, depiction_id, representative_id, size) VALUES (100, 10, 1, 2), (101, 11, 1, 2)")
    c.executemany("INSERT INTO style_member VALUES (?, ?)", [(100, 1), (100, 2), (101, 1), (101, 3)])
    c.execute("INSERT INTO composition (pictogram_id, kind, method) VALUES (1, 'generic', 'rules'), (3, 'generic', 'rules')")
    c.commit()
    out = pipeline.reprocess_source(c, "bad")
    assert out == {"pictograms": 2, "memberships": 3, "groups_removed": 1, "depictions_removed": 1, "compositions_removed": 1}
    assert c.execute("SELECT COUNT(*) FROM pictogram WHERE source_id='bad' AND measured_at IS NULL").fetchone()[0] == 2
    assert [tuple(r) for r in c.execute("SELECT id FROM depiction")] == [(11,)]
    assert tuple(c.execute("SELECT representative_id, size FROM style_group WHERE id=101").fetchone()) == (3, 1)  # a new representative
    assert [tuple(r) for r in c.execute("SELECT pictogram_id FROM composition")] == [(3,)]


def test_a_fresh_database_opens(tmp_path):
    from handdown import db

    c = db.connect(tmp_path / "fresh.sqlite")
    assert c.execute("SELECT name FROM sqlite_master WHERE name='pictogram_derived'").fetchone()
