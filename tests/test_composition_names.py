from handdown.composition.names import name_evidence


def roles(ev):
    return sorted((p.role, p.label) for p in ev.parts)


def test_negation_and_base():
    ev = name_evidence("bell-off")
    assert ev.base == ["bell"]
    assert roles(ev) == [("negation", "slash")]


def test_frame_is_meaning_with_shape():
    ev = name_evidence("home-circle-outline")
    assert ev.base == ["home"]
    assert ev.frame == "circle"
    assert roles(ev) == [("frame", "circle")]


def test_modifier_and_repetition():
    assert roles(name_evidence("user-plus")) == [("modifier", "plus")]
    ev = name_evidence("file-multiple")
    assert roles(ev) == [("repetition", "plural")]


def test_bare_frame_is_an_element_not_a_composite():
    ev = name_evidence("circle")
    assert ev.base == ["circle"] and ev.parts == []


def test_partners_two_elements():
    ev = name_evidence("car-house")
    assert ev.base == ["car", "house"]


def test_code_tokens_are_text_evidence():
    assert name_evidence("4g-plus-mobiledata").text
    assert name_evidence("counter-5").text
    assert not name_evidence("bell-off").text
    assert name_evidence("double-arrow").parts[0].role == "repetition"
