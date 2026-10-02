import io
import zipfile

import httpx
from PIL import Image, ImageDraw

from handdown import db
from handdown.adapters.web import WebAdapter
from handdown.config import Config
from handdown.discover import record_candidate

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h9v9z"/></svg>'


def _png(w=48, h=48):
    img = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(img).rectangle((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_web_adapter_crawls_one_site_section_for_images_and_zips(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(
        conn,
        None,
        id="web:x",
        platform_id="x",
        name="X",
        adapter="web",
        url="https://site.example/picto/",
        adapter_args={"depth": 1},
        harvest_status="accepted",
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("set/eat.png", _png())
        z.writestr("set/readme.txt", "x")
    pages = {
        "/picto/": '<a href="page2.html">2</a> <a href="/about/">about</a> <a href="https://other.example/a.png">x</a>'
        '<img src="/img/logo.png"> <a href="files/drink.svg">d</a> <a href="all.zip">zip</a> <img src="banner.png">',
        "/picto/page2.html": '<img src="icons/sleep.png"> <a href="deeper.html">3</a>',
    }
    assets = {
        "/picto/files/drink.svg": SVG.encode(),
        "/picto/all.zip": archive.getvalue(),
        "/picto/icons/sleep.png": _png(),
        "/img/logo.png": _png(),
        "/picto/banner.png": _png(600, 100),
    }
    fetched = []

    def handler(req):
        fetched.append(req.url.path if req.url.host == "site.example" else str(req.url))
        if req.url.host != "site.example":
            return httpx.Response(404)
        if req.url.path in pages:
            return httpx.Response(200, text=pages[req.url.path], headers={"content-type": "text/html"})
        if req.url.path in assets:
            return httpx.Response(200, content=assets[req.url.path])
        return httpx.Response(404)

    ad = WebAdapter(cfg, conn, delay=0)
    ad.client = httpx.Client(transport=httpx.MockTransport(handler))
    items = {i.original_id: i for i in ad.items("web:x")}
    assert set(items) == {
        "https://site.example/picto/files/drink.svg",
        "https://site.example/picto/all.zip#set/eat.png",
        "https://site.example/picto/icons/sleep.png",
    }
    assert items["https://site.example/picto/files/drink.svg"].format == "svg"
    assert items["https://site.example/picto/icons/sleep.png"].format == "raster" and items["https://site.example/picto/icons/sleep.png"].name == "sleep"
    assert "/picto/deeper.html" not in fetched  # depth 1
    assert "/about/" not in fetched and "https://other.example/a.png" not in fetched  # same section of the same site
    assert "/img/logo.png" not in fetched  # logos are not pictograms


def test_start_urls_may_name_files_directly(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    urls = [f"https://data.example/images/ghs/GHS0{n}.svg" for n in (1, 2)]
    record_candidate(
        conn,
        None,
        id="web:ghs",
        platform_id="x",
        name="GHS",
        adapter="web",
        url="https://data.example/ghs/",
        adapter_args={"start": urls},
        harvest_status="accepted",
    )
    ad = WebAdapter(cfg, conn, delay=0)
    ad.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=SVG.encode())))
    assert sorted(i.name for i in ad.items("web:ghs")) == ["GHS01", "GHS02"]
