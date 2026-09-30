# Vision Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Compute DINOv2 embeddings and SigLIP zero-shot labels for every pictogram on GitHub Actions. Merge them back, and use them to fill objects and views.

**Architecture:** `handdown.vision` (vocabulary, runner inference, shard export/import, apply), and the `embed.yml` workflow. Torch/transformers are runner-only.

**Spec:** `docs/superpowers/specs/2026-09-30-vision-models-design.md`

## Global Constraints

- Shards are age-encrypted (reuse `shard.py` crypto); `export-vision` refuses plaintext on Actions.
- No assets in git: `labels.jsonl` is metadata; embeddings travel only in encrypted shards.
- Local container: 3 GB. Imports stream; no torch locally.
- Workflow changes are pushed by the operator (`cradle push`).

## Review Focus

- Pictogram in a shard that is unknown locally (new source): skipped and counted, no crash. → Task 3 test.
- Label vocabulary with duplicate texts: deduplicated. → Task 2 test.
- A model score below threshold never overwrites a rule/AI object. → Task 4 test.
- Iconify job list with a subset of collections: only those are harvested. → Task 1 test.
- An empty render (blank SVG): skipped, not embedded as zeros. → Task 3 test.

### Task 1: Iconify adapter respects the job list

**Files:** `src/handdown/adapters/iconify.py`, `src/handdown/registry.py`; test `tests/test_sources.py`
**Interfaces:** `IconifyAdapter(cfg, conn=None)`. `sources()` yields only the Iconify sources present in `source` with status `accepted` when there are any (job-list mode), otherwise all collections.
- [ ] Test: with a fake `collections.json` of 3 prefixes, and 1 of them accepted in the DB, `sources()` yields that 1. With none accepted, it yields 3.
- [ ] Implement, run, commit.

### Task 2: Label vocabulary

**Files:** `src/handdown/vision.py` (`build_vocabulary(conn) -> list[dict]`), CLI `vision-vocab OUT`; test `tests/test_vision.py`
**Interfaces:** each label is `{"id": str, "kind": "object"|"view"|"feature", "text": str}`. Object labels come from concepts whose WordNet synset lexname is in `OBJECT_LEXNAMES` (plus `term:` concepts whose parent is one), deduplicated by text.
- [ ] Test: concepts cup (artifact), download (verb), and two concepts with the same label → 1 cup object label, no download; views and features present; unique texts.
- [ ] Implement, run, commit.

### Task 3: Runner inference, vision shards, import

**Files:** `src/handdown/vision.py` (`embed(conn, cfg, labels, models, batch=64) -> int`, `export_vision(conn, out, recipients)`, `import_vision(conn, cfg, path, identity) -> dict`), `src/handdown/schema.sql` (tables `embedding`, `vision_label`), CLI `embed`, `export-vision`, `import-vision`; test `tests/test_vision.py`
**Interfaces:**
- `models` is an object with `image_features(list[PIL.Image]) -> dict[str, np.ndarray]` (keys `dinov2`, `siglip`) and `label_scores(siglip_feats, kind) -> np.ndarray [n, labels_of_kind]`. A real implementation (`TorchModels`) is imported lazily on runners; tests use a fake.
- `embed` stores `embedding(pictogram_id, model, vec float16 bytes)`, plus `vision_label(pictogram_id, kind, label_id, score, rank)`: the top 5 objects, the top 1 view, and features with p ≥ 0.2. Blank renders are skipped.
- The shard JSONL carries `{source_id, original_id, sha256, emb: {model: b64}, labels: [[kind, label_id, score, rank]]}`.
- `import_vision` matches by (source_id, original_id), counts `unknown` rows and skips them.
- [ ] Tests: fake models → embed stores rows and skips the blank SVG; encrypted export → import into a second catalog matches, and an unknown row is counted.
- [ ] Implement, run, commit.

### Task 4: Apply vision labels

**Files:** `src/handdown/vision.py` (`apply(conn, min_score=0.3) -> dict`), CLI `vision-apply`; test `tests/test_vision.py`
**Interfaces:** a depiction without `object_id` gets the object label whose mean top-1 score over its members is ≥ `min_score` (`method` stays; `description` notes `vision`); `view` likewise when `unknown`. Existing objects are never overwritten.
- [ ] Tests: confident → set; below threshold → untouched; existing object → untouched.
- [ ] Implement, run, commit.

### Task 5: Workflow and run

**Files:** `.github/workflows/embed.yml`, job lists `work/jobs/vision-*.jsonl`
- [ ] Workflow: like `harvest.yml` (plan with key check, matrix). Steps: `uv sync`; `uv pip install torch --index-url https://download.pytorch.org/whl/cpu` and `transformers pillow`; import-sources; harvest `--no-registry`; `handdown process --workers 4`; `handdown embed`; `export-vision`; rm data; upload `*.age`.
- [ ] Job lists for iconify, git-svg, npm-svg, font (and commons after the import) with status harvested.
- [ ] Commit; the operator pushes (workflow change); dispatch; the host imports; `vision-apply`; findings in takeaways.
