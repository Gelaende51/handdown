import json

import pytest

from handdown import db
from handdown.config import Config


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from handdown.concepts import wordnet

    try:
        wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")
    monkeypatch.setenv("HANDDOWN_ROOT", str(tmp_path))
    return db.connect(Config().db_path)


def test_vocabulary_objects_views_features(conn):
    from handdown import vision

    rows = [
        ("wn:cup.n.01", "cup", "cup.n.01"),
        ("wn:download.v.01", "download", "download.v.01"),
        ("wn:mug.n.04", "mug", "mug.n.04"),
        ("term:mug", "mug", None),
    ]
    conn.executemany("INSERT INTO concept (id, label, wordnet_synset) VALUES (?,?,?)", rows)
    labels = vision.build_vocabulary(conn)
    objects = [lab for lab in labels if lab["kind"] == "object"]
    assert {lab["id"] for lab in objects} >= {"wn:cup.n.01"}
    assert not any(lab["id"] == "wn:download.v.01" for lab in objects)
    assert len({lab["text"] for lab in labels}) == len(labels)  # deduplicated texts
    assert {lab["kind"] for lab in labels} == {"object", "view", "feature"}
    assert all(json.dumps(lab) for lab in labels)
