import pytest

from handdown.normalize import normalize, parse_paint

NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(body: str, vb: str = "0 0 24 24", extra: str = "") -> str:
    return f'<svg {NS} viewBox="{vb}" {extra}>{body}</svg>'


def test_strips_scripts_handlers_and_external_refs():
    src = svg(
        "<script>alert(1)</script>"
        '<path d="M0 0h10v10z" onclick="x()"/>'
        '<use href="http://evil/x.svg#a"/>'
        '<image href="data:image/png;base64,AAAA"/>'
        "<foreignObject><div/></foreignObject>"
        '<a href="javascript:alert(1)"><path d="M1 1h2v2z"/></a>'
    )
    r = normalize(src)
    out = r.svg
    for bad in ("script", "onclick", "evil", "foreignObject", "javascript", "image", "<a"):
        assert bad not in out
    assert "M1 1h2v2z" in out  # content of <a> is kept, only the link goes
    assert set(r.removed) >= {"script", "foreignObject", "image", "attr:onclick", "href"}


def test_rejects_entities():
    src = '<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x "boom">]>' + svg("<text>&x;</text>")
    with pytest.raises(ValueError):
        normalize(src)


def test_native_monochrome_currentcolor():
    r = normalize(svg('<path fill="currentColor" d="M0 0h10v10z"/>'))
    assert r.color_class == "native"
    assert r.color_count == 1


def test_default_black_fill_is_native():
    r = normalize(svg('<path d="M0 0h10v10z"/>'))
    assert r.color_class == "native"


def test_black_with_white_detail_is_derivable():
    r = normalize(svg('<path fill="#000" d="M0 0h10v10z"/><path fill="#fff" d="M2 2h2v2z"/>'))
    assert r.color_class == "derivable"
    assert "#fff" in r.svg or "white" in r.svg


def test_many_colors_is_none():
    body = "".join(f'<path fill="{c}" d="M{i} 0h1v1z"/>' for i, c in enumerate(["#f00", "#0f0", "#00f", "#ff0", "#0ff", "#f0f", "#888", "#123"]))
    assert normalize(svg(body)).color_class == "none"


def test_gradient_is_threshold():
    body = (
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#000"/>'
        '<stop offset="1" stop-color="#333"/></linearGradient></defs>'
        '<path fill="url(#g)" d="M0 0h10v10z"/>'
    )
    assert normalize(svg(body)).color_class == "threshold"


def test_monochrome_output_is_pure_black_and_white():
    r = normalize(svg('<path fill="#e33" d="M0 0h10v10z"/><path fill="#fde" d="M2 2h2v2z"/>'))
    out = r.svg.lower()
    assert "#e33" not in out and "#fde" not in out


def test_css_classes_are_inlined():
    body = '<style>.a{fill:#ffffff}.b,.c{fill:#000}</style><path class="b" d="M0 0h9v9z"/><path class="a" d="M1 1h1v1z"/>'
    r = normalize(svg(body))
    assert "<style" not in r.svg
    assert r.color_class == "derivable"


def test_stroke_icon_metadata():
    r = normalize(svg('<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4 4L20 20"/>'))
    assert r.stroke_width == 2
    assert r.uses["stroke"] is True
    assert r.stroke_caps == "round"


def test_viewbox_made_square_and_size_attrs_dropped():
    r = normalize(svg('<path d="M0 0h32v16z"/>', vb="0 0 32 16", extra='width="32" height="16"'))
    assert r.viewbox == (0.0, -8.0, 32.0, 32.0)
    assert 'width="32"' not in r.svg


def test_viewbox_from_width_height():
    src = f'<svg {NS} width="16" height="16"><path d="M0 0h16v16z"/></svg>'
    assert normalize(src).viewbox == (0.0, 0.0, 16.0, 16.0)


def test_text_detected():
    assert normalize(svg("<text>A</text>")).has_text


def test_parse_paint():
    assert parse_paint("none") is None
    assert parse_paint("currentColor") == (0, 0, 0)
    assert parse_paint("#fff") == (255, 255, 255)
    assert parse_paint("rgb(255, 0, 0)") == (255, 0, 0)
    assert parse_paint("url(#g)") == "url(#g)"


def test_duotone_is_derivable():
    r = normalize(svg('<path fill="currentColor" opacity=".3" d="M0 0h20v20z"/><path fill="currentColor" d="M4 4h4v4z"/>'))
    assert r.color_class == "derivable"
    assert r.extra["duotone"]
