"""Whole pipeline over a tiny fixture corpus: 3 sources, 7 pictograms."""

from collections.abc import Iterator

import pytest
import yaml

from handdown import cluster, concepts, db, pipeline, score, vault
from handdown.adapters.base import Item, SourceInfo
from handdown.config import Config

NS = 'xmlns="http://www.w3.org/2000/svg"'
CAN = '<path d="M6 7h12l-1 14H7z"/><rect x="4" y="4" width="16" height="2"/>'
CAN_O = '<path fill="none" stroke="currentColor" stroke-width="2" d="M6 7h12l-1 14H7z"/><rect x="4" y="4" width="16" height="2"/>'
X = '<path fill="none" stroke="#000" stroke-width="3" d="M5 5L19 19M19 5L5 19"/>'
HOUSE = '<path d="M12 3l9 8h-3v9H6v-9H3z"/>'
FIXTURE = {
    "a": [("trash-can", CAN), ("home", HOUSE), ("close", X)],
    "b": [("trash-can-outline", CAN_O), ("home-24-regular", HOUSE)],
    "c": [("trash-can", X), ("ashcan", CAN)],  # an X drawn for "trash can": a second depiction
}


class Fixture:
    name = "fixture"

    def sources(self) -> Iterator[SourceInfo]:
        for sid in FIXTURE:
            yield SourceInfo(id=f"fx:{sid}", name=f"Fixture {sid}", platform_id="fixture", license_spdx="CC0-1.0", domain="ui")

    def items(self, source_id: str) -> Iterator[Item]:
        for name, body in FIXTURE[source_id.split(":")[1]]:
            yield Item(original_id=name, name=name, svg=f'<svg {NS} viewBox="0 0 24 24">{body}</svg>')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    try:
        concepts.wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")
    return cfg, conn


def test_pipeline_and_vault_roundtrip(env):
    cfg, conn = env
    assert sum(pipeline.harvest(conn, Fixture()).values()) == 7
    assert pipeline.process(conn, cfg, workers=1) == (7, 0)
    assert pipeline.dedupe(conn) >= 1  # identical houses / cans collapse
    concepts.run(conn)
    assert cluster.run(conn) >= 3
    score.run(conn, log=lambda *_: None)

    can = conn.execute("SELECT COUNT(*) FROM depiction_cluster WHERE concept_id='wn:ashcan.n.01'").fetchone()[0]
    assert can == 2  # can shape and X shape
    combined = conn.execute("SELECT COUNT(*) FROM rating WHERE metric='combined'").fetchone()[0]
    assert combined > 0

    ex = vault.Exporter(conn, cfg, min_sources=1)
    assert ex.run(log=lambda *_: None) >= 2
    note = next(p for p in cfg.vault.rglob("concepts/**/*.md") if vault.read_frontmatter(p).get("concept") == "wn:ashcan.n.01")
    text = note.read_text()
    assert "## 1." in text and "## 2." in text

    # Hand edits: an override and notes survive regeneration and reach the DB.
    fm = vault.read_frontmatter(note)
    rep = conn.execute("SELECT representative_id FROM depiction_cluster WHERE concept_id='wn:ashcan.n.01' ORDER BY size DESC").fetchone()[0]
    fm["overrides"] = {f"cluster-rep:{rep}": {"description": "trash can", "meaning": 90}}
    fm["notes"] = "prefer the lid variant"
    body = text.split("\n---\n", 1)[1]
    note.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n" + body)

    assert vault.sync(conn, cfg) == 2
    val = conn.execute("SELECT value FROM rating WHERE pictogram_id=? AND metric='meaning' AND is_override=1", (rep,)).fetchone()[0]
    assert val == 90
    vault.Exporter(conn, cfg, min_sources=1).run(log=lambda *_: None)
    again = vault.read_frontmatter(note)
    assert again["notes"] == "prefer the lid variant"
    assert "trash can" in note.read_text()


def test_html_export(env):
    import json

    from handdown import site

    cfg, conn = env
    pipeline.harvest(conn, Fixture())
    pipeline.process(conn, cfg, workers=1)
    concepts.run(conn)
    cluster.run(conn)
    score.run(conn, log=lambda *_: None)
    assert site.export(conn, cfg, min_sources=1, log=lambda *_: None) >= 2
    index = json.loads((cfg.site / "data" / "concepts.json").read_text())
    ashcan = next(c for c in index if c["label"] in ("trash can", "ashcan"))
    page = (cfg.site / "c" / f"{ashcan['f']}.html").read_text()
    assert page.count('class="cluster"') == 2
    assert "<script>alert" not in page
    for c in index:
        assert (cfg.site / "svg" / f"{c['r']}.svg").exists()


def test_shard_roundtrip(env, tmp_path, monkeypatch):
    from handdown import shard

    cfg, conn = env
    pipeline.harvest(conn, Fixture())
    pipeline.process(conn, cfg, workers=1)
    out = tmp_path / "shard.tar.gz"
    assert shard.export_shard(conn, cfg, out) == 7

    other = tmp_path / "other"
    monkeypatch.setenv("HANDDOWN_ROOT", str(other))
    cfg2 = Config()
    conn2 = db.connect(cfg2.db_path)
    assert shard.import_shard(conn2, cfg2, out) == 7
    assert shard.import_shard(conn2, cfg2, out) == 7  # idempotent
    q = lambda sql: conn2.execute(sql).fetchone()[0]  # noqa: E731
    assert q("SELECT COUNT(*) FROM pictogram") == 7
    assert q("SELECT COUNT(*) FROM raw_svg") == 7
    assert q("SELECT COUNT(*) FROM feature") == 7
    assert q("SELECT COUNT(*) FROM rating WHERE metric='legibility'") == 7
    assert q("SELECT COUNT(*) FROM source WHERE harvest_status='harvested'") == 3
    for (path,) in conn2.execute("SELECT norm_path FROM pictogram"):
        assert not path.startswith("/")  # portable between host and container
        assert cfg2.resolve(path).exists()


def test_encrypted_shard(env, tmp_path, monkeypatch):
    import pytest as _pytest

    from handdown import shard

    cfg, conn = env
    pipeline.harvest(conn, Fixture())
    pipeline.process(conn, cfg, workers=1)
    key = tmp_path / "keys" / "age.key"
    pub = shard.keygen(key)
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    with _pytest.raises(shard.ShardError):
        shard.keygen(key)  # never overwrite a private key

    out = tmp_path / "shard.tar.gz"
    assert shard.export_shard(conn, cfg, out, [pub]) == 7
    enc = tmp_path / "shard.tar.gz.age"
    assert enc.exists() and not out.exists() and not (tmp_path / "shard.tar.gz.plain").exists()
    assert b"<svg" not in enc.read_bytes()

    other = tmp_path / "other"
    monkeypatch.setenv("HANDDOWN_ROOT", str(other))
    cfg2 = Config()
    conn2 = db.connect(cfg2.db_path)
    with _pytest.raises(shard.ShardError):
        shard.import_shard(conn2, cfg2, enc)  # no key
    wrong = tmp_path / "keys" / "wrong.key"
    shard.keygen(wrong)
    with _pytest.raises(shard.ShardError):
        shard.import_shard(conn2, cfg2, enc, wrong)
    assert shard.import_shard(conn2, cfg2, enc, key) == 7
    assert not list((cfg2.cache / "shards").glob("*"))  # decrypted copy removed


def test_cli_refuses_plain_shard_on_actions(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from handdown.cli import app

    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("HANDDOWN_AGE_RECIPIENT", raising=False)
    r = CliRunner().invoke(app, ["export-shard", str(tmp_path / "s.tar.gz")])
    assert r.exit_code != 0 and "refusing" in r.output + str(r.exception)
    assert not (tmp_path / "s.tar.gz").exists()


def test_job_list_sources_are_accepted_on_runner(env):
    from handdown import shard

    _cfg, conn = env
    rows = [{"id": "commons:x", "platform_id": "commons", "name": "X", "adapter": "commons", "harvest_status": "failed"}]
    assert shard.import_sources(conn, rows) == 1
    assert conn.execute("SELECT harvest_status FROM source WHERE id='commons:x'").fetchone()[0] == "accepted"


def test_resolve_accepts_foreign_absolute_paths(env):
    cfg, _conn = env
    target = cfg.norm_path("ab" + "0" * 62)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<svg/>")
    foreign = "/srv/other-checkout/data/norm/ab/" + target.name
    assert cfg.resolve(foreign) == target
    assert cfg.resolve("data/norm/ab/" + target.name) == target
    assert cfg.resolve(None) is None
