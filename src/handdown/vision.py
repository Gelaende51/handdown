"""Vision models (DINOv2, SigLIP) for all pictograms, run on GitHub Actions.

Runners embed their share of the catalog and score it against a label
vocabulary (objects, views, features); results travel back as encrypted
shards and fill objects and views locally. Torch and transformers are only
installed on runners and imported lazily.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .hierarchy.names import OBJECT_LEXNAMES

VIEW_TEXTS = {
    "front": "seen from the front",
    "side": "seen from the side",
    "top": "seen from above",
    "bottom": "seen from below",
    "three-quarter": "in three-quarter perspective",
    "isometric": "in isometric projection",
    "partial": "as a partial close-up",
    "full": "shown in full",
}
FEATURES = [
    "steam",
    "saucer",
    "lid",
    "handle",
    "straw",
    "frame",
    "circle frame",
    "square frame",
    "slash",
    "cross",
    "arrow",
    "plus sign",
    "check mark",
    "text",
    "numbers",
    "stars",
    "motion lines",
    "shadow",
    "person",
    "hand",
]


def build_vocabulary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Label vocabulary: object concepts (drawable things), views, features.
    Texts are unique; the first concept with a text wins."""
    from .concepts import wordnet

    wn = wordnet()
    labels: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label_id: str, kind: str, text: str) -> None:
        if text not in seen:
            seen.add(text)
            labels.append({"id": label_id, "kind": kind, "text": text})

    rows = conn.execute("SELECT id, label, wordnet_synset, parent_id FROM concept ORDER BY id").fetchall()
    synset_lex = {}
    for cid, _label, synset, _parent in rows:
        if synset:
            try:
                synset_lex[cid] = wn.synset(synset).lexname()
            except Exception:  # unknown synset id (older WordNet data)
                continue
    for cid, label, synset, parent in rows:
        lex = synset_lex.get(cid) if synset else synset_lex.get(parent or "")
        if lex in OBJECT_LEXNAMES:
            add(cid, "object", f"a pictogram of a {label}")
    for view, text in VIEW_TEXTS.items():
        add(f"view:{view}", "view", f"a pictogram {text}")
    for feature in FEATURES:
        add(f"feature:{feature}", "feature", f"a pictogram with {feature}")
    return labels
