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
    for cid, label, synset, _parent in rows:
        # WordNet objects only: term phrases ("arrow left circle") dilute zero-shot
        if synset and synset_lex.get(cid) in OBJECT_LEXNAMES:
            add(cid, "object", f"a pictogram of a {label}")
    for view, text in VIEW_TEXTS.items():
        add(f"view:{view}", "view", f"a pictogram {text}")
    for feature in FEATURES:
        add(f"feature:{feature}", "feature", f"a pictogram with {feature}")
    return labels


TOP_OBJECTS = 5
FEATURE_MIN = 0.2


def _images(cfg: Any, rows: list[sqlite3.Row], size: int = 224) -> tuple[list[Any], list[int]]:
    """RGB renders (black on white) of the rows; blank renders are skipped."""
    import numpy as np
    from PIL import Image

    from .metrics import render

    images, ids = [], []
    for r in rows:
        path = cfg.resolve(r["norm_path"])
        if path is None or not path.exists():
            continue
        ink = render(path.read_text(), size)
        if ink.max() < 0.05:
            continue
        images.append(Image.fromarray(((1 - ink) * 255).astype(np.uint8)).convert("RGB"))
        ids.append(r["id"])
    return images, ids


def embed(conn: sqlite3.Connection, cfg: Any, labels: list[dict[str, Any]], models: Any, batch: int = 64, log: Any = print) -> int:
    """Embed every unique valid pictogram and store its top labels."""
    import numpy as np

    by_kind = {k: [lab["id"] for lab in labels if lab["kind"] == k] for k in ("object", "view", "feature")}
    rows = conn.execute("SELECT id, norm_path FROM pictogram WHERE duplicate_of IS NULL AND svg_valid = 1 AND norm_path IS NOT NULL ORDER BY id").fetchall()
    n = 0
    for start in range(0, len(rows), batch):
        images, ids = _images(cfg, rows[start : start + batch])
        if not images:
            continue
        feats = models.image_features(images)
        scores = {k: models.label_scores(feats["siglip"], k) for k in by_kind if by_kind[k]}
        for i, pid in enumerate(ids):
            for model, arr in feats.items():
                conn.execute("INSERT OR REPLACE INTO embedding VALUES (?,?,?)", (pid, model, np.asarray(arr[i], dtype=np.float16).tobytes()))
            conn.execute("DELETE FROM vision_label WHERE pictogram_id=?", (pid,))
            rows_out = []
            if "object" in scores:
                top = np.argsort(-scores["object"][i])[:TOP_OBJECTS]
                rows_out += [(pid, "object", by_kind["object"][j], float(scores["object"][i][j]), r + 1) for r, j in enumerate(top)]
            if "view" in scores:
                j = int(np.argmax(scores["view"][i]))
                rows_out.append((pid, "view", by_kind["view"][j], float(scores["view"][i][j]), 1))
            if "feature" in scores:
                feats_on = [j for j in np.argsort(-scores["feature"][i]) if scores["feature"][i][j] >= FEATURE_MIN]
                rows_out += [(pid, "feature", by_kind["feature"][j], float(scores["feature"][i][j]), r + 1) for r, j in enumerate(feats_on)]
            conn.executemany("INSERT INTO vision_label VALUES (?,?,?,?,?)", rows_out)
            n += 1
        conn.commit()
        if (start // batch) % 50 == 0:
            log(f"  embedded {n}/{len(rows)}")
    return n


def export_vision(conn: sqlite3.Connection, out: Any, recipients: list[str] | None) -> int:
    """Stream embeddings and labels as JSONL (gzip), keyed by source and
    original id; age-encrypted when recipients are given."""
    import base64
    import gzip
    import json
    from pathlib import Path

    from . import shard

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for p in conn.execute("SELECT id, source_id, original_id, sha256 FROM pictogram WHERE id IN (SELECT DISTINCT pictogram_id FROM embedding)"):
            emb = {m: base64.b64encode(v).decode() for m, v in conn.execute("SELECT model, vec FROM embedding WHERE pictogram_id=?", (p[0],))}
            labels = [list(r) for r in conn.execute("SELECT kind, label_id, score, rank FROM vision_label WHERE pictogram_id=?", (p[0],))]
            f.write(json.dumps({"source_id": p[1], "original_id": p[2], "sha256": p[3], "emb": emb, "labels": labels}) + "\n")
            n += 1
    if recipients:
        shard.encrypt_file(out, recipients)
    return n


def import_vision(conn: sqlite3.Connection, cfg: Any, path: Any, identity: Any) -> dict[str, int]:
    """Merge a vision shard; rows for pictograms unknown here are counted and skipped."""
    import base64
    import gzip
    import json
    from pathlib import Path

    from . import shard

    path = Path(path)
    plain = shard.decrypt_to(path, Path(identity) if identity else None, cfg) if path.name.endswith(".age") else path
    stats = {"matched": 0, "unknown": 0}
    try:
        with gzip.open(plain, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                row = conn.execute("SELECT id FROM pictogram WHERE source_id=? AND original_id=?", (rec["source_id"], rec["original_id"])).fetchone()
                if row is None:
                    stats["unknown"] += 1
                    continue
                pid = row[0]
                for model, b64 in rec["emb"].items():
                    conn.execute("INSERT OR REPLACE INTO embedding VALUES (?,?,?)", (pid, model, base64.b64decode(b64)))
                conn.execute("DELETE FROM vision_label WHERE pictogram_id=?", (pid,))
                conn.executemany("INSERT OR REPLACE INTO vision_label VALUES (?,?,?,?,?)", [(pid, *lab) for lab in rec["labels"]])
                stats["matched"] += 1
                if stats["matched"] % 5000 == 0:
                    conn.commit()
    finally:
        if plain != path:
            plain.unlink(missing_ok=True)
    conn.commit()
    return stats


IMAGENET = ((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))  # DINOv2
SIGLIP_NORM = ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))


def pixels(images: list[Any], mean: tuple[float, ...], std: tuple[float, ...], size: int = 224) -> Any:
    """Normalized NCHW float32 array. Done in numpy because transformers'
    image processors now require torchvision, which the runners lack."""
    import numpy as np
    from PIL import Image

    arr = np.stack([np.asarray(im.convert("RGB").resize((size, size), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0 for im in images])
    arr = (arr - np.array(mean, dtype=np.float32)) / np.array(std, dtype=np.float32)
    return np.ascontiguousarray(arr.transpose(0, 3, 1, 2), dtype=np.float32)


def as_features(out: Any) -> Any:
    """get_*_features returns a tensor in older transformers and a model
    output (with pooler_output) in newer ones."""
    return out if hasattr(out, "norm") else out.pooler_output


class TorchModels:
    """DINOv2-small and SigLIP on CPU (runners only: torch + transformers)."""

    def __init__(self, labels: list[dict[str, Any]], dinov2: str = "facebook/dinov2-small", siglip: str = "google/siglip-base-patch16-224"):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.dmodel = AutoModel.from_pretrained(dinov2).eval()
        self.smodel = AutoModel.from_pretrained(siglip).eval()
        tokenizer = AutoTokenizer.from_pretrained(siglip)
        self.text: dict[str, Any] = {}
        for kind in ("object", "view", "feature"):
            texts = [lab["text"] for lab in labels if lab["kind"] == kind]
            chunks = []
            with torch.no_grad():
                for i in range(0, len(texts), 256):
                    t = tokenizer(texts[i : i + 256], padding="max_length", max_length=64, truncation=True, return_tensors="pt")
                    e = as_features(self.smodel.get_text_features(input_ids=t["input_ids"]))
                    chunks.append(e / e.norm(dim=-1, keepdim=True))
            if chunks:
                self.text[kind] = torch.cat(chunks)

    def image_features(self, images: list[Any]) -> dict[str, Any]:
        torch = self.torch
        with torch.no_grad():
            d = self.dmodel(pixel_values=torch.from_numpy(pixels(images, *IMAGENET))).last_hidden_state[:, 0]
            s = as_features(self.smodel.get_image_features(pixel_values=torch.from_numpy(pixels(images, *SIGLIP_NORM))))
        d = d / d.norm(dim=-1, keepdim=True)
        s = s / s.norm(dim=-1, keepdim=True)
        return {"dinov2": d.numpy(), "siglip": s.numpy()}

    def label_scores(self, siglip: Any, kind: str) -> Any:
        torch = self.torch
        logits = torch.from_numpy(siglip) @ self.text[kind].T * self.smodel.logit_scale.exp() + self.smodel.logit_bias
        return torch.sigmoid(logits).numpy()


def apply(conn: sqlite3.Connection, min_score: float = 0.3) -> dict[str, int]:
    """Fill depictions that have no object (or an unknown view) with the
    majority top-1 vision label of their members, when its mean score is at
    least ``min_score``. Existing objects and views are never overwritten."""
    counts = {"objects": 0, "views": 0}
    for kind, column, empty in (("object", "object_id", "object_id IS NULL"), ("view", "view", "view = 'unknown'")):
        rows = conn.execute(
            f"""SELECT d.id, v.label_id, COUNT(*) AS n, AVG(v.score) AS mean
                FROM depiction d JOIN style_group g ON g.depiction_id = d.id
                JOIN style_member sm ON sm.style_group_id = g.id
                JOIN vision_label v ON v.pictogram_id = sm.pictogram_id AND v.kind = ? AND v.rank = 1
                WHERE d.{empty}
                GROUP BY d.id, v.label_id ORDER BY d.id, n DESC, mean DESC""",
            (kind,),
        ).fetchall()
        best: dict[int, tuple[str, float]] = {}
        for did, label, _n, mean in rows:
            best.setdefault(did, (label, mean))  # first row per depiction = majority label
        for did, (label, mean) in best.items():
            if mean < min_score:
                continue
            value = label.removeprefix("view:") if kind == "view" else label
            conn.execute(f"UPDATE depiction SET {column} = ? WHERE id = ? AND {empty}", (value, did))
            counts["objects" if kind == "object" else "views"] += 1
    conn.commit()
    return counts
