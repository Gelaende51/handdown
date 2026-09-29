import json

import pytest

from handdown import concepts, db, wikidata
from handdown.config import Config


def test_apply_links_qid_and_fills_missing_labels(tmp_path, monkeypatch):
    try:
        wn = concepts.wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    conn = db.connect(Config().db_path)
    c = concepts.resolve(["ashcan"], wn)
    conn.execute(
        "INSERT INTO concept (id, label, wordnet_synset, labels) VALUES (?,?,?,?)",
        (c.id, c.label, c.wordnet_synset, json.dumps({"en": "ashcan", "fr": "poubelle"})),
    )
    s = wn.synset("ashcan.n.01")
    maps = {"pwn30": {f"{s.offset():08d}-n": "i1"}, "pwn31": {"99999999-n": "i1"}}
    monkeypatch.setattr(wikidata, "_cili", lambda version, client: maps[version])
    f = tmp_path / "wd.jsonl"
    f.write_text(json.dumps({"wn31": "99999999-n", "qid": "Q123", "labels": {"de": "Mülleimer", "fr": "benne"}}) + "\n")
    assert wikidata.apply(conn, f, log=lambda *_: None) == 1
    qid, labels = conn.execute("SELECT wikidata_qid, labels FROM concept").fetchone()
    labels = json.loads(labels)
    assert qid == "Q123"
    assert labels["de"] == "Mülleimer"
    assert labels["fr"] == "poubelle"  # existing label kept
