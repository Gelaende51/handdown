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
    c = classify(name_evidence("car-house"), shape(part("base"), part("partner"), relations=[(0, 1, "right_of")]), False)
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
