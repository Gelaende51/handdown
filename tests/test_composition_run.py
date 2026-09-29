from collections.abc import Iterator

from handdown import db
from handdown.adapters.base import Item, SourceInfo
from handdown.composition import run as comp
from handdown.config import Config
from handdown.pipeline import dedupe, harvest, process

NS = 'xmlns="http://www.w3.org/2000/svg"'
BELL = '<path d="M6 17h12l-2-3v-4a4 4 0 0 0-8 0v4z"/>'


class Composites:
    name = "fixture"

    def sources(self) -> Iterator[SourceInfo]:
        yield SourceInfo(id="fx:c", name="C", platform_id="fixture", domain="ui")
        yield SourceInfo(id="fx:d", name="D", platform_id="fixture", domain="ui")

    def items(self, source_id: str) -> Iterator[Item]:
        yield Item(original_id="bell", name="bell", svg=f'<svg {NS} viewBox="0 0 24 24">{BELL}</svg>')
        off = f'{BELL}<path d="M3 3L21 21" stroke="#000" stroke-width="2"/>'
        yield Item(original_id="bell-off", name="bell-off", svg=f'<svg {NS} viewBox="0 0 24 24">{off}</svg>')


def _catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    cfg = Config()
    conn = db.connect(cfg.db_path)
    harvest(conn, Composites())
    process(conn, cfg, workers=1)
    dedupe(conn)
    return cfg, conn


def test_run_stores_composites_under_elements_idempotently(tmp_path, monkeypatch):
    cfg, conn = _catalog(tmp_path, monkeypatch)
    first = comp.run(conn, cfg, log=lambda *_: None)
    again = comp.run(conn, cfg, log=lambda *_: None)
    assert first == again
    assert conn.execute("SELECT COUNT(*) FROM composition").fetchone()[0] == 1  # bell-off (duplicate in fx:d skipped)
    roles = {r[0] for r in conn.execute("SELECT role FROM composition_part")}
    assert {"base", "negation"} <= roles
    base_concept = conn.execute("SELECT concept_id FROM composition_part WHERE role='base'").fetchone()[0]
    assert base_concept and "bell" in base_concept


def test_manual_rows_survive_rerun(tmp_path, monkeypatch):
    cfg, conn = _catalog(tmp_path, monkeypatch)
    comp.run(conn, cfg, log=lambda *_: None)
    conn.execute("UPDATE composition SET method='manual', fit='sequence'")
    conn.commit()
    comp.run(conn, cfg, log=lambda *_: None)
    assert tuple(conn.execute("SELECT method, fit FROM composition").fetchone()) == ("manual", "sequence")
