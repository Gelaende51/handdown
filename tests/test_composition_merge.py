from handdown.composition.merge import classify
from handdown.composition.names import name_evidence
from handdown.composition.shape import ShapeEvidence, ShapePart


def shape(*parts, slash=False, frame=None, repetition=1, relations=()):
    return ShapeEvidence(parts=list(parts), slash=slash, frame=frame, repetition=repetition, relations=list(relations))


def part(role, extent=20, size_class="equal", px16=8.0):
    size = {"size_class": size_class, "px16": px16, "extent_ratio": 1.0, "area_ratio": 1.0, "glyph_share": 1.0}
    return ShapePart(bbox=(0, extent, 0, extent), area=extent * extent, role_hint=role, size=size)


def test_single_part_is_not_a_composite():
    assert classify(name_evidence("bell"), shape(part("base")), False) is None


def test_agreeing_negation_is_generic_mark():
    c = classify(name_evidence("bell-off"), shape(part("base"), part("negation"), slash=True, relations=[(0, 1, "crossing")]), False)
    assert c.kind == "generic" and c.font_type == "mark" and not c.conflict and c.fit == "glyph"


def test_name_without_shape_is_conflict():
    c = classify(name_evidence("bell-off"), shape(part("base")), False)
    assert c is not None and c.conflict


def test_equal_partners_are_sequence_and_degrade():
    c = classify(name_evidence("car-with-house"), shape(part("base"), part("partner"), relations=[(0, 1, "right_of")]), False)
    assert c.font_type == "sequence" and c.fit in ("degrades", "sequence")


def test_negated_bare_frame_is_contradictory():
    c = classify(name_evidence("circle-off"), shape(part("frame"), part("negation"), slash=True), False)
    assert c.fit == "contradictory"


def test_tiny_modifier_degrades():
    mod = part("modifier", 6, "tiny", 1.5)
    c = classify(name_evidence("user-plus"), shape(part("base"), mod, relations=[(0, 1, "corner:br:cutout")]), False)
    assert c.font_type == "ligature" and c.fit == "degrades"


def test_merged_parts_are_unique():
    c = classify(name_evidence("user-plus"), shape(part("base")), False)
    assert c.kind == "unique"  # name says composite, shape is one fused part


def test_multi_component_element_is_not_a_composite():
    # "user" = head + shoulders: two components, one element, no operator in the name
    assert classify(name_evidence("user"), shape(part("base"), part("partner")), False) is None
    assert classify(name_evidence("information"), shape(part("base"), part("modifier", 6, "tiny", 2.0)), False) is None


def test_named_modifier_confirms_shape_modifier():
    mod = part("modifier", 8, "minor", 6.0)
    c = classify(name_evidence("user-plus"), shape(part("base"), mod, relations=[(0, 1, "corner:br:cutout")]), False)
    assert c is not None and not c.conflict and c.fit == "glyph"


def test_relations_point_at_composition_parts():
    # two base components (index 0 and 1) and a named modifier (index 2)
    parts = (part("base"), part("base"), part("modifier", 8, "minor", 6.0))
    c = classify(name_evidence("user-plus"), shape(*parts, relations=[(0, 2, "corner:br:cutout")]), False)
    roles = [p["role"] for p in c.parts]
    assert roles.count("base") == 1
    a, b, _ = c.relations[0]
    assert c.parts[a]["role"] == "base" and c.parts[b]["role"] == "modifier"


def test_multiword_name_without_shape_partner_is_one_element():
    assert classify(name_evidence("video-call"), shape(part("base")), False) is None
    c = classify(name_evidence("car-to-house"), shape(part("base"), part("partner")), False)
    assert c is not None and c.font_type == "sequence"


def test_unnamed_frame_counts_only_in_sign_sources():
    framed = shape(part("frame"), part("base"))
    assert classify(name_evidence("donut"), framed, False) is None  # interior detail, not a frame
    c = classify(name_evidence("ISO_7010_P001"), framed, False, sign_domain=True)
    assert c is not None and any(p["role"] == "frame" for p in c.parts)
    assert classify(name_evidence("home-circle"), shape(part("frame"), part("base")), False) is not None


def test_partners_need_a_connector_word():
    two = shape(part("base"), part("partner"))
    assert classify(name_evidence("music-notes"), two, False) is None
    c = classify(name_evidence("car-and-house"), two, False)
    assert c is not None and c.font_type == "sequence"


def test_classify_does_not_mutate_its_input():
    ev = shape(part("frame"), part("base"))
    classify(name_evidence("donut"), ev, False)
    assert ev.parts[0].role_hint == "frame"


def test_named_modifier_does_not_keep_shape_partners():
    c = classify(name_evidence("person-check"), shape(part("base"), part("partner"), part("modifier", 8, "minor", 6.0)), False)
    assert "partner" not in [p["role"] for p in c.parts]


def test_shape_repetition_needs_name_or_sign_source():
    stripes = shape(part("base"), part("repetition"), repetition=2)
    assert classify(name_evidence("delete"), stripes, False) is None
    c = classify(name_evidence("file-multiple"), stripes, False)
    assert c is not None and "repetition" in [p["role"] for p in c.parts]


def test_code_tokens_in_name_are_text():
    c = classify(name_evidence("1k-plus-outline"), shape(part("base")), False)
    assert c is not None and "text" in [p["role"] for p in c.parts]
