"""Vision models (DINOv2, SigLIP) for all pictograms, run on GitHub Actions.

Runners embed their share of the catalog and score it against a label
vocabulary (objects, views, features); results travel back as encrypted
shards and fill objects and views locally. Torch and transformers are only
installed on runners and imported lazily.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import provenance
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


# Classes for glyphs that depict no object; they compete with the objects so
# that abstract glyphs are not forced onto the nearest thing.
CLASSES = {
    "class:letter": "a letter of the alphabet",
    "class:number": "a number or digit",
    "class:script": "a character of a writing system",
    "class:braille": "a braille pattern of dots",
    "class:music": "a musical notation symbol",
    "class:geometric": "a simple geometric shape",
    "class:logo": "a brand logo",
    "class:abstract": "an abstract symbol",
    "class:arrow": "an arrow",
}


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
    for class_id, text in CLASSES.items():
        add(class_id, "object", f"a pictogram of {text}")
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


def embed(conn: sqlite3.Connection, cfg: Any, labels: list[dict[str, Any]], models: Any, batch: int = 64, log: Any = print, parts_only: bool = False) -> int:
    """Embed every unique valid pictogram (or only the parts cut out of composites) and store its top labels."""
    import numpy as np

    by_kind = {k: [lab["id"] for lab in labels if lab["kind"] == k] for k in ("object", "view", "feature")}
    if hasattr(models, "label_rows"):
        label_rows, scale, bias = models.label_rows()
        store_label_embeddings(conn, label_rows, scale, bias)
    parts = " AND derived_from IS NOT NULL" if parts_only else ""
    rows = conn.execute(
        f"SELECT id, norm_path FROM pictogram WHERE duplicate_of IS NULL AND svg_valid = 1 AND norm_path IS NOT NULL{parts} ORDER BY id"
    ).fetchall()
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
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('siglip_scale', 'siglip_bias')").fetchall())
        header = {
            "label_embeddings": [[r[0], r[1], base64.b64encode(r[2]).decode()] for r in conn.execute("SELECT label_id, kind, vec FROM label_embedding")],
            "scale": meta.get("siglip_scale"),
            "bias": meta.get("siglip_bias"),
        }
        f.write(json.dumps(header) + "\n")
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
    from .composition.extract import DERIVED

    path = Path(path)
    plain = shard.decrypt_to(path, Path(identity) if identity else None, cfg) if path.name.endswith(".age") else path
    stats = {"matched": 0, "unknown": 0}
    try:
        with gzip.open(plain, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if "label_embeddings" in rec:
                    if rec["label_embeddings"]:
                        conn.executemany(
                            "INSERT OR REPLACE INTO label_embedding VALUES (?,?,?)",
                            [(lid, kind, base64.b64decode(b64)) for lid, kind, b64 in rec["label_embeddings"]],
                        )
                        conn.execute("INSERT OR REPLACE INTO meta VALUES ('siglip_scale', ?)", (rec["scale"],))
                        conn.execute("INSERT OR REPLACE INTO meta VALUES ('siglip_bias', ?)", (rec["bias"],))
                    continue
                row = conn.execute("SELECT id, sha256 FROM pictogram WHERE source_id=? AND original_id=?", (rec["source_id"], rec["original_id"])).fetchone()
                if row is None:
                    stats["unknown"] += 1
                    continue
                # parts are cut out again on the runner: same id but another cut means another image
                if rec["source_id"] == DERIVED and rec.get("sha256") != row[1]:
                    stats["changed"] = stats.get("changed", 0) + 1
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
        self.label_ids = {k: [lab["id"] for lab in labels if lab["kind"] == k] for k in ("object", "view", "feature")}
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

    def label_rows(self) -> tuple[list[tuple[str, str, Any]], float, float]:
        rows = []
        for kind, ids in self.label_ids.items():
            vecs = self.text[kind].numpy()
            rows += [(lid, kind, vecs[i]) for i, lid in enumerate(ids)]
        return rows, float(self.smodel.logit_scale.exp().item()), float(self.smodel.logit_bias.item())

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
        with torch.no_grad():  # logit_scale/bias are parameters that require grad
            logits = torch.from_numpy(siglip) @ self.text[kind].T * self.smodel.logit_scale.exp() + self.smodel.logit_bias
            return torch.sigmoid(logits).detach().numpy()


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
            if mean < min_score or label.startswith("class:"):
                continue  # classes (letter, braille ...) are not drawable objects
            value = label.removeprefix("view:") if kind == "view" else label
            conn.execute(f"UPDATE depiction SET {column} = ? WHERE id = ? AND {empty}", (value, did))
            counts["objects" if kind == "object" else "views"] += 1
    conn.commit()
    return counts


def store_label_embeddings(conn: sqlite3.Connection, rows: list[tuple[str, str, Any]], scale: float, bias: float) -> None:
    """Text embeddings of the vocabulary plus SigLIP's logit scale and bias,
    so labels can be scored locally (and re-scored for a new vocabulary)."""
    import numpy as np

    conn.executemany(
        "INSERT OR REPLACE INTO label_embedding VALUES (?,?,?)",
        [(lid, kind, np.asarray(v, dtype=np.float16).tobytes()) for lid, kind, v in rows],
    )
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('siglip_scale', ?)", (str(float(scale)),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('siglip_bias', ?)", (str(float(bias)),))
    conn.commit()


def score_labels(conn: sqlite3.Connection, chunk: int = 5000, log: Any = print) -> int:
    """Re-score every SigLIP image embedding against the stored label
    embeddings with a softmax per kind. SigLIP's sigmoid saturates for
    pictograms (every image matches "a pictogram of ..."), so relative
    softmax probabilities are the usable confidence."""
    import numpy as np

    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('siglip_scale', 'siglip_bias')").fetchall())
    scale, bias = float(meta.get("siglip_scale", 1.0)), float(meta.get("siglip_bias", 0.0))
    kinds: dict[str, tuple[list[str], Any]] = {}
    for kind in ("object", "view", "feature"):
        rows = conn.execute("SELECT label_id, vec FROM label_embedding WHERE kind=? ORDER BY label_id", (kind,)).fetchall()
        if rows:
            m = np.stack([np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows])
            m /= np.linalg.norm(m, axis=1, keepdims=True).clip(1e-6)
            kinds[kind] = ([r[0] for r in rows], m)
    if not kinds:
        return 0
    n, last = 0, 0
    while True:
        rows = conn.execute(
            "SELECT pictogram_id, vec FROM embedding WHERE model='siglip' AND pictogram_id > ? ORDER BY pictogram_id LIMIT ?", (last, chunk)
        ).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        ids = [r[0] for r in rows]
        x = np.stack([np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows])
        x /= np.linalg.norm(x, axis=1, keepdims=True).clip(1e-6)
        out = []
        for kind, (label_ids, m) in kinds.items():
            logits = x @ m.T * scale + bias
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            top = np.argsort(-p, axis=1)[:, : (TOP_OBJECTS if kind != "view" else 1)]
            for i, pid in enumerate(ids):
                out += [(pid, kind, label_ids[j], float(p[i, j]), r + 1) for r, j in enumerate(top[i])]
        conn.executemany("DELETE FROM vision_label WHERE pictogram_id=?", [(pid,) for pid in ids])
        conn.executemany("INSERT INTO vision_label VALUES (?,?,?,?,?)", out)
        conn.commit()
        n += len(ids)
        log(f"  scored {n}")
    return n


PROBE_SCALE = 8.0  # temperature: unit vectors -> logits with useful spread


def _probe_features(conn: sqlite3.Connection, pictogram_ids: list[int]) -> tuple[list[int], Any]:
    """Concatenated unit SigLIP + DINOv2 embeddings for pictograms that have both."""
    import numpy as np

    ids, rows = [], []
    for pid in pictogram_ids:
        e = dict(conn.execute("SELECT model, vec FROM embedding WHERE pictogram_id=?", (pid,)).fetchall())
        if "siglip" in e and "dinov2" in e:
            s = np.frombuffer(e["siglip"], np.float16).astype(np.float32)
            d = np.frombuffer(e["dinov2"], np.float16).astype(np.float32)
            rows.append(np.concatenate([s / (np.linalg.norm(s) or 1), d / (np.linalg.norm(d) or 1)]) * PROBE_SCALE)
            ids.append(pid)
    return ids, (np.stack(rows) if rows else np.zeros((0, 1152), np.float32))


def _softmax_regression(x: Any, y: Any, k: int, epochs: int = 300, lr: float = 0.5, l2: float = 1e-4) -> tuple[Any, Any]:
    import numpy as np

    w = np.zeros((x.shape[1], k), np.float32)
    b = np.zeros(k, np.float32)
    onehot = np.eye(k, dtype=np.float32)[y]
    for _ in range(epochs):
        z = x @ w + b
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        g = (p - onehot) / len(y)
        w -= lr * (x.T @ g + l2 * w)
        b -= lr * g.sum(axis=0)
    return w, b


def _proba(x: Any, w: Any, b: Any) -> Any:
    import numpy as np

    z = x @ w + b
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


def train_probe(conn: sqlite3.Connection, out: Any, min_examples: int = 8, cv: bool = False, epochs: int = 300) -> dict[str, Any]:
    """Linear probe on the embeddings of Claude-assessed depictions (method
    'ai'). Zero-shot labels do not fit pictograms (34-51 % agreement); a
    probe trained on the assessed sample reached 94 % at confidence >= 0.7."""
    import collections

    import numpy as np

    rows = conn.execute(
        """SELECT d.object_id, g.representative_id FROM depiction d JOIN style_group g ON g.depiction_id = d.id
           WHERE d.method = 'ai' AND d.object_id IS NOT NULL"""
    ).fetchall()
    ids, x = _probe_features(conn, [r[1] for r in rows])
    label = {r[1]: r[0] for r in rows}
    y_raw = [label[i] for i in ids]
    counts = collections.Counter(y_raw)
    keep = [i for i, o in enumerate(y_raw) if counts[o] >= min_examples]
    classes = sorted({y_raw[i] for i in keep})
    if len(classes) < 2:
        raise ValueError("fewer than two objects with enough examples to train on")
    index = {o: k for k, o in enumerate(classes)}
    x, y = x[keep], np.array([index[y_raw[i]] for i in keep])
    stats: dict[str, Any] = {"examples": len(y), "classes": len(classes)}
    if cv:
        folds = np.random.default_rng(0).integers(0, 5, len(y))
        correct = np.zeros(len(y), bool)
        for f in range(5):
            tr, te = folds != f, folds == f
            if te.any() and len(set(y[tr])) == len(classes):
                w, b = _softmax_regression(x[tr], y[tr], len(classes), epochs)
                correct[te] = _proba(x[te], w, b).argmax(axis=1) == y[te]
        stats["cv_accuracy"] = round(float(correct.mean()), 3)
    w, b = _softmax_regression(x, y, len(classes), epochs)
    np.savez_compressed(out, w=w, b=b, classes=np.array(classes))
    return stats


def predict_probe(conn: sqlite3.Connection, model: Any, min_conf: float = 0.9) -> dict[str, int]:
    """Set the object of depictions that have none, when the probe is confident.
    Such depictions get method 'probe' (kept by rule rebuilds, visible as
    their own method in reviews and quality measurement)."""
    import numpy as np

    m = np.load(model, allow_pickle=False)
    w, b, classes = m["w"], m["b"], [str(c) for c in m["classes"]]
    trained = datetime.fromtimestamp(Path(model).stat().st_mtime, UTC).isoformat(timespec="seconds")
    model_name = f"{provenance.PROBE_MODEL} ({len(classes)} classes)"
    run = f"{Path(model).name} trained {trained}"
    rows = conn.execute(
        # SQLite takes the bare columns from the row holding MAX(g.size)
        """SELECT d.id, g.representative_id, MAX(g.size) FROM depiction d JOIN style_group g ON g.depiction_id = d.id
           WHERE d.object_id IS NULL GROUP BY d.id"""
    ).fetchall()
    counts = {"candidates": len(rows), "set": 0}
    for start in range(0, len(rows), 5000):
        part = rows[start : start + 5000]
        ids, x = _probe_features(conn, [r[1] for r in part])
        if not ids:
            continue
        p = _proba(x, w, b)
        dep = {r[1]: r[0] for r in part}
        logged = []
        for i, pid in enumerate(ids):
            k = int(p[i].argmax())
            applied = bool(p[i, k] >= min_conf)
            if applied:
                conn.execute(
                    "UPDATE depiction SET object_id=?, method='probe', description=? WHERE id=? AND object_id IS NULL",
                    (classes[k], f"probe p={p[i, k]:.2f}", dep[pid]),
                )
                counts["set"] += 1
            top = [(classes[j], round(float(p[i, j]), 3)) for j in np.argsort(-p[i])[:3]]
            logged.append(
                dict(
                    pictogram_id=pid,
                    subject="depiction",
                    subject_id=dep[pid],
                    field="object",
                    value=classes[k],
                    confidence=round(float(p[i, k]), 4),
                    method="probe",
                    model=model_name,
                    input="image",
                    run=run,
                    context={"applied": applied, "min_conf": min_conf, "top3": top},
                )
            )
        provenance.record(conn, logged)
        conn.commit()
    return counts
