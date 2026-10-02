import io
import json
import zipfile

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from handdown import db
from handdown.adapters import font as font_mod
from handdown.config import Config


def _font_bytes():
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "naxi1"])
    fb.setupCharacterMap({0xE000: "naxi1"})  # private use, like the Naxi scan font
    pen = TTGlyphPen(None)
    for x, y in ((100, 100), (900, 100), (900, 900), (100, 900)):
        (pen.moveTo if (x, y) == (100, 100) else pen.lineTo)((x, y))
    pen.closePath()
    square = pen.glyph()
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "naxi1": square})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "naxi1": (1000, 100)})
    fb.setupHorizontalHeader(ascent=900, descent=-100)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


class _Response:
    def __init__(self, body, text=""):
        self.body, self.text = body, text

    def raise_for_status(self):
        pass

    def iter_bytes(self, n):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_font_from_a_download_page_and_a_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    conn.execute("INSERT INTO platform (id, name) VALUES ('p','p')")
    args = {"page": "https://fonts.example/Naxi.html"}
    conn.execute("INSERT INTO source (id, platform_id, name, adapter, adapter_args) VALUES ('naxi','p','Naxi','font',?)", (json.dumps(args),))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", "OFL")
        z.writestr("BabelStoneNaxi.ttf", _font_bytes())
    page = '<a href="Download/BabelStoneNaxi.zip">Download</a> <a href="other.html">x</a>'
    requested = []
    monkeypatch.setattr(font_mod.httpx, "get", lambda url, **kw: (requested.append(url), _Response(b"", page))[1])
    monkeypatch.setattr(font_mod.httpx, "stream", lambda method, url, **kw: (requested.append(url), _Response(archive.getvalue()))[1])

    items = list(font_mod.FontAdapter(cfg, conn).items("naxi"))
    assert requested == ["https://fonts.example/Naxi.html", "https://fonts.example/Download/BabelStoneNaxi.zip"]
    assert [i.original_id for i in items] == ["U+E000"] and "<path" in items[0].svg
