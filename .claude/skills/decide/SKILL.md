---
name: decide
description: Run simple judgements (pick-one, yes/no, rate-on-a-scale) over many items with Cloudflare's Clef decision model through `uv run handdown decide`, instead of reading every item into Claude's context. Use when a task means classifying, filtering, triaging, flagging or scoring dozens or more items (pictograms, names, sources, log lines, commits, issues), including classifying into more classes than one choice holds (decision tree).
---

# Clef decision model: `handdown decide`

Clef (Cloudflare, open weights) answers typed questions about a state, which can be text,
JSON or up to 4 images, with probabilities. It never generates text. Only input tokens
are billed.

| model | $/M input | context | notes |
| --- | --- | --- | --- |
| `clef` (default) | 0.24 | 65k | most accurate |
| `clef-flash` | 0.038 | 24k | 6× cheaper; also runs on a free GitHub runner (llama.cpp, 22 s per image) |

## How to call

```bash
uv run handdown decide items.jsonl --spec q.json --model clef-flash --job <name> > answers.jsonl
uv run handdown decide items.jsonl --tree work/bench/clef-tree.json --beam 2 \
    --instructions "Which object does this pictogram depict?" > classes.jsonl
uv run handdown decide items.jsonl --spec q.json --endpoint http://<server>:8082/v1/systemone
```

- `items.jsonl`: `{"id", "state", "images"?: [png/jpeg/webp paths], "questions"?}` per line.
  An item's own `questions` replace the spec's. Images must be files or data URLs, not
  remote URLs.
- stdout: `{"id", "answers"}` per item (tree: `{"id", "class", "label", "p", "path"}`).
  stderr: the totals. Filter with `jq` before reading, e.g. only `p >= 0.8` or `other`.
- Exit 75: the daily free allocation is used up. The rest was skipped, and it resets at
  00:00 UTC.

`q.json`:

```json
{"questions": {
  "kind": {"type": "choice", "instructions": "What kind of source is this?",
           "criteria": {"icons": "An icon set", "font": "A symbol font", "aac": "An AAC symbol system"}},
  "free": {"type": "noul", "instructions": "Does the license allow redistribution?"},
  "fit":  {"type": "score", "instructions": "How well does it work at 16 px?",
           "criteria": ["Unreadable", "Readable with effort", "Clear", "Crisp"]}
}}
```

- `choice` criteria is an object; "other" is added automatically.
- `score` criteria is an array, lowest first. `noul` returns P(yes), and 0.5 means
  undecided.
- Put the full meaning in `instructions`, because question ids are not sent.

## Many items for free: on GitHub runners

`decide.yml` runs Clef Flash on runners (llama.cpp, no daily cap, no token), sliced across
up to 256 jobs. It handles pictogram job lists (`work/vlm/items-*.jsonl` + sources, rendered
on the runner) or text items, with a spec or a tree. The answers are committed to
`work/decide/answers/`. Start it with `gh workflow run decide.yml -f items=… -f spec=…|tree=…`;
`docs/actions.md` has the examples. Prefer it over Workers AI for more than a few hundred
pictograms.

## Limits that matter

- At most **255 options** per choice. Accuracy falls well before that: on pictograms,
  62% at 64 options, 48% at 128, 40% at 254. For many classes use a tree of
  ~40–60 options per stage. With `--beam 2` the second stage chooses among the two
  likeliest groups' members.
- Text that doesn't fit the context is cut off without an error. Split long items.
- Free plan: 10,000 neurons a day (~450k clef or ~2.9M clef-flash tokens). The tool
  refuses to start at 100% and keeps its own count; `--over-limit` runs past it.
  Cloudflare's own count lags ~20 minutes.
- As a referee over other models' names it doesn't beat a majority vote (57% vs 61%), but
  its confident answers are reliable (96% at p ≥ 0.8). Use it to confirm, not to label.

## Verify, then record

1. Spot-check a handful of answers, especially those near the threshold.
2. Add an entry to `dev/takeaways.md` under *Decision models*: the task, item count,
   tokens you would have read vs. what you read, the model and its cost (stderr; also
   `work/decide-usage.jsonl`), and whether the verdicts were good enough. Also record when
   you decided against it.
