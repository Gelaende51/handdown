import io

import numpy as np
from PIL import Image, ImageDraw

from handdown import raster
from handdown.metrics import render


def _png(img, fmt="PNG", **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _disk(size=64, fill=(200, 30, 30), bg=(255, 255, 255)):
    img = Image.new("RGB", (size, size), bg)
    ImageDraw.Draw(img).ellipse((size // 8, size // 8, size * 7 // 8, size * 7 // 8), fill=fill)
    return img


def test_wrapper_keeps_the_original_and_renders():
    svg = raster.to_svg(_png(_disk()))
    assert raster.is_raster(svg) and 'viewBox="0 0 64 64"' in svg
    assert raster.decode(svg).size == (64, 64)
    ink = render(svg, 48)
    assert 0.3 < ink.mean() < 0.7  # the red disk shows up as ink


def test_monochrome_of_a_colour_image_is_one_bit():
    norm = raster.monochrome(raster.to_svg(_png(_disk())))
    img = raster.decode(norm.svg)
    assert set(np.unique(np.asarray(img.convert("L")))) <= {0, 255}
    assert norm.color_class == "threshold" and norm.color_count == 2  # red and white: colour, so thresholded
    assert norm.uses["raster"] and not norm.uses["stroke"]
    ink = render(norm.svg, 48)
    assert 0.4 < ink.mean() < 0.7


def test_two_tone_icons_are_native():
    img = Image.new("L", (64, 64), 255)
    ImageDraw.Draw(img).rectangle((16, 16, 47, 47), fill=0)
    assert raster.monochrome(raster.to_svg(_png(img))).color_class == "native"


def test_light_glyphs_on_transparency_use_the_alpha_mask():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((16, 16, 47, 47), fill=(255, 255, 255, 255))  # white icon for dark themes
    ink = render(raster.monochrome(raster.to_svg(_png(img))).svg, 64)
    assert 0.2 < ink.mean() < 0.3  # the square, not an empty canvas


def test_sign_frames_are_not_inverted():
    img = Image.new("L", (64, 64), 0)  # black square with a white symbol
    ImageDraw.Draw(img).ellipse((20, 20, 43, 43), fill=255)
    assert render(raster.monochrome(raster.to_svg(_png(img))).svg, 64).mean() > 0.8


def test_pixel_art_stays_crisp_and_large_images_shrink():
    img = Image.new("L", (16, 16), 255)
    ImageDraw.Draw(img).rectangle((4, 4, 11, 11), fill=0)
    svg = raster.monochrome(raster.to_svg(_png(img))).svg
    assert "pixelated" in svg
    assert set(np.unique(np.round(render(svg, 64), 2))) <= {0.0, 1.0}
    big = raster.to_svg(_png(_disk(2048)))
    assert raster.decode(big).size == (512, 512)


def test_first_frame_of_a_gif_and_icons_in_ico():
    frames = [_disk(32, fill=(0, 0, 0)), Image.new("RGB", (32, 32), (255, 255, 255))]
    gif = io.BytesIO()
    frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
    assert render(raster.to_svg(gif.getvalue()), 32).mean() > 0.3
    assert raster.decode(raster.to_svg(_png(_disk(48), "ICO", sizes=[(48, 48)]))).size == (48, 48)


def test_icon_size_filter():
    assert raster.icon_like(16, 16) and raster.icon_like(512, 400)
    assert not raster.icon_like(1200, 300)  # banner
    assert not raster.icon_like(8, 8) and not raster.icon_like(2000, 2000)


def test_process_turns_a_raster_original_into_a_measured_pictogram(tmp_path, monkeypatch):
    from handdown import db, pipeline
    from handdown.config import Config

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    conn.execute("INSERT INTO source (id, platform_id, name) VALUES ('s','p','s')")
    conn.execute("INSERT INTO pictogram (id, source_id, original_id, format) VALUES (1, 's', 'disk.png', 'raster')")
    original = raster.to_svg(_png(_disk()))
    conn.execute("INSERT INTO raw_svg VALUES (1, ?)", (original,))
    conn.commit()
    ok, fail = pipeline.process(conn, cfg, workers=1)
    assert (ok, fail) == (1, 0)
    row = conn.execute("SELECT norm_path, color_class, font_ready, svg_valid, traced FROM pictogram WHERE id=1").fetchone()
    assert row["svg_valid"] == 1 and row["color_class"] == "threshold" and row["font_ready"] == 0 and row["traced"] == 0
    norm = cfg.resolve(row["norm_path"]).read_text()
    assert raster.is_raster(norm) and norm != original  # the 1-bit version; the original stays in raw_svg
    assert conn.execute("SELECT svg FROM raw_svg WHERE pictogram_id=1").fetchone()[0] == original
    assert conn.execute("SELECT 1 FROM feature WHERE pictogram_id=1").fetchone()
