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
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db, provenance

# Runners re-harvest whole sources; fonts and Commons are too slow for that.
ADAPTERS = ("iconify", "npm-svg", "git-svg")
PROMPT = (
    "This is a monochrome pictogram (black on white). Name the single object or symbol it depicts "
    "in 1 to 3 English words, e.g. 'coffee cup', 'arrow', 'letter A'. Answer with the name only."
)

Ask = Callable[[list[Any]], list[Any]]  # answers, or (answer, extra fields)


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
    conn: sqlite3.Connection,
    cfg: Any,
    sample: list[dict[str, Any]],
    ask: Ask,
    model: str,
    out: Path,
    batch: int = 1,
    size: int = 256,
    limit: int | None = None,
    variants: tuple[str, ...] = ("norm",),
    log: Any = print,
) -> dict[str, int]:
    """Render the sample and write one answer line per key and version to
    ``out``; lines already in ``out`` are skipped, so a rerun resumes. Versions:
    ``norm`` (the catalog's black-and-white pictogram) and ``original`` (the
    harvested raster image in colour, raster pictograms only; its lines carry
    ``"variant": "original"``). ``limit`` takes the first keys, so variants of a
    speed test answer the same images. An answer may come with extra fields
    (timings) as ``(text, dict)``."""
    from .raster import render_rgb
    from .vision import _images

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["key"], r.get("variant", "norm")))
    _sample_table(conn, sample)
    rows = conn.execute(
        """SELECT b.key, p.id, p.norm_path, p.format, r.svg AS raw FROM bench_sample b
           LEFT JOIN pictogram p ON p.source_id = b.source_id AND p.original_id = b.original_id
           LEFT JOIN raw_svg r ON r.pictogram_id = p.id AND p.format = 'raster' ORDER BY b.key"""
    ).fetchall()[:limit]
    # depiction ids change when the hierarchy is rebuilt: answers carry the pictogram they were asked about
    who = {r["key"]: {"source_id": r["source_id"], "original_id": r["original_id"]} for r in sample if "source_id" in r}
    stats = {"answered": 0, "missing": 0}
    with out.open("a") as f:
        for variant in variants:
            mark = {"variant": variant} if variant != "norm" else {}
            if variant == "norm":
                todo = [r for r in rows if (r["key"], "norm") not in done]
                present = [r for r in todo if r["id"] is not None]
                rendered, ids = _images(cfg, present, size) if present else ([], [])
                key_of = {r["id"]: r["key"] for r in present}
                for r in todo:
                    if r["id"] not in ids:
                        f.write(json.dumps({"key": r["key"], **who.get(r["key"], {}), "model": model, "error": "missing"}) + "\n")
                        stats["missing"] += 1
                pairs = [(key_of[pid], img) for pid, img in zip(ids, rendered, strict=True)]
            else:  # the raster original, in colour
                pairs = [(r["key"], render_rgb(r["raw"], size)) for r in rows if r["raw"] and (r["key"], variant) not in done]
            f.flush()
            for start in range(0, len(pairs), batch):
                part = pairs[start : start + batch]
                keys, images = [k for k, _ in part], [img for _, img in part]
                t = time.monotonic()
                answers = ask(images)
                seconds = round((time.monotonic() - t) / len(part), 2)
                for k, a in zip(keys, (list(answers) + [""] * len(keys))[: len(keys)], strict=True):
                    text, extra = a if isinstance(a, tuple) else (a, {})
                    f.write(json.dumps({"key": k, **who.get(k, {}), "model": model, "answer": text, "seconds": seconds, **mark, **extra}) + "\n")
                    stats["answered"] += 1
                f.flush()
                log(f"{model} {variant}: {start + len(part)}/{len(pairs)}")
    return stats


def clean(text: str) -> str:
    """Lower-case name without markdown, thinking, articles or trailing punctuation."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"[*_`\"']", "", text).strip().rstrip(".!").strip().lower()
    return re.sub(r"^(an?|the)\s+", "", text)


def _png(image: Any) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Ollama:
    """A model served by Ollama on the runner, one image per request. Answers
    carry Ollama's timings, so a speed test shows where the time goes."""

    def __init__(self, model: str, threads: int | None = None, host: str = "http://127.0.0.1:11434"):
        import httpx

        self.model, self.client = model, httpx.Client(base_url=host, timeout=900)
        self.options: dict[str, Any] = {"temperature": 0, "num_predict": 32}
        if threads:
            self.options["num_thread"] = threads

    def __call__(self, images: list[Any]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for image in images:
            body = {"model": self.model, "prompt": PROMPT, "images": [_png(image)], "stream": False, "options": self.options}
            r = self.client.post("/api/generate", json=body)
            r.raise_for_status()
            j = r.json()
            timings = {k: round(j.get(f"{k}_duration", 0) / 1e9, 2) for k in ("load", "prompt_eval", "eval")}
            out.append((clean(j["response"]), {**timings, "prompt_tokens": j.get("prompt_eval_count"), "answer_tokens": j.get("eval_count")}))
        return out


GATEWAY = "https://ai-gateway.vercel.sh/v1"
# OpenAI-compatible hosted APIs: spec prefix -> (base URL, environment variable with the key)
# hermes: Hermes Agent's subscription proxy (`hermes proxy start`) on the host, which attaches the
# Nous Portal login itself (free plan included), so no key here
HOSTED: dict[str, tuple[str, str | None]] = {
    "gateway": (GATEWAY, "AI_GATEWAY_API_KEY"),
    "nous": ("https://inference-api.nousresearch.com/v1", "NOUS_API_KEY"),
    "hermes": ("http://host.containers.internal:8645/v1", None),
    # OpenCode Zen: free models also without a login, with the shared key "public" (what opencode itself sends)
    "zen": ("https://opencode.ai/zen/v1", "OPENCODE_API_KEY"),
}
ANONYMOUS_KEYS = {"zen": "public"}


def hosted_client(provider: str, timeout: float = 120) -> Any:
    """HTTP client for a ``HOSTED`` provider; HERMES_PROXY_URL moves the proxy."""
    import os

    import httpx

    base, key = HOSTED[provider]
    if provider == "hermes":
        base = os.environ.get("HERMES_PROXY_URL", base)
        # a proxy on the host (or localhost) is not reached through the container's HTTP proxy
        return httpx.Client(base_url=base, timeout=timeout, trust_env=False)
    token = os.environ.get(key or "") or ANONYMOUS_KEYS.get(provider, "")
    return httpx.Client(base_url=base, timeout=timeout, headers={"Authorization": f"Bearer {token}"})


class CreditExhausted(RuntimeError):
    """The gateway refuses for lack of credit (HTTP 402): stop instead of writing errors."""


class Gateway:
    """A hosted model through an OpenAI-compatible API (``HOSTED``: Vercel's AI
    Gateway, Nous Portal), one image per request. Unlike the runner's own
    models this sends the rendered pictogram to the model's provider."""

    def __init__(
        self,
        model: str,
        answer_tokens: int = 64,
        client: Any = None,
        wait: Callable[[float], None] = time.sleep,
        provider: str = "gateway",
        think: bool = True,
    ):
        import os

        self.model, self.answer_tokens, self.wait = model, answer_tokens, wait
        # think=off asks for no reasoning (OpenRouter-style field, which Nous and the gateway pass on);
        # reasoning models otherwise spend the answer budget thinking and return nothing
        self.reasoning: dict[str, Any] | None = None if think else {"enabled": False}
        key = HOSTED[provider][1]
        if key and not client and not os.environ.get(key) and provider not in ANONYMOUS_KEYS:
            raise KeyError(f"{key} is not set")
        self.client = client or hosted_client(provider)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(6):
            r = self.client.post("/chat/completions", json=body)
            if r.status_code == 402:
                raise CreditExhausted(r.text[:300])
            if r.status_code == 400 and "reasoning" in body:  # the model takes no reasoning switch: ask without it
                self.reasoning = None
                body = {k: v for k, v in body.items() if k != "reasoning"}
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                self.wait(float(r.headers.get("retry-after") or 2 ** (attempt + 2)))
                continue
            r.raise_for_status()
            return r.json()
        raise AssertionError("unreachable")

    def __call__(self, images: list[Any]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for image in images:
            content = [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png(image)}"}}]
            body = {"model": self.model, "messages": [{"role": "user", "content": content}], "max_tokens": self.answer_tokens, "temperature": 0}
            if self.reasoning:
                body["reasoning"] = self.reasoning
            j = self._post(body)
            usage = j.get("usage") or {}
            extra = {"prompt_tokens": usage.get("prompt_tokens"), "answer_tokens": usage.get("completion_tokens")}
            if "cost" in usage:
                extra["cost_usd"] = usage["cost"]
            out.append((clean(j["choices"][0]["message"].get("content") or ""), extra))
        return out


def hosted_models(client: Any = None, provider: str = "gateway") -> list[dict[str, Any]]:
    """A hosted provider's models with type, tags and price per token. Free ones
    cost 0 for input and output or carry the ``:free`` tag (Nous Portal);
    vision ones are tagged so or take images as input."""
    client = client or hosted_client(provider, timeout=60)
    r = client.get("/models")
    r.raise_for_status()
    rows = []
    for m in r.json().get("data", []):
        price = m.get("pricing") or {}
        tags = list(m.get("tags") or [])
        if "image" in ((m.get("architecture") or {}).get("input_modalities") or []) and "vision" not in tags:
            tags.append("vision")
        cost_in, cost_out = price.get("input", price.get("prompt")), price.get("output", price.get("completion"))
        rows.append(
            {
                "id": m["id"],
                "type": m.get("type"),
                "tags": tags,
                "input": cost_in,
                "output": cost_out,
                "image": price.get("image") or price.get("input_image"),
                # media models priced per image or second list no token price: not free
                "free": m["id"].endswith(":free")
                or "free" in tags
                or (m.get("type") in (None, "language") and bool(price) and all(float(v or 0) == 0 for v in (cost_in, cost_out))),
                "context": m.get("context_window"),
            }
        )
    return rows


class LlamaServer:
    """A GGUF vision model served by llama.cpp's ``llama-server`` (started on the
    first call), with the image capped at ``tokens`` tokens: Ollama scales every
    image to ~1,000 tokens, which is the whole cost on a CPU runner."""

    def __init__(self, repo: str, tokens: int | None = None, threads: int | None = None, port: int = 8081, binary: str = "llama-server"):
        import httpx

        self.repo, self.tokens, self.threads, self.port, self.binary = repo, tokens, threads, port, binary
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=900)
        self.started = False

    def command(self) -> list[str]:
        args = [self.binary, "-hf", self.repo, "--port", str(self.port), "-c", "4096"]
        if self.tokens:
            args += ["--image-min-tokens", str(self.tokens), "--image-max-tokens", str(self.tokens)]
        if self.threads:
            args += ["-t", str(self.threads)]
        return args

    def _start(self, wait: int = 3600) -> None:
        import atexit
        import subprocess

        import httpx

        proc = subprocess.Popen(self.command())
        atexit.register(proc.terminate)
        deadline = time.monotonic() + wait  # the first start downloads the model
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {proc.returncode}")
            try:
                if self.client.get("/health").status_code == 200:
                    self.started = True
                    return
            except httpx.TransportError:  # not listening yet
                pass
            time.sleep(5)
        raise RuntimeError("llama-server did not become ready")

    def __call__(self, images: list[Any]) -> list[tuple[str, dict[str, Any]]]:
        if not self.started:
            self._start()
        out = []
        for image in images:
            content = [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png(image)}"}}]
            r = self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 32})
            r.raise_for_status()
            j = r.json()
            t = j.get("timings", {})
            extra = {"prompt_eval": round(t.get("prompt_ms", 0) / 1000, 2), "eval": round(t.get("predicted_ms", 0) / 1000, 2)}
            out.append((clean(j["choices"][0]["message"]["content"]), {**extra, "prompt_tokens": j.get("usage", {}).get("prompt_tokens")}))
        return out


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
    """(ask, batch, render size) for 'ollama:<tag>', 'llamacpp:<hf repo>[:quant]',
    'gateway:<provider>/<model>' (Vercel AI Gateway), 'nous:<model>' (Nous Portal
    API key), 'hermes:<model>' (Nous Portal through Hermes Agent's proxy),
    'zen:<model>' (OpenCode Zen) or
    'florence:omniparser';
    options after '@': size (render px), threads, tokens (llama.cpp image
    tokens), answer (hosted answer tokens), think=off (hosted, no reasoning),
    e.g. '@size=128,threads=4,tokens=64'."""
    kind, _, rest = spec.partition(":")
    name, _, opts = rest.partition("@")
    options = dict(kv.split("=", 1) for kv in opts.split(",") if kv)
    if kind == "ollama":
        threads = int(options["threads"]) if "threads" in options else None
        return Ollama(name, threads=threads), 1, int(options.get("size", 256))
    if kind == "llamacpp":
        tokens = int(options["tokens"]) if "tokens" in options else None
        threads = int(options["threads"]) if "threads" in options else None
        return LlamaServer(name, tokens=tokens, threads=threads), 1, int(options.get("size", 256))
    if kind in HOSTED:
        think = options.get("think", "on") != "off"
        return Gateway(name, answer_tokens=int(options.get("answer", 64)), provider=kind, think=think), 1, int(options.get("size", 256))
    if kind == "florence" and name == "omniparser":
        return OmniParserCaption(), 8, 64  # OmniParser captions 64 px crops
    raise ValueError(f"unknown model spec {spec!r}")


# Words models add around the object name ("folder icon", "outline of a house")
FILLER = {"icon", "icons", "symbol", "sign", "shape", "outline", "filled", "solid", "black", "white", "simple", "pictogram", "glyph", "emoji", "of"}


def _synsets(phrase: str) -> list[Any]:
    """Senses of the whole phrase, then of each noun in it ("cloud upload" ->
    cloud and upload): models name the object anywhere in a short phrase."""
    from .concepts import wordnet

    wn = wordnet()
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in FILLER]
    if not words:
        return []
    found = list(wn.synsets("_".join(words)))
    for w in words:
        found += [s for s in wn.synsets(w, pos=wn.NOUN) if s not in found]
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
    if target in candidates:
        return "exact"
    for s in candidates:
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


# Text benchmark: names of a depiction's pictograms -> which candidate object is drawn.
TEXT_QUESTION = "These are the names of one pictogram in several icon sets. Which object is drawn in it?"


def _gloss(s: Any, words: int = 10) -> str:
    return f"{s.lemma_names()[0].replace('_', ' ')}: {' '.join(s.definition().split()[:words])}"


def text_items(conn: sqlite3.Connection, n: int = 2000, seed: int = 0, max_options: int = 8, max_names: int = 20) -> list[dict[str, Any]]:
    """Claude-assessed depictions as text: member names as the state, candidate
    objects from the names' object words (the rules' pick first, then per word
    the rules' sense and the two most frequent noun senses) and the rules' own pick for comparison.
    Depictions whose names give no candidate are left out."""
    from .concepts import resolve, wordnet
    from .hierarchy.names import name_roles

    wn = wordnet()
    keys = [r[0] for r in conn.execute("SELECT id FROM depiction WHERE method='ai' AND object_id LIKE 'wn:%' ORDER BY id")]
    random.Random(seed).shuffle(keys)
    items: list[dict[str, Any]] = []
    for did in keys:
        if len(items) >= n:
            break
        names = [
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT p.original_name FROM style_group g JOIN style_member m ON m.style_group_id = g.id
                   JOIN pictogram p ON p.id = m.pictogram_id WHERE g.depiction_id = ? AND p.original_name IS NOT NULL
                   ORDER BY p.id LIMIT ?""",
                (did, max_names),
            )
        ]
        roles = [name_roles(nm) for nm in names]
        phrase = Counter(" ".join(r.object_tokens) for r in roles if r.object_tokens).most_common(1)
        rules = resolve(phrase[0][0].split()) if phrase else None
        options: dict[str, str] = {}
        if rules and rules.id.startswith("wn:"):  # the rules' pick is always a candidate
            options[rules.id] = _gloss(wn.synset(rules.id[3:]))
        for token, _ in Counter(t for r in roles for t in r.object_tokens).most_common():
            ruled = resolve([token])
            senses = ([wn.synset(ruled.id[3:])] if ruled and ruled.id.startswith("wn:") else []) + list(wn.synsets(token, pos=wn.NOUN)[:2])
            for s in senses:
                options.setdefault(f"wn:{s.name()}", _gloss(s))
        options = dict(list(options.items())[:max_options])
        if not options:
            continue
        state = "; ".join(" ".join(re.findall(r"[a-z0-9]+", nm.lower())) for nm in names)
        items.append({"key": did, "state": state, "options": options, "rules": rules.id if rules else None})
    return items


def run_text(items: list[dict[str, Any]], predict: Callable[[list[dict[str, Any]]], list[tuple[str, float]]], model: str, out: Path, batch: int = 32) -> int:
    """Write {key, model, answer, p} per item; keys already in ``out`` are skipped."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["key"] for line in out.read_text().splitlines()} if out.exists() else set()
    todo = [i for i in items if i["key"] not in done]
    with out.open("a") as f:
        for start in range(0, len(todo), batch):
            part = todo[start : start + batch]
            for item, (answer, p) in zip(part, predict(part), strict=True):
                f.write(json.dumps({"key": item["key"], "model": model, "answer": answer, "p": round(p, 4)}) + "\n")
            f.flush()
    return len(todo)


def score_text(conn: sqlite3.Connection, items: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per model (and the name rules): share matching Claude's object, the
    share answerable at all (Claude's object among the candidates) and the
    share correct among answers with p >= 0.9."""
    gold = {r[0]: r[1] for r in conn.execute("SELECT id, object_id FROM depiction WHERE method='ai'")}
    by_key = {i["key"]: i for i in items}
    rows = results + [{"key": i["key"], "model": "rules", "answer": i["rules"], "p": 1.0} for i in items]
    report: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["key"] not in gold or r["key"] not in by_key:
            continue
        m = report.setdefault(r["model"], {"n": 0, "exact": 0, "coverage": 0, "confident": 0, "confident_exact": 0})
        hit = r["answer"] == gold[r["key"]]
        m["n"] += 1
        m["exact"] += hit
        m["coverage"] += gold[r["key"]] in by_key[r["key"]]["options"]
        if r.get("p", 0) >= 0.9:
            m["confident"] += 1
            m["confident_exact"] += hit
    for m in report.values():
        n = m["n"] or 1
        m["exact_at_0.9"] = round(m.pop("confident_exact") / (m["confident"] or 1), 3)
        m["share_at_0.9"] = round(m.pop("confident") / n, 3)
        m["exact"], m["coverage"] = round(m["exact"] / n, 3), round(m["coverage"] / n, 3)
    return report


class LayaChoice:
    """Laya (Convai Innovations, Apache-2.0): one choice question over the
    candidates, in batches; the Router picks the checkpoint."""

    def __init__(self) -> None:
        from laya import Router

        self.router = Router(device="cpu")

    def __call__(self, items: list[dict[str, Any]]) -> list[tuple[str, float]]:
        requests = [
            {
                "state": i["state"],
                "questions": {"object": {"type": "choice", "instructions": TEXT_QUESTION, "criteria": i["options"]}},
                "max_len": 512,
                "head_max_len": 320,  # room for up to 8 glossed candidates
            }
            for i in items
        ]
        out = []
        for res in self.router.predict_batch(requests):
            a = res["answers"]["object"]
            out.append((a["choice"], float(a["probabilities"][a["choice"]])))
        return out


# Long-tail labelling: the benchmark machinery over every depiction without an object.
def vlm_jobs(conn: sqlite3.Connection) -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Per adapter: items (depictions without an object, and every depiction of
    a raster pictogram; by the representative of their largest style group)
    and the job list of their sources."""
    from .shard import SOURCE_COLS

    rows = conn.execute(
        # SQLite takes the bare columns from the row holding MAX(g.size)
        """SELECT d.id, p.source_id, p.original_id, s.adapter, MAX(g.size) FROM depiction d
           JOIN style_group g ON g.depiction_id = d.id JOIN pictogram p ON p.id = g.representative_id
           JOIN source s ON s.id = p.source_id
           -- raster pictograms also with an object: both versions are compared (raster_disagreements)
           WHERE (d.object_id IS NULL OR p.format = 'raster') AND p.topic IS NULL  -- off-topic: reference only (topic.py)
             -- answered before (also when unresolvable: those go to vlm_backup); ids as of the answer
             AND d.id NOT IN (SELECT subject_id FROM classification WHERE method = 'vlm' AND subject = 'depiction')
           GROUP BY d.id ORDER BY d.id"""
    ).fetchall()
    jobs: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for adapter in sorted({r[3] for r in rows}):
        items = [{"key": r[0], "source_id": r[1], "original_id": r[2]} for r in rows if r[3] == adapter]
        ids = sorted({i["source_id"] for i in items})
        marks = ",".join("?" * len(ids))
        sources = [dict(r) for r in conn.execute(f"SELECT {', '.join(SOURCE_COLS)} FROM source WHERE id IN ({marks}) ORDER BY id", ids)]
        jobs[adapter] = (items, sources)
    return jobs


def present_only(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items whose source this database has: a runner harvests only its shard's
    sources and must not report the other shards' items as missing."""
    have = {r[0] for r in conn.execute("SELECT id FROM source")}
    return [i for i in items if i["source_id"] in have]


def current_depiction(conn: sqlite3.Connection, source_id: str, original_id: str) -> int | None:
    """The depiction a pictogram belongs to now (ids change when the hierarchy is rebuilt)."""
    row = conn.execute(
        """SELECT g.depiction_id FROM pictogram p JOIN style_member m ON m.pictogram_id = p.id
           JOIN style_group g ON g.id = m.style_group_id WHERE p.source_id = ? AND p.original_id = ?""",
        (source_id, original_id),
    ).fetchone()
    return row[0] if row else None


def vlm_apply(conn: sqlite3.Connection, answers: list[dict[str, Any]], run: str | None = None, items: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Set the object of depictions that have none from a vision model's answer
    (its head noun, resolved like pictogram names), as method 'vlm'. Every
    answer is logged with its origin, applied or not.

    An answer is applied to the depiction its pictogram belongs to now: the
    pictogram comes from the answer itself or, for answers without it, from
    ``items`` (the job list the run was given, which maps keys to pictograms).
    Answers whose pictogram has no depiction any more are counted as gone."""
    from .concepts import _ensure, resolve
    from .hierarchy.names import object_head

    seen: set[str] = set()
    counts = {"answers": 0, "set": 0, "gone": 0}
    by_key = {i["key"]: i for i in items or []}
    resolved = []
    for a in answers:
        ident = a if "source_id" in a else by_key.get(a["key"])
        if ident is None:
            resolved.append(a)  # nothing to resolve by: the key is taken as it is
            continue
        dep = current_depiction(conn, ident["source_id"], ident["original_id"])
        if dep is None:
            counts["gone"] += 1
            continue
        resolved.append({**a, "key": dep})
    answers = resolved
    # the original (colour) answer of a raster pictogram decides; the black-and-white one is logged beside it
    for a in sorted(answers, key=lambda a: a.get("variant") != "original"):
        model = a.get("model") if a.get("variant") in (None, "norm") else f"{a.get('model')}@variant={a['variant']}"
        if not a.get("answer"):  # logged too, so the backup finds it
            provenance.record(
                conn,
                [
                    dict(
                        pictogram_id=provenance._representative(conn, a["key"]),
                        subject="depiction",
                        subject_id=a["key"],
                        field="object",
                        method="vlm",
                        model=model,
                        input="image",
                        run=a.get("run") or run,
                        context={"applied": False, "error": a.get("error") or "empty"},
                    )
                ],
            )
            continue
        counts["answers"] += 1
        words = " ".join(w for w in re.findall(r"[a-z0-9]+", a["answer"].lower()) if w not in FILLER)
        tokens, _ = object_head(words)
        concept = resolve(tokens) if tokens else None
        value = concept.id if concept is not None and concept.id.startswith("wn:") else None
        applied = 0
        if value:
            _ensure(conn, concept, seen)
            applied = conn.execute(
                "UPDATE depiction SET object_id=?, method='vlm', description=? WHERE id=? AND object_id IS NULL",
                (value, f"vlm {a['model']}: {a['answer']}", a["key"]),
            ).rowcount
            counts["set"] += applied
        timings = {k: v for k, v in a.items() if k not in ("key", "model", "answer", "run")}
        provenance.record(
            conn,
            [
                dict(
                    pictogram_id=provenance._representative(conn, a["key"]),
                    subject="depiction",
                    subject_id=a["key"],
                    field="object",
                    value=value,
                    raw=a["answer"],
                    method="vlm",
                    model=model,
                    input="image",
                    run=a.get("run") or run,
                    context={"applied": bool(applied), **timings},
                )
            ],
        )
    conn.commit()
    return counts


def run_claude(
    conn: sqlite3.Connection,
    cfg: Any,
    sample: list[dict[str, Any]],
    model: str,
    effort: str | None,
    out: Path,
    batch: int = 24,
    limit: int | None = None,
    workdir: Path = Path("data/bench-claude"),
    log: Any = print,
) -> dict[str, Any]:
    """Claude on the sample as in production (numbered sheets of ``batch``
    pictograms, the hierarchy prompt); per image: the object, seconds, tokens
    and list-price cost (the call's usage split evenly). Resumes like ``run``."""
    from . import ai

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["key"] for line in out.read_text().splitlines()} if out.exists() else set()
    _sample_table(conn, sample)
    rows = conn.execute(
        """SELECT b.key, p.norm_path FROM bench_sample b
           JOIN pictogram p ON p.source_id = b.source_id AND p.original_id = b.original_id ORDER BY b.key"""
    ).fetchall()
    rows = [r for r in rows[:limit] if r["key"] not in done and r["norm_path"] and cfg.resolve(r["norm_path"]).exists()]
    name = f"claude:{model}@effort={effort or 'off'}"
    stats: dict[str, Any] = {"answered": 0, "calls": 0, "cost_usd": 0.0}
    with out.open("a") as f:
        for start in range(0, len(rows), batch):
            part = rows[start : start + batch]
            image = ai.sheet([cfg.resolve(r["norm_path"]).read_text() for r in part], cols=6)
            t = time.monotonic()
            try:
                answer, result = ai.ask(
                    image, ai.HIERARCHY_PROMPT, "You look at pictograms and report what you see. Reply with JSON only.", workdir, model=model, effort=effort
                )
            except ai.QuotaExceeded:
                raise
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:  # one bad batch: skip it, as the production pass does
                log(f"{name}: batch at {start} failed: {str(e)[:200]}")
                stats["failed"] = stats.get("failed", 0) + 1
                continue
            n = len(part)
            seconds = round((time.monotonic() - t) / n, 2)
            usage = result.get("usage") or {}
            cost = result.get("total_cost_usd") or 0.0
            per = {
                "input_tokens": usage.get("input_tokens", 0) / n,
                "output_tokens": usage.get("output_tokens", 0) / n,
                "cache_read_tokens": usage.get("cache_read_input_tokens", 0) / n,
                "cache_write_tokens": usage.get("cache_creation_input_tokens", 0) / n,
            }
            for i, r in enumerate(part, 1):
                a = answer.get(str(i))
                obj = clean(a["object"]) if isinstance(a, dict) and isinstance(a.get("object"), str) else ""
                line = {
                    "key": r["key"],
                    "model": name,
                    "answer": obj,
                    "seconds": seconds,
                    **{k: round(v, 1) for k, v in per.items()},
                    "cost_usd": round(cost / n, 5),
                }
                f.write(json.dumps(line) + "\n")
                stats["answered"] += 1
            f.flush()
            ai._log_run(conn, "bench", result)
            conn.commit()
            stats["calls"] += 1
            stats["cost_usd"] = round(stats["cost_usd"] + cost, 4)
            log(f"{name}: {start + n}/{len(rows)}, ${stats['cost_usd']:.3f}")
    return stats


def vlm_slice(items: list[dict[str, Any]], sources: list[dict[str, Any]], shard: int, shards: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A runner's contiguous share of the items, sorted by source so it needs
    few sources, and the job list of just those sources."""
    ordered = sorted(items, key=lambda i: (i["source_id"], i["key"]))
    size = -(-len(ordered) // shards)
    part = ordered[shard * size : (shard + 1) * size]
    need = {i["source_id"] for i in part}
    return part, [s for s in sources if s["id"] in need]


def vlm_backup(
    conn: sqlite3.Connection,
    cfg: Any,
    model: str = "opus",
    effort: str | None = "high",
    limit: int | None = None,
    batch: int = 24,
    workdir: Path = Path("data/vlm-backup"),
    log: Any = print,
) -> dict[str, Any]:
    """Claude names the object where the vision model gave none (unresolvable
    answer, or the pictogram was missing on the runner): sheets of ``batch``,
    the hierarchy prompt, method 'ai'. Raises ``ai.QuotaExceeded`` at the limit;
    a rerun continues where it stopped."""
    from . import ai
    from .concepts import _ensure, resolve
    from .hierarchy.names import object_head

    rows = conn.execute(
        # SQLite takes the bare columns from the row holding MAX(g.size)
        """SELECT d.id, g.representative_id AS rid, p.norm_path, MAX(g.size) FROM depiction d
           JOIN style_group g ON g.depiction_id = d.id JOIN pictogram p ON p.id = g.representative_id
           WHERE d.object_id IS NULL
             AND d.id IN (SELECT subject_id FROM classification WHERE method = 'vlm' AND subject = 'depiction' AND value IS NULL)
             AND d.id NOT IN (SELECT subject_id FROM classification WHERE method = 'ai' AND subject = 'depiction')
           GROUP BY d.id ORDER BY d.id"""
    ).fetchall()
    rows = [r for r in rows[:limit] if r["norm_path"] and cfg.resolve(r["norm_path"]).exists()]
    name = f"{model}@effort={effort or 'off'}"
    seen: set[str] = set()
    stats: dict[str, Any] = {"asked": 0, "set": 0, "cost_usd": 0.0}
    workdir.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(rows), batch):
        part = rows[start : start + batch]
        image = ai.sheet([cfg.resolve(r["norm_path"]).read_text() for r in part], cols=6)
        try:
            answer, result = ai.ask(
                image, ai.HIERARCHY_PROMPT, "You look at pictograms and report what you see. Reply with JSON only.", workdir, model=model, effort=effort
            )
        except ai.QuotaExceeded:
            raise
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
            log(f"backup batch at {start} failed: {str(e)[:200]}")
            continue
        run = ai._log_run(conn, "vlm-backup", result)
        stats["cost_usd"] = round(stats["cost_usd"] + (result.get("total_cost_usd") or 0), 4)
        for i, r in enumerate(part, 1):
            a = answer.get(str(i))
            raw = a.get("object") if isinstance(a, dict) and isinstance(a.get("object"), str) else None
            tokens, _ = object_head(raw) if raw else ([], [])
            concept = resolve(tokens) if tokens else None
            value = concept.id if concept is not None and concept.id.startswith("wn:") else None
            if value:
                _ensure(conn, concept, seen)
                stats["set"] += conn.execute(
                    "UPDATE depiction SET object_id=?, method='ai', description=? WHERE id=? AND object_id IS NULL", (value, raw, r["id"])
                ).rowcount
            provenance.record(
                conn,
                [
                    dict(
                        pictogram_id=r["rid"],
                        subject="depiction",
                        subject_id=r["id"],
                        field="object",
                        value=value,
                        raw=raw,
                        method="ai",
                        model=name,
                        input="image",
                        run=run,
                        context={"backup_for": "vlm", "answer": a},
                    )
                ],
            )
            stats["asked"] += 1
        conn.commit()
        log(f"backup: {start + len(part)}/{len(rows)}, {stats['set']} set, ${stats['cost_usd']:.3f}")
    return stats


def raster_disagreements(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Raster pictograms whose original (colour) and black-and-white versions
    the same model named as different objects; ``near`` marks related ones
    (e.g. cup and mug). Shows where the 1-bit conversion lost meaning."""
    rows = conn.execute(
        """SELECT o.subject_id, o.source_id, o.original_id, o.value AS original, m.value AS monochrome,
                  o.raw AS original_raw, m.raw AS monochrome_raw, m.model, o.run
           FROM classification o JOIN classification m
             ON m.subject = o.subject AND m.subject_id = o.subject_id AND m.method = o.method AND m.field = o.field
            AND o.model = m.model || '@variant=original' AND IFNULL(o.run, '') = IFNULL(m.run, '')
           WHERE o.field = 'object' AND o.model LIKE '%@variant=original' AND o.value IS NOT m.value
           ORDER BY o.subject_id"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["near"] = bool(d["monochrome_raw"] and d["original"] and match(d["monochrome_raw"], d["original"]) == "near")
        out.append(d)
    return out


def split_parts(items: list[dict[str, Any]], sources: list[dict[str, Any]], max_items: int) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Job lists too long for one workflow run (256 runners at most) split
    into parts of at most ``max_items``, sorted by source so a part needs few
    sources; each part with the job list of just its sources."""
    ordered = sorted(items, key=lambda i: (i["source_id"], i["key"]))
    parts = [ordered[i : i + max_items] for i in range(0, len(ordered), max_items)] or [[]]
    out = []
    for part in parts:
        need = {i["source_id"] for i in part}
        out.append((part, [s for s in sources if s["id"] in need]))
    return out
