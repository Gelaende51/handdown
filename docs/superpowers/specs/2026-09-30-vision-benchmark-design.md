# Vision benchmark: open and free models against Claude's labels

## Goal

Find out whether a free model (self-hosted on GitHub Actions or a free API) can
name the object a pictogram depicts well enough to label the long tail that
Claude has not assessed and the linear probe cannot cover (3.6 % coverage at
p ≥ 0.9). Answer key: the 20,052 depictions Claude assessed (`method='ai'`).

## Sample

- `handdown bench-sample` draws 500 Claude-assessed depictions: sources are
  shuffled (seeded) and at most 4 depictions are taken per source, so the
  sample spans many styles. The pictogram is the representative of the
  depiction's largest style group.
- Only `iconify`, `npm-svg` and `git-svg` sources: runners must re-harvest
  whole sources, and fonts and Commons are slow to harvest. Cost: the sample
  misses those two source kinds.
- Tracked: `work/bench/sample.jsonl` (key = depiction id, source id,
  original id) and `work/bench/sources.jsonl` (job list). Metadata only; the
  answer key stays in the local database.

## Runner

- `bench.yml`, one matrix job per model. Each job imports the job list,
  harvests, marks every non-sample pictogram as measured (`bench-prepare`) so
  `process` normalizes only the sample, renders black on white, asks the
  model and writes `{key, model, answer, seconds}` per pictogram.
- Pictograms never leave the runner (no artifact, no cache). Only the answers
  (text) are uploaded; a collect job commits them to
  `work/bench/results/<run id>.jsonl`.
- Backends:
  - `ollama:<tag>`: Ollama installed on the runner, one image per request,
    temperature 0 (Qwen2.5-VL 3B, Qwen3-VL 4B, Gemma 3 4B, Moondream 2).
  - (GitHub Models was planned as a free API, but it was retired on
    2026-07-30; the backend was removed after the first run.)
  - `florence:omniparser`: OmniParser v2 icon caption model (Florence-2
    fine-tuned on UI icons), 64 px renders as in OmniParser.
- Same prompt for every model: name the single object or symbol in 1–3
  English words.

## Scoring (local)

`handdown bench-score` maps each answer to WordNet (whole phrase and every
word, filler words like "icon" dropped) and compares with Claude's object:

- exact: Claude's synset is among the answer's synsets;
- near: WordNet path distance ≤ 4 under a specific shared parent (depth ≥ 6:
  mug/cup under container, thumb/hand under extremity; house/key under
  artifact is a miss). Loose on purpose: dog/cat counts as near;
- miss otherwise; seconds per image per model.
- Claude's objects outside WordNet (`term:`, 7 of 500) match by normalized
  text ("check mark" = `term:checkmarks`).
- Chance level: answering "cup" for everything scores 0 % exact, 6 % near.

Claude's labels are themselves imperfect, so near matches and a printed
sample of disagreements are read, not just the percentages.

## Decision

A model that reaches the probe's high-confidence accuracy on a meaningful
share, or clearly beats zero-shot SigLIP (34–51 %) overall at a runtime that
fits a day of runners for ~135k depictions, becomes the long-tail labeller
(method `vlm`, like `probe`). Otherwise Claude keeps labelling hard cases and
the probe retrains on them.

## Text benchmark (Laya)

- `handdown bench-text-items` writes 2,000 Claude-assessed depictions as text:
  the member pictograms' names (state) and up to 8 WordNet candidates from
  their object words, the name rules' pick first (`work/bench/text.jsonl`,
  metadata only). `bench-text.yml` asks Laya (Apache-2.0, ModernBERT-large,
  CPU) to choose one; `bench-text-score` compares with Claude and the rules.
- Baseline before Laya: the name rules match Claude on 30 %, and Claude's
  object is among the candidates for only 38 %: names mostly say what an
  icon means ("users", "lamp", "fuel"), not what is drawn (silhouette, light
  bulb, gas pump). Choosing among name candidates can gain at most 8 points;
  the drawn object needs vision, text fits the meaning level.
- Result (run 36782921650): Laya zero-shot 28 % vs rules 30 %; 69 % vs 73 %
  where Claude's object is among several candidates; p ≥ 0.9 only 37 % right.
  Not adopted for objects.

## Long-tail labelling (vlm.yml)

- `handdown vlm-jobs` writes per adapter the depictions without an object
  (representative of the largest style group) and their sources
  (`work/vlm/items-/sources-<adapter>.jsonl`, 130,012 depictions, 544 sources).
- `vlm.yml` gives each runner a contiguous slice of about 250 depictions
  (sorted by source, `vlm-slice`) and the job list of just their sources;
  it harvests those, processes only the listed pictograms (`bench-prepare`)
  and answers them (`bench-run --present-only`). Only the answers are
  committed. Model: Qwen3-VL 4B on Ollama (decision in
  `docs/vision-benchmark.md`); a runner stopped by the 6-hour limit still
  uploads its answers, and `vlm-jobs` leaves out every depiction already
  answered.
- `handdown vlm-backup` sends what the model left (answer with no WordNet
  object, or pictogram missing on the runner) to Claude Opus with high
  reasoning in sheets of 24, as method `ai`; it exits 75 at the usage limit.
- `handdown vlm-apply` resolves an answer's head noun like a pictogram name
  (`object_head`, `resolve`) and sets the object as method `vlm`, only where
  a depiction has none.
