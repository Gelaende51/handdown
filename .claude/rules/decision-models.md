# Description
- When to hand a simple judgement over many items to a decision model, Cloudflare's
  Clef, instead of reading every item into Claude's context. Tool: `uv run handdown
  decide` (skill `decide`).

# Rules
- Before reading many items to make the same simple judgement about each (classify,
  filter, flag, triage, score: pictograms, names, sources, log lines, commits, issues),
  stop and weigh whether `handdown decide` is cheaper. Say briefly which way you
  decided and why.
- Use it when only the verdict per item matters and the items add up to far more text
  than the verdicts. Skip it for a few items, for judgements that need reasoning across
  items or the code, and for exact rules that code can check.
- Clef only chooses; it never names or writes. Give it the candidates. Every choice gets
  "other" as a way out, and at most 255 options fit in one choice. For more classes, use
  a tree (`--tree`). Keep each stage small (~40–60 options), because accuracy falls with
  the number of options.
- Workers AI's free plan allows 10,000 neurons a day. The tool keeps its own count and
  stops at 100% (exit 75), because Cloudflare's count lags ~20 minutes. For large runs
  use the self-hosted Clef Flash on GitHub Actions (`clefcpp:`/`cleftree:` in the bench
  workflow, `--endpoint` for a server of your own), which has no daily cap.
- Spot-check its answers before acting on them. Trust a confident answer (p ≥ 0.8) more
  than the rest; a low-confidence score is close to a guess.
- Record every evaluation, used or not, in `dev/takeaways.md` under *Decision models*:
  task, items, tokens you would have read vs. read, model and cost, verdict quality.
  Every run is also logged in `work/decide-usage.jsonl`.
- The token comes only from the environment (`CLOUDFLARE_API_TOKEN`, set by the
  operator with `cradle secret set` on the host). Never print it, write it to a file or
  ask for it in the chat.
