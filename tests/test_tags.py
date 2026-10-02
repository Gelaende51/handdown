from handdown import tags


def test_free_text_features_map_onto_the_vocabulary():
    assert tags.canon_feature("small badge icon") == "feature:badge"
    assert tags.canon_feature("Small badge duplicate") == "feature:badge"
    assert tags.canon_feature("circle outline") == "frame:circle"
    assert tags.canon_feature("rounded square frame") == "frame:square"
    assert tags.canon_feature("diagonal slash") == "feature:slash"
    assert tags.canon_feature("x mark") == "feature:cross"
    assert tags.canon_feature("steam") == "feature:steam"
    assert tags.canon_feature("wheels") == "feature:wheel"
    assert tags.canon_feature("points-left") == "direction:left"
    assert tags.canon_feature("rounded corners") == "corners:rounded"
    assert tags.canon_feature("") is None


def test_corner_style_from_paths():
    square = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/></svg>'
    rounded = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="4"/></svg>'
    circle = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>'
    fillet = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/></svg>'
    )
    assert tags.corner_style(square) == "sharp"
    assert tags.corner_style(rounded) == "rounded"
    assert tags.corner_style(circle) == "rounded"
    assert tags.corner_style(fillet) == "rounded"
    star = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 2l3 7h7l-6 4 2 8-6-5-6 5 2-8-6-4h7z"/></svg>'
    assert tags.corner_style(star) == "sharp"
    percent = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="20" height="20" rx="15%"/></svg>'
    assert tags.corner_style(percent) == "rounded"  # lengths with units


def test_pictogram_tags_from_measurements_and_depiction():
    row = {
        "style": "outline",
        "container_shape": "circle",
        "symmetry": '{"h": false, "v": true, "r": false}',
        "mirror_safe": 1,
        "stroke_caps": "round",
        "format": "svg",
        "color_class": "native",
        "topic": None,
    }
    got = tags.pictogram_tags(row, corners="rounded", view="side", varieties=["points-left", "small badge icon", "zzz rare"])
    assert {
        "style:outline",
        "frame:circle",
        "symmetry:vertical",
        "mirror-safe",
        "ends:round",
        "format:svg",
        "corners:rounded",
        "view:side",
        "direction:left",
        "feature:badge",
    } <= got
    assert "feature:zzz rare" in got  # rare free text is dropped when tags are built (min_count), not here


def _catalog(tmp_path, monkeypatch):
    from handdown import db
    from handdown.config import Config

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    c.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s'), ('t','p','t')")
    shapes = {
        "sq": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/></svg>',
        "ci": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>',
    }
    (tmp_path / "data" / "norm").mkdir(parents=True)
    for name, svg in shapes.items():
        (tmp_path / "data" / "norm" / f"{name}.svg").write_text(svg)
    rows = [(1, "s", "sq", "outline"), (2, "s", "ci", "filled"), (3, "t", "ci", "outline"), (4, "s", "sq", "outline")]
    for pid, src, shape, style in rows:
        c.execute(
            "INSERT INTO pictogram (id, source_id, original_id, norm_path, sha256, svg_valid, measured_at, style, format) VALUES (?,?,?,?,?,1,'t',?,'svg')",
            (pid, src, f"i{pid}", f"data/norm/{shape}.svg", f"{pid:064x}", style),
        )
    c.execute("UPDATE pictogram SET topic='off-topic: test' WHERE id=4")  # left out of the tags
    import json

    c.execute("INSERT INTO depiction (id, view, varieties, method) VALUES (10, 'side', ?, 'ai')", (json.dumps(["points-left", "small badge icon"]),))
    c.execute("INSERT INTO depiction (id, view, varieties, method) VALUES (11, 'unknown', ?, 'ai')", (json.dumps(["rare thing"]),))
    gid = c.execute("INSERT INTO style_group (depiction_id, size) VALUES (10, 2) RETURNING id").fetchone()[0]
    c.executemany("INSERT INTO style_member VALUES (?, ?)", [(gid, 1), (gid, 2)])
    rare = c.execute("INSERT INTO style_group (depiction_id, size) VALUES (11, 1) RETURNING id").fetchone()[0]
    c.execute("INSERT INTO style_member VALUES (?, 3)", (rare,))
    c.execute("INSERT INTO composition (pictogram_id, kind, method) VALUES (3, 'generic', 'rules')")
    c.execute("INSERT INTO composition_part (pictogram_id, part_no, role, label) VALUES (3, 0, 'base', NULL), (3, 1, 'negation', 'slash')")
    c.commit()
    return c, cfg


def test_build_tags(tmp_path, monkeypatch):
    c, cfg = _catalog(tmp_path, monkeypatch)
    counts = tags.build(c, cfg, workers=1, min_count=2)
    got = {}
    for pid, tag in c.execute("SELECT pictogram_id, tag FROM pictogram_tag"):
        got.setdefault(pid, set()).add(tag)
    assert {"style:outline", "corners:sharp", "view:side", "direction:left", "feature:badge"} <= got[1]
    assert "corners:rounded" in got[2] and "feature:badge" in got[2]
    assert "feature:rare thing" not in got[3]  # fewer than min_count pictograms
    assert "feature:slash" in got[3]  # from the composite's negation part
    assert 4 not in got  # off-topic
    assert counts["pictograms"] == 3
    assert c.execute("SELECT n FROM tag_count WHERE tag='style:outline'").fetchone()[0] == 2


def test_browse_filters_by_tags_with_counts(tmp_path, monkeypatch):
    import threading
    import urllib.request

    from handdown import review

    c, cfg = _catalog(tmp_path, monkeypatch)
    tags.build(c, cfg, workers=1, min_count=2)
    server = review.make_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(url):
        with urllib.request.urlopen(base + url) as r:
            return r.read().decode()

    try:
        page = get("/browse?tag=style%3Aoutline")
        assert "/image/1" in page and "/image/3" in page and "/image/2" not in page
        assert "tag=corners%3Arounded" in page and "tag=corners%3Asharp" in page  # facets within the selection, with counts
        page = get("/browse?tag=style%3Aoutline&tag=corners%3Arounded")
        assert "/image/3" in page and "/image/1" not in page
        assert "browse?tag=style%3Aoutline" in page  # removing a chosen tag
        page = get("/browse?source=s&tag=style%3Aoutline")
        assert "/image/1" in page and "/image/3" not in page
        assert "tag=style%3Aoutline" in get("/browse")  # the vocabulary with catalog counts
    finally:
        server.shutdown()
