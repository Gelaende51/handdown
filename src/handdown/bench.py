"""Vision benchmark: free models name the object of Claude-assessed pictograms
(docs/superpowers/specs/2026-09-30-vision-benchmark-design.md).

Runners re-harvest the sample's sources, process only the sample, ask one
model and write answers; ``score`` compares them with Claude's objects."""

from __future__ import annotations

import base64
import io
import json
import random
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db

# Runners re-harvest whole sources; fonts and Commons are too slow for that.
ADAPTERS = ("iconify", "npm-svg", "git-svg")
PROMPT = (
    "This is a monochrome pictogram (black on white). Name the single object or symbol it depicts "
    "in 1 to 3 English words, e.g. 'coffee cup', 'arrow', 'letter A'. Answer with the name only."
)
BATCH_PROMPT = (
    "These are {n} monochrome pictograms (black on white), numbered in order. For each, name the single "
    "object or symbol it depicts in 1 to 3 English words, e.g. 'coffee cup', 'arrow', 'letter A'. "
    "Answer with exactly one line per pictogram: '<number>: <name>'."
)

Ask = Callable[[list[Any]], list[str]]


def make_sample(
    conn: sqlite3.Connection, n: int = 500, per_source: int = 4, seed: int = 0, adapters: tuple[str, ...] = ADAPTERS
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Claude-assessed depictions spread over shuffled sources, and the job list
    of those sources. The pictogram is the depiction's largest style group's
    representative."""
    from .shard import SOURCE_COLS

    rows = conn.execute(
        # SQLite takes the bare columns from the row holding MAX(g.size)
        f"""SELECT d.id, p.source_id, p.original_id, MAX(g.size) FROM depiction d
            JOIN style_group g ON g.depiction_id = d.id JOIN pictogram p ON p.id = g.representative_id
            JOIN source s ON s.id = p.source_id
            WHERE d.method = 'ai' AND d.object_id IS NOT NULL AND s.adapter IN ({",".join("?" * len(adapters))})
            GROUP BY d.id ORDER BY d.id""",
        adapters,
    ).fetchall()
    by_source: dict[str, list[dict[str, Any]]] = {}
    for did, sid, oid, _ in rows:
        by_source.setdefault(sid, []).append({"key": did, "source_id": sid, "original_id": oid})
    rng = random.Random(seed)
    order = sorted(by_source)
    rng.shuffle(order)
    sample: list[dict[str, Any]] = []
    for sid in order:
        if len(sample) >= n:
            break
        items = by_source[sid]
        sample += rng.sample(items, min(per_source, len(items), n - len(sample)))
    ids = sorted({r["source_id"] for r in sample})
    marks = ",".join("?" * len(ids))
    sources = [dict(r) for r in conn.execute(f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE id IN ({marks}) ORDER BY id", ids)]
    return sample, sources


def _sample_table(conn: sqlite3.Connection, sample: list[dict[str, Any]]) -> None:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS bench_sample (key INTEGER, source_id TEXT, original_id TEXT)")
    conn.execute("DELETE FROM bench_sample")
    conn.executemany("INSERT INTO bench_sample VALUES (?,?,?)", [(r["key"], r["source_id"], r["original_id"]) for r in sample])


def prepare(conn: sqlite3.Connection, sample: list[dict[str, Any]]) -> int:
    """Mark every pictogram outside the sample as measured, so ``process`` only
    normalizes the sample. Returns how many were skipped."""
    _sample_table(conn, sample)
    n = conn.execute(
        """UPDATE pictogram SET measured_at = ? WHERE measured_at IS NULL AND NOT EXISTS
           (SELECT 1 FROM bench_sample b WHERE b.source_id = pictogram.source_id AND b.original_id = pictogram.original_id)""",
        (db.now(),),
    ).rowcount
    conn.commit()
    return n


def run(
    conn: sqlite3.Connection, cfg: Any, sample: list[dict[str, Any]], ask: Ask, model: str, out: Path, batch: int = 1, size: int = 256, log: Any = print
) -> dict[str, int]:
    """Render the sample and write one answer line per key to ``out``; keys
    already in ``out`` are skipped, so a rerun resumes."""
    from .vision import _images

    out = Path(out)
    done = {json.loads(line)["key"] for line in out.read_text().splitlines()} if out.exists() else set()
    _sample_table(conn, sample)
    rows = conn.execute(
        """SELECT b.key, p.id, p.norm_path FROM bench_sample b
           LEFT JOIN pictogram p ON p.source_id = b.source_id AND p.original_id = b.original_id ORDER BY b.key"""
    ).fetchall()
    todo = [r for r in rows if r["key"] not in done]
    stats = {"answered": 0, "missing": 0}
    with out.open("a") as f:
        images, keys = [], []
        present = [r for r in todo if r["id"] is not None]
        rendered, ids = _images(cfg, present, size) if present else ([], [])
        key_of = {r["id"]: r["key"] for r in present}
        for r in todo:
            if r["id"] not in ids:
                f.write(json.dumps({"key": r["key"], "model": model, "error": "missing"}) + "\n")
                stats["missing"] += 1
        f.flush()
        pairs = [(key_of[pid], img) for pid, img in zip(ids, rendered, strict=True)]
        for start in range(0, len(pairs), batch):
            part = pairs[start : start + batch]
            keys, images = [k for k, _ in part], [img for _, img in part]
            t = time.monotonic()
            answers = ask(images)
            seconds = round((time.monotonic() - t) / len(part), 2)
            for k, a in zip(keys, (answers + [""] * len(keys))[: len(keys)], strict=True):
                f.write(json.dumps({"key": k, "model": model, "answer": a, "seconds": seconds}) + "\n")
                stats["answered"] += 1
            f.flush()
            log(f"{model}: {start + len(part)}/{len(pairs)}")
    return stats


def clean(text: str) -> str:
    """Lower-case name without markdown, thinking, articles or trailing punctuation."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"[*_`\"']", "", text).strip().rstrip(".!").strip().lower()
    return re.sub(r"^(an?|the)\s+", "", text)


def numbered(text: str, n: int) -> list[str]:
    """Answers to a batch prompt, by number; missing numbers give ''."""
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.)-]\s*(.*)", line)
        if m:
            found[int(m.group(1))] = clean(m.group(2))
    return [found.get(i + 1, "") for i in range(n)]


def _png(image: Any) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Ollama:
    """A model served by Ollama on the runner, one image per request."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434"):
        import httpx

        self.model, self.client = model, httpx.Client(base_url=host, timeout=900)

    def __call__(self, images: list[Any]) -> list[str]:
        out = []
        for image in images:
            r = self.client.post(
                "/api/generate",
                json={"model": self.model, "prompt": PROMPT, "images": [_png(image)], "stream": False, "options": {"temperature": 0, "num_predict": 32}},
            )
            r.raise_for_status()
            out.append(clean(r.json()["response"]))
        return out


class GitHubModels:
    """GitHub Models with the workflow token; batches, because the free tier
    counts requests, not images."""

    URL = "https://models.github.ai/inference/chat/completions"

    def __init__(self, model: str, token: str, retries: int = 8):
        import httpx

        self.model, self.retries = model, retries
        self.client = httpx.Client(timeout=300, headers={"Authorization": f"Bearer {token}"})

    def __call__(self, images: list[Any]) -> list[str]:
        content: list[dict[str, Any]] = [{"type": "text", "text": BATCH_PROMPT.format(n=len(images))}]
        content += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png(i)}", "detail": "low"}} for i in images]
        body = {"model": self.model, "messages": [{"role": "user", "content": content}], "temperature": 0}
        for _ in range(self.retries):
            r = self.client.post(self.URL, json=body)
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", "60"))
                if wait > 3600:
                    raise RuntimeError(f"GitHub Models daily limit reached (retry after {wait} s)")
                time.sleep(wait + 1)
                continue
            r.raise_for_status()
            return numbered(r.json()["choices"][0]["message"]["content"], len(images))
        raise RuntimeError("GitHub Models kept answering 429")


class OmniParserCaption:
    """OmniParser v2's icon caption model (Florence-2 fine-tuned on UI icons),
    loaded the way OmniParser loads it."""

    def __init__(self, repo: str = "microsoft/OmniParser-v2.0", processor: str = "microsoft/Florence-2-base"):
        from unittest.mock import patch

        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForCausalLM, AutoProcessor
        from transformers.dynamic_module_utils import get_imports

        def no_flash_attn(path: Any) -> list[str]:  # CPU: Florence's remote code imports flash_attn optionally
            return [i for i in get_imports(path) if i != "flash_attn"]

        local = Path(snapshot_download(repo, allow_patterns=["icon_caption/*"])) / "icon_caption"
        with patch("transformers.dynamic_module_utils.get_imports", no_flash_attn):
            self.processor = AutoProcessor.from_pretrained(processor, trust_remote_code=True)
            self.model = AutoModelForCausalLM.from_pretrained(local, torch_dtype=torch.float32, trust_remote_code=True).eval()
        self.torch = torch

    def __call__(self, images: list[Any]) -> list[str]:
        inputs = self.processor(images=images, text=["<CAPTION>"] * len(images), return_tensors="pt", do_resize=False)
        with self.torch.no_grad():
            ids = self.model.generate(input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"], max_new_tokens=20, num_beams=1, do_sample=False)
        return [clean(t) for t in self.processor.batch_decode(ids, skip_special_tokens=True)]


def backend(spec: str) -> tuple[Ask, int, int]:
    """(ask, batch, render size) for 'ollama:<tag>', 'github:<model>' or 'florence:omniparser'."""
    import os

    kind, _, name = spec.partition(":")
    if kind == "ollama":
        return Ollama(name), 1, 256
    if kind == "github":
        return GitHubModels(name, os.environ["GITHUB_TOKEN"]), 10, 256
    if kind == "florence" and name == "omniparser":
        return OmniParserCaption(), 8, 64  # OmniParser captions 64 px crops
    raise ValueError(f"unknown model spec {spec!r}")


def _synsets(phrase: str) -> list[Any]:
    from .concepts import wordnet

    wn = wordnet()
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    if not words:
        return []
    found = list(wn.synsets("_".join(words)))
    if not found:
        found = list(wn.synsets(words[-1], pos=wn.NOUN)) or list(wn.synsets(words[-1]))
    return found


def match(answer: str, gold: str) -> str:
    """'exact' when Claude's synset is among the answer's; 'near' within WordNet
    path distance 4 under a specific shared parent (depth >= 6: mug/cup under
    container, thumb/hand under extremity, not house/key under artifact); else
    'miss'. Loose on purpose (dog/cat is near): read the misses too."""
    from nltk.corpus.reader.wordnet import WordNetError

    from .concepts import wordnet

    if gold.startswith("term:"):  # Claude's objects outside WordNet: compare the text

        def norm(t: str) -> str:
            return re.sub(r"[^a-z0-9]", "", t.lower()).rstrip("s")

        words = answer.split()
        return "exact" if norm(gold[5:]) in {norm(answer), norm(words[-1]) if words else ""} - {""} else "miss"
    candidates = _synsets(answer)
    if not candidates or not gold.startswith("wn:"):
        return "miss"
    try:
        target = wordnet().synset(gold[3:])
    except WordNetError:  # a gold id that WordNet does not know is a miss, not a crash
        return "miss"
    head = _synsets(answer.split()[-1]) if len(answer.split()) > 1 else []
    if target in candidates or target in head:
        return "exact"
    for s in candidates + head:
        d = s.shortest_path_distance(target)
        if d is not None and d <= 4 and any(h.max_depth() >= 6 for h in s.lowest_common_hypernyms(target)):
            return "near"
    return "miss"


def score(conn: sqlite3.Connection, results: list[dict[str, Any]], misses: int = 15) -> dict[str, dict[str, Any]]:
    """Per model: answered count, exact and near shares, seconds per image and
    a few misses (Claude's object, answer) to read."""
    gold = {r[0]: r[1] for r in conn.execute("SELECT id, object_id FROM depiction WHERE method='ai' AND object_id IS NOT NULL")}
    report: dict[str, dict[str, Any]] = {}
    for r in results:
        m = report.setdefault(r["model"], {"n": 0, "exact": 0, "near": 0, "seconds": 0.0, "misses": []})
        if "answer" not in r or r["key"] not in gold:
            continue
        level = match(r["answer"], gold[r["key"]])
        m["n"] += 1
        m["seconds"] += r.get("seconds", 0.0)
        if level == "miss":
            if len(m["misses"]) < misses:
                m["misses"].append((gold[r["key"]], r["answer"]))
        else:
            m[level] += 1
    for m in report.values():
        n = m["n"] or 1
        m["exact"], m["near"], m["seconds"] = round(m["exact"] / n, 3), round(m["near"] / n, 3), round(m["seconds"] / n, 2)
    return report
