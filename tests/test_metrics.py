from handdown.metrics import hamming, measure

NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(body: str) -> str:
    return f'<svg {NS} viewBox="0 0 24 24">{body}</svg>'


SQUARE = svg('<rect x="4" y="4" width="16" height="16"/>')
RING = svg('<circle cx="12" cy="12" r="8" fill="none" stroke="#000" stroke-width="2"/>')
HAIRLINES = svg("".join(f'<rect x="{2 + i * 2}" y="3" width="0.3" height="18"/>' for i in range(10)))
OFFCENTER = svg('<rect x="0" y="0" width="8" height="8"/>')
TWO_DOTS = svg('<circle cx="6" cy="12" r="3"/><circle cx="18" cy="12" r="3"/>')
CIRCLED = svg('<circle cx="12" cy="12" r="10" fill="none" stroke="#000" stroke-width="2"/><rect x="9" y="9" width="6" height="6"/>')


def test_square_is_simple_legible_balanced_filled():
    m = measure(SQUARE)
    assert m["components"] == 1
    assert m["holes"] == 0
    assert m["style"] == "filled"
    assert m["legibility"] > 75
    assert m["balance"] > 90
    assert m["symmetry"]["h"] and m["symmetry"]["v"] and m["symmetry"]["r"]
    assert 0.6 < m["padding_ratio"] < 0.75


def test_ring_is_outline_with_hole():
    m = measure(RING)
    assert m["holes"] == 1
    assert m["style"] == "outline"


def test_hairlines_lose_legibility():
    assert measure(HAIRLINES)["legibility"] < measure(SQUARE)["legibility"] - 20
    assert measure(HAIRLINES)["min_feature_px16"] < 1


def test_offcenter_is_unbalanced():
    assert measure(OFFCENTER)["balance"] < 30


def test_components_counted():
    assert measure(TWO_DOTS)["components"] == 2


def test_container_detected():
    assert measure(CIRCLED)["container_shape"] == "circle"
    assert measure(SQUARE)["container_shape"] == "none"


def test_phash_similar_for_similar_shapes():
    a = measure(SQUARE)["phash"]
    b = measure(svg('<rect x="4.5" y="4" width="15.5" height="16"/>'))["phash"]
    c = measure(TWO_DOTS)["phash"]
    assert hamming(a, b) < hamming(a, c)


def test_feature_vector_shape():
    assert len(measure(SQUARE)["feature"]) == 256
