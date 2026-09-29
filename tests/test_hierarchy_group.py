from collections.abc import Iterator

import numpy as np

from handdown import concepts, db
from handdown.adapters.base import Item, SourceInfo
from handdown.config import Config
from handdown.hierarchy import group as hg
from handdown.metrics import measure
from handdown.pipeline import dedupe, harvest, process

NS = 'xmlns="http://www.w3.org/2000/svg"'
CUP = '<path d="M5 8h11v7a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/><path d="M16 10h2a2 2 0 0 1 0 4h-2" fill="none" stroke="#000" stroke-width="1.5"/>'
CUP_O = (
    '<path d="M5 8h11v7a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" fill="none" stroke="#000" stroke-width="1.5"/>'
    '<path d="M16 10h2a2 2 0 0 1 0 4h-2" fill="none" stroke="#000" stroke-width="1.5"/>'
)
MIRROR = f'<g transform="translate(24 0) scale(-1 1)">{CUP}</g>'
STEAM = CUP + '<path d="M8 2c1 1-1 2 0 3M11 2c1 1-1 2 0 3" fill="none" stroke="#000" stroke-width="1.2"/>'


def svg(b):
    return f'<svg {NS} viewBox="0 0 24 24">{b}</svg>'


def feat(b):
    return np.asarray(measure(svg(b))["feature"], dtype=np.float16)


def test_nested_labels_style_vs_depiction():
    style, dep = hg.nested_labels(np.stack([feat(CUP), feat(CUP_O), feat(MIRROR), feat(STEAM)]))
    assert style[0] == style[1]  # outline and filled: same style group
    assert style[2] != style[0]  # mirrored: not style
    assert style[3] != style[0]  # steam present: other variety
    assert dep[2] != dep[0]  # mirrored: its own depiction (orientation)
    for a in range(4):  # nesting: same style group -> same depiction
        for b in range(4):
            if style[a] == style[b]:
                assert dep[a] == dep[b]


class Cups:
    name = "fixture"

    def sources(self) -> Iterator[SourceInfo]:
        for s in ("a", "b"):
            yield SourceInfo(id=f"fx:{s}", name=s, platform_id="fixture", domain="ui")

    def items(self, source_id: str) -> Iterator[Item]:
        if source_id == "fx:a":
            yield Item(original_id="cup", name="coffee-cup", svg=svg(CUP))
            yield Item(original_id="dl", name="floppy-disk-download", svg=svg('<rect x="4" y="4" width="16" height="16"/>'))
        else:
            yield Item(original_id="cup", name="coffee-cup-outline", svg=svg(CUP_O))
            yield Item(original_id="dl", name="cloud-download", svg=svg('<circle cx="12" cy="12" r="7"/>'))
            yield Item(original_id="one", name="ic-24", svg=svg('<path d="M4 4h4v4z"/>'))


def _catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    harvest(conn, Cups())
    process(conn, cfg, workers=1)
    dedupe(conn)
    concepts.run(conn)
    return cfg, conn


def test_run_builds_levels_and_meanings(tmp_path, monkeypatch):
    _cfg, conn = _catalog(tmp_path, monkeypatch)
    first = hg.run(conn, log=lambda *_: None)
    assert first == hg.run(conn, log=lambda *_: None)  # idempotent
    sizes = [r[0] for r in conn.execute("SELECT sg.size FROM style_group sg JOIN depiction d ON d.id = sg.depiction_id WHERE d.object_id LIKE '%cup%'")]
    assert 2 in sizes  # outline + filled coffee cup: one style group
    rows = conn.execute(
        "SELECT DISTINCT d.object_id FROM meaning_link m JOIN depiction d ON d.id = m.depiction_id WHERE m.concept_id LIKE '%download%'"
    ).fetchall()
    assert len(rows) == 2  # floppy disk and cloud both mean download
    assert conn.execute("SELECT COUNT(*) FROM depiction WHERE object_id IS NULL").fetchone()[0] >= 1
