import io
import json
import tarfile

from handdown import db
from handdown.adapters.tarball import GitSvgAdapter, guess_license
from handdown.config import Config
from handdown.discover import norm_url, record_candidate
from handdown.triage import decide

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h9v9z"/></svg>'


def test_norm_url():
    assert norm_url("https://github.com/Foo/Bar.git") == "github.com/foo/bar"
    assert norm_url("git+https://www.github.com/foo/bar/tree/main/icons") == "github.com/foo/bar"


def test_guess_license():
    assert guess_license("Permission is hereby granted, free of charge, to any person") == "MIT"
    assert guess_license("SIL OPEN FONT LICENSE Version 1.1") == "OFL-1.1"


def _tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_tarball_extracts_svgs_only_and_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(conn, None, id="gh:a/b", platform_id="github", name="b", adapter="git-svg", adapter_args={"repo": "a/b"}, harvest_status="accepted")
    tgz = cfg.raw / "gh_a_b.tar.gz"
    tgz.parent.mkdir(parents=True)
    tgz.write_bytes(
        _tarball(
            {
                "b-main/icons/home.svg": SVG,
                "b-main/icons/sub/car.svg": SVG,
                "b-main/../../evil.svg": SVG,
                "b-main/node_modules/x/y.svg": SVG,
                "b-main/docs/readme.md": b"# hi",
                "b-main/LICENSE": b"Permission is hereby granted, free of charge",
            }
        )
    )
    ad = GitSvgAdapter(cfg, conn)
    items = {i.original_id: i for i in ad.items("gh:a/b")}
    assert set(items) == {"icons/home.svg", "icons/sub/car.svg"}
    assert items["icons/sub/car.svg"].categories == ["sub"]
    assert ad.meta["gh:a/b"] == {"license": "MIT", "svgs": 2, "rasters": 0}
    assert not (tmp_path / "evil.svg").exists()
    assert not tgz.exists()  # tarball removed after extraction


def _png(w, h):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(img).rectangle((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_tarball_takes_raster_icons_where_no_svg_exists(tmp_path, monkeypatch):
    from handdown import raster

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(conn, None, id="gh:a/b", platform_id="github", name="b", adapter="git-svg", adapter_args={"repo": "a/b"}, harvest_status="accepted")
    tgz = cfg.raw / "gh_a_b.tar.gz"
    tgz.parent.mkdir(parents=True)
    tgz.write_bytes(
        _tarball(
            {
                "b-main/icons/home.svg": SVG,
                "b-main/png/home.png": _png(32, 32),  # the vector exists: skipped
                "b-main/png/16/save.png": _png(16, 16),
                "b-main/png/32x32/save.png": _png(32, 32),  # the largest size is kept
                "b-main/png/apps/save.png": _png(24, 24),  # another folder, another icon
                "b-main/img/ok.gif": _png(24, 24),
                "b-main/img/banner.png": _png(1200, 200),
                "b-main/docs/screenshot.png": _png(1920, 1080),
            }
        )
    )
    ad = GitSvgAdapter(cfg, conn)
    items = {i.original_id: i for i in ad.items("gh:a/b")}
    assert set(items) == {"icons/home.svg", "png/32x32/save.png", "png/apps/save.png", "img/ok.gif"}
    save = items["png/32x32/save.png"]
    assert save.format == "raster" and raster.is_raster(save.svg) and save.name == "save"
    assert raster.decode(save.svg).size == (32, 32)
    assert items["icons/home.svg"].format == "svg"
    assert ad.meta["gh:a/b"]["rasters"] == 3


def _row(**kw):
    base = {"name": "", "notes": "", "id": "gh:x/y", "adapter_args": "{}", "popularity": "{}", "platform_id": "github"}
    base.update(kw)
    return base


def test_triage_decisions():
    iconify = {"tablericons": "iconify:tabler"}
    assert decide(_row(name="tabler-icons"), iconify)[0] == "rejected"
    ok = _row(name="pictograms", notes="Open pictogram icon set svg", popularity=json.dumps({"stars": 40}))
    assert decide(ok, iconify)[0] == "accepted"
    tool = _row(name="icon-picker", notes="React component icon picker", popularity=json.dumps({"stars": 900}))
    assert decide(tool, iconify)[0] == "rejected"
    big = _row(name="icons", notes="svg icons", adapter_args=json.dumps({"size_kb": 900_000}))
    assert "too large" in decide(big, iconify)[1]


def _commons(tmp_path, monkeypatch, args):
    import httpx

    from handdown.adapters.commons import CommonsAdapter

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    record_candidate(
        conn,
        None,
        id="commons:x",
        platform_id="commons",
        name="X",
        adapter="commons",
        url="https://commons.wikimedia.org/wiki/Category:X_signs",
        adapter_args=args,
        harvest_status="blocked-network",
    )
    fetched = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        if req.url.host == "upload.example":
            fetched.append(req.url.path)
            return httpx.Response(200, content=_png(64, 64) if "thumb" in req.url.path else SVG)
        if p.get("list") == "categorymembers":
            if p["cmtitle"] == "Category:X signs":
                members = [{"title": "File:A.svg"}, {"title": "File:B.png"}, {"title": "File:Photo.jpg"}, {"title": "Category:Sub"}]
                return httpx.Response(200, json={"query": {"categorymembers": members}})
            return httpx.Response(200, json={"query": {"categorymembers": [{"title": "File:C.svg"}]}})
        assert p.get("iiurlwidth") == "512"  # rasters come as thumbnails
        pages = []
        for t in p["titles"].split("|"):
            png = t.endswith(".png")
            info = {
                "mime": "image/png" if png else "image/svg+xml",
                "size": 100,
                "width": 2000 if png else 24,
                "height": 2000 if png else 24,
                "url": f"https://upload.example/{t}",
                "thumburl": f"https://upload.example/thumb/{t}",
                "thumbwidth": 512,
                "thumbheight": 512,
                "descriptionurl": f"https://commons/{t}",
                "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": "<a href='x'>Jane</a>"}},
            }
            pages.append({"title": t, "imageinfo": [info], "categories": [{"title": "Category:X signs"}]})
        return httpx.Response(200, json={"query": {"pages": pages}})

    ad = CommonsAdapter(cfg, conn, delay=0)
    ad.client = httpx.Client(transport=httpx.MockTransport(handler))
    return list(ad.items("commons:x")), fetched


def test_commons_adapter_with_mock_api(tmp_path, monkeypatch):
    from handdown import raster

    items, _ = _commons(tmp_path, monkeypatch, {"depth": 1})
    by = {i.name: i for i in items}
    assert sorted(by) == ["A", "B", "C"]  # the photo (JPEG) is not a pictogram source
    meta = json.loads(by["A"].description)
    assert meta["license"] == "CC0" and meta["artist"] == "Jane"
    assert by["A"].categories == ["X signs"] and by["A"].format == "svg"
    assert by["B"].format == "raster" and raster.decode(by["B"].svg).size == (64, 64)


def test_commons_formats_limit_a_rerun_to_rasters(tmp_path, monkeypatch):
    items, fetched = _commons(tmp_path, monkeypatch, {"depth": 1, "formats": ["png", "gif"]})
    assert [i.name for i in items] == ["B"] and fetched == ["/thumb/File:B.png"]


def test_commons_text_handles_numbers_and_language_dicts():
    from handdown.adapters.commons import _text

    assert _text(2019.0) == "2019.0"
    assert _text({"en": "<b>Sign</b>", "de": "Schild"}) == "Sign"
    assert _text("") is None and _text(None) is None


def test_iconify_restricts_to_job_list(tmp_path, monkeypatch):
    from handdown.adapters.iconify import IconifyAdapter

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    pkg = cfg.raw / "iconify" / "package"
    pkg.mkdir(parents=True)
    (pkg / "collections.json").write_text(json.dumps({p: {"name": p} for p in ("a", "b", "c")}))
    assert len(list(IconifyAdapter(cfg, conn).sources())) == 3  # no job list: everything
    record_candidate(conn, None, id="iconify:b", platform_id="iconify", name="b", adapter="iconify", harvest_status="accepted")
    assert [s.id for s in IconifyAdapter(cfg, conn).sources()] == ["iconify:b"]


def test_recheck_rasters_accepts_repositories_with_raster_icon_sets(tmp_path, monkeypatch):
    from handdown import triage

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    conn = db.connect(Config().db_path)
    for repo, note in (("a/pngs", "only 0 SVG files"), ("b/docs", "only 1 SVG files"), ("c/skip", "triage: score -7")):
        record_candidate(
            conn, None, id=f"gh:{repo}", platform_id="github", name=repo, adapter="git-svg", adapter_args={"repo": repo}, harvest_status="rejected"
        )
        conn.execute("UPDATE source SET notes=? WHERE id=?", (note, f"gh:{repo}"))
    trees = {
        "a/pngs": [{"path": f"icons/32/i{n}.png", "type": "blob", "size": 900} for n in range(25)] + [{"path": "README.md", "type": "blob", "size": 10}],
        "b/docs": [{"path": "docs/screenshot.png", "type": "blob", "size": 90000}, {"path": "node_modules/x/a.png", "type": "blob", "size": 10}] * 15,
    }
    asked = []

    def fetch_tree(repo):
        asked.append(repo)
        return trees[repo]

    out = triage.recheck_rasters(conn, fetch_tree, min_files=20)
    assert out == {"checked": 2, "accepted": 1}
    assert sorted(asked) == ["a/pngs", "b/docs"]  # only harvest-time rejections, not irrelevant repositories
    row = conn.execute("SELECT harvest_status, notes FROM source WHERE id='gh:a/pngs'").fetchone()
    assert row[0] == "accepted" and "25 raster icons" in row[1]
    assert conn.execute("SELECT harvest_status FROM source WHERE id='gh:b/docs'").fetchone()[0] == "rejected"


def test_seed_queries_include_raster_sets():
    from handdown.discover import seed_queries

    q = seed_queries()
    assert ("github", "pixel art icons") in q and ("npm", "keywords:png-icons") in q
