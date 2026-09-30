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
    assert ad.meta["gh:a/b"] == {"license": "MIT", "svgs": 2}
    assert not (tmp_path / "evil.svg").exists()
    assert not tgz.exists()  # tarball removed after extraction


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


def test_commons_adapter_with_mock_api(tmp_path, monkeypatch):
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
        adapter_args={"depth": 1},
        harvest_status="blocked-network",
    )

    def handler(req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        if req.url.host == "upload.example":
            return httpx.Response(200, text=SVG.decode())
        if p.get("list") == "categorymembers":
            if p["cmtitle"] == "Category:X signs":
                return httpx.Response(200, json={"query": {"categorymembers": [{"title": "File:A.svg"}, {"title": "File:B.png"}, {"title": "Category:Sub"}]}})
            return httpx.Response(200, json={"query": {"categorymembers": [{"title": "File:C.svg"}]}})
        pages = [
            {
                "title": t,
                "imageinfo": [
                    {
                        "mime": "image/svg+xml",
                        "size": 100,
                        "url": f"https://upload.example/{t}",
                        "descriptionurl": f"https://commons/{t}",
                        "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": "<a href='x'>Jane</a>"}},
                    }
                ],
                "categories": [{"title": "Category:X signs"}],
            }
            for t in p["titles"].split("|")
        ]
        return httpx.Response(200, json={"query": {"pages": pages}})

    ad = CommonsAdapter(cfg, conn, delay=0)
    ad.client = httpx.Client(transport=httpx.MockTransport(handler))
    items = list(ad.items("commons:x"))
    assert sorted(i.name for i in items) == ["A", "C"]
    meta = json.loads(items[0].description)
    assert meta["license"] == "CC0" and meta["artist"] == "Jane"
    assert items[0].categories == ["X signs"]


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
