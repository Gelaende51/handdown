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
  - `github:<model>`: GitHub Models with the workflow token (`models: read`);
    10 images per request and numbered answers, because the free tier allows
    about 150 requests a day.
  - `florence:omniparser`: OmniParser v2 icon caption model (Florence-2
    fine-tuned on UI icons), 64 px renders as in OmniParser.
- Same prompt for every model: name the single object or symbol in 1–3
  English words.

## Scoring (local)

`handdown bench-score` maps each answer to WordNet (whole phrase, then head
word) and compares with Claude's object:

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
