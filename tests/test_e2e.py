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
    note = next(cfg.vault.rglob("ashcan*.md"))
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
