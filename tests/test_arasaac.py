import io
import json

import httpx
from PIL import Image, ImageDraw

from handdown import db, raster
from handdown.adapters.arasaac import ArasaacAdapter
from handdown.config import Config
from handdown.discover import record_candidate


def _png(colour):
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((40, 40, 260, 260), fill=colour)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_arasaac_colour_original_and_official_black_and_white(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(
        conn, None, id="arasaac:all", platform_id="arasaac", name="ARASAAC", adapter="arasaac", adapter_args={"language": "en"}, harvest_status="accepted"
    )
    asked = []

    def handler(req):
        asked.append(str(req.url))
        if req.url.path == "/v1/pictograms/all/en":
            return httpx.Response(
                200,
                json=[
                    {
                        "_id": 2340,
                        "keywords": [{"keyword": "apple", "meaning": "fruit of the apple tree"}, {"keyword": "manzana"}],
                        "synsets": ["07739125-n"],
                        "categories": ["fruit"],
                        "tags": ["food"],
                        "schematic": False,
                    }
                ],
            )
        if req.url.host == "static.arasaac.org":
            return httpx.Response(200, content=_png((220, 30, 30, 255)))
        if req.url.path == "/v1/pictograms/2340" and req.url.params.get("color") == "false":
            return httpx.Response(200, content=_png((0, 0, 0, 255)))
        return httpx.Response(404)

    ad = ArasaacAdapter(cfg, conn, delay=0)
    ad.client = httpx.Client(transport=httpx.MockTransport(handler))
    items = {i.original_id: i for i in ad.items("arasaac:all")}
    assert set(items) == {"2340", "2340/bw"}
    colour, bw = items["2340"], items["2340/bw"]
    assert colour.format == bw.format == "raster" and colour.name == bw.name == "apple"
    assert raster.decode(colour.svg).getpixel((150, 150))[:3] == (220, 30, 30)
    assert "wordnet31:07739125-n" in colour.tags and "fruit" in colour.categories
    assert colour.license == "CC-BY-NC-SA-4.0" and "Sergio Palao" in colour.author
    assert json.loads(colour.description)["keywords"][0]["meaning"] == "fruit of the apple tree"
    assert "official black and white" in bw.tags
    assert any(u.startswith("https://static.arasaac.org/pictograms/2340/2340_300.png") for u in asked)


def test_arasaac_fetches_in_parallel_and_keeps_the_order(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(
        conn, None, id="arasaac:all", platform_id="arasaac", name="ARASAAC", adapter="arasaac", adapter_args={"language": "en"}, harvest_status="accepted"
    )
    threads = set()

    def handler(req):
        if req.url.path == "/v1/pictograms/all/en":
            return httpx.Response(200, json=[{"_id": n, "keywords": [{"keyword": f"w{n}"}]} for n in range(1, 13)])
        threads.add(threading.get_ident())
        return httpx.Response(200, content=_png((0, 0, 0, 255)))

    ad = ArasaacAdapter(cfg, conn, delay=0, workers=4)
    ad.client = httpx.Client(transport=httpx.MockTransport(handler))
    ids = [i.original_id for i in ad.items("arasaac:all")]
    assert ids == [x for n in range(1, 13) for x in (str(n), f"{n}/bw")]
    assert len(threads) > 1
