from handdown.composition.shape import shape_evidence, size_class

NS = 'xmlns="http://www.w3.org/2000/svg"'
BELL = '<path d="M6 17h12l-2-3v-4a4 4 0 0 0-8 0v4z"/>'


def svg(body):
    return f'<svg {NS} viewBox="0 0 24 24">{body}</svg>'


def test_separate_slash_detected_without_name():
    # a slash with a gap to the base is its own component
    body = '<rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'
    ev = shape_evidence(svg(body + '<path d="M3 3L21 21" stroke="#000" stroke-width="2"/>'))
    assert ev.slash


def test_frame_detected_with_content():
    ev = shape_evidence(svg('<circle cx="12" cy="12" r="10" fill="none" stroke="#000" stroke-width="2"/><rect x="9" y="9" width="6" height="6"/>'))
    assert ev.frame == "circle"
    assert any(p.role_hint == "frame" for p in ev.parts)


def test_repetition_counts_equal_copies():
    ev = shape_evidence(svg('<rect x="2" y="9" width="5" height="6"/><rect x="9.5" y="9" width="5" height="6"/><rect x="17" y="9" width="5" height="6"/>'))
    assert ev.repetition == 3


def test_corner_badge_is_minor_modifier_with_relation():
    ev = shape_evidence(svg('<rect x="2" y="2" width="14" height="14"/><circle cx="19" cy="19" r="3"/>'))
    mods = [p for p in ev.parts if p.role_hint == "modifier"]
    assert len(mods) == 1
    assert any(rel.startswith("corner:br") for _, _, rel in ev.relations)
    assert mods[0].size["size_class"] in ("minor", "tiny")


def test_blank_render_has_no_parts():
    assert shape_evidence(svg("")).parts == []


def test_size_classes():
    assert [size_class(x) for x in (1.5, 1.0, 0.5, 0.2)] == ["dominant", "equal", "minor", "tiny"]


def test_filled_square_is_not_a_slash():
    ev = shape_evidence(svg('<rect x="3" y="3" width="18" height="18"/>'))
    assert not ev.slash


def test_merged_slash_needs_name_support():
    merged = svg(BELL + '<path d="M3 3L21 21" stroke="#000" stroke-width="2"/>')
    assert not shape_evidence(merged).slash  # alone, a merged diagonal is not enough
    assert shape_evidence(merged, expect_negation=True).slash


def test_outline_with_touching_inner_parts_is_not_a_frame():
    window = '<rect x="3" y="3" width="18" height="18" fill="none" stroke="#000" stroke-width="2"/><path d="M12 3v18M3 12h18" stroke="#000" stroke-width="2"/>'
    assert shape_evidence(svg(window)).frame is None
    # content floating inside with a gap is a frame
    framed = '<rect x="2" y="2" width="20" height="20" fill="none" stroke="#000" stroke-width="2"/><rect x="8" y="8" width="8" height="8"/>'
    assert shape_evidence(svg(framed)).frame == "square"
