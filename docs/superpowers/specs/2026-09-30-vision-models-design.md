# Vision models on GitHub Actions: design

Date: 2026-09-30
Status: approved in conversation ("go ahead with github actions")
Extends: [depiction hierarchy](2026-09-29-depiction-hierarchy-design.md)

## Purpose

Classify every pictogram with specialised vision models instead of (only)
a general chat model: complete coverage, no account quota, no third-party
upload. The chat model (Claude) remains for meanings and low-confidence
cases. A proprietary typed classifier (Jev, TypeSafe AI, early access
since 2026-09-15) can be added later behind the same interface.

## Models (open weights, Hugging Face, run on CPU runners)

| Model | Output | Use |
|---|---|---|
| `facebook/dinov2-small` | 384-dim image embedding (CLS), float16 | style groups and depictions (visual similarity) |
| `google/siglip-base-patch16-224` | 768-dim image embedding + zero-shot scores against a label vocabulary | object, view and feature labels with confidence |

## Label vocabulary (committed metadata)

`work/vision/labels.jsonl`: one line per label `{"id", "kind", "text"}`
- `object`: labels of object concepts in the catalog (WordNet lexnames of
  `hierarchy.names.OBJECT_LEXNAMES`), text "a pictogram of a {label}";
- `view`: the view vocabulary ("… seen from the side", "… from above", …);
- `feature`: a curated list (steam, saucer, lid, frame, slash, arrow, …).

## Runner job (`.github/workflows/embed.yml`)

1. Load a share of the job list (like `harvest.yml`). The Iconify adapter
   restricts itself to the Iconify sources of the job list.
2. Harvest and normalise (without the render-based metrics), render each
   unique SVG at 224 px black on white.
3. DINOv2 and SigLIP in batches; zero-shot softmax per kind; keep the top 5
   objects, the top view and features with p ≥ 0.2.
4. `export-vision` writes a shard (JSONL gz: source_id, original_id, sha256,
   dinov2 b64, siglip b64, labels), encrypted with age like harvest shards.

Vision dependencies (torch CPU, transformers) are installed only on the
runner, not in `uv.lock`.

## Local

- `import-vision` streams shards into `embedding(pictogram_id, model, vec)`
  and `vision_label(pictogram_id, kind, label_id, score, rank)`, matched on
  (source_id, original_id).
- `vision-apply`: depictions without an object get the majority SigLIP
  object of their style groups when its mean score ≥ 0.3 (method `vision`);
  views likewise. Claude (`ai --job hierarchy`) then selects only style
  groups whose depiction has no object or low vision confidence.
- Regrouping with DINOv2 (style ≤ t1, depiction ≤ t2, mirror check via
  silhouettes) is a follow-up once embeddings are merged and thresholds can
  be tuned on real data.

## Testing

- Vocabulary building from concepts (object lexnames only).
- `vision` module with a fake model: top-k and threshold logic, shard round
  trip (encrypted), import matching by source/original id.
- `vision-apply` on a fixture: object set when confident, untouched when not.
