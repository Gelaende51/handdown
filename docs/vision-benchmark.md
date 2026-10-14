# Vision benchmark: naming the drawn object

Which model names the object a pictogram depicts well enough to label the
≈130,000 depictions without an object? Every model below answered the same
244 pictograms (fewer where noted), drawn from 142 icon sets
(`work/bench/sample.jsonl`; method in
`docs/superpowers/specs/2026-09-30-vision-benchmark-design.md`). All answers are
in `work/bench/results/` and in the `classification` table with their origin.

**Decision (2026-10-02):** Qwen3-VL 4B on Ollama labels the long tail on free
GitHub runners; Claude Opus (reasoning high) answers what Qwen leaves
unresolved. **Addition (2026-10-03):** LongCat 2.5 Preview (free on Nous
Portal and OpenCode Zen, 60 %, 3.4 s per image) is a fast first pass from the
host, for what the runners have not reached yet and for composite parts,
which only the host's catalog has (see *Hosted models*).

## Results

| Model | Runs on | vs Opus high | vs Claude labels | Cross, family-balanced | Cross vs Claude keys | Cross vs open keys | s / image | Tokens per image (in / out) | $ per 1,000 | Images per week |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude Opus, reasoning high | Claude quota | (reference) | 70 % | 59.2 % | 69.0 % | 49.3 % | 0.9 | ≈0 / 58 | 1.56 | ≈110k–330k |
| Claude Opus, reasoning off | Claude quota | 92 % | 71 % | 59.6 % | 69.6 % | 49.6 % | 1.5 | ≈50 / 56 | 1.53 | ≈115k–340k |
| Claude labels (production: Sonnet, reasoning off) | – | 77 % | – | 61.5 % | 70.8 % | 52.2 % | – | – | – | – |
| Claude Sonnet, reasoning off | Claude quota | 76 % | 71 % | 59.7 % | 70.3 % | 49.1 % | 1.5 | ≈54 / 57 | 0.78 | ≈225k–665k |
| Claude Sonnet, reasoning high | Claude quota | 74 % | 70 % | 59.3 % | 70.0 % | 48.5 % | 2.3 | ≈54 / 75 | 0.96 | ≈185k–545k |
| **Qwen3-VL 4B (Ollama)** | runner CPU | **73 %** | 63 % | **59.7 %** | 63.1 % | **56.2 %** | 55–85 | 1,083 / 3 | free | ≈140k–220k |
| Qwen3-VL 4B (llama.cpp, 1,024 image tokens) | runner CPU | 61 % | 55 % | 55.5 % | 55.2 % | 55.8 % | 79 | 1,083 / – | free | ≈150k |
| Gemma 3 4B (Ollama), 127 images | runner CPU | 60 % | 57 % | 50.9 % | 53.7 % | 48.0 % | 163 | – | free | ≈74k |
| Qwen3-VL 4B (llama.cpp, 576 tokens) | runner CPU | 58 % | 54 % | 55.9 % | 53.6 % | 58.2 % | 43 | 635 / – | free | ≈280k |
| Claude Haiku, reasoning off | Claude quota | 55 % | 55 % | 49.2 % | 57.9 % | 40.5 % | 2.6 | ≈45 / 46 | 0.28 | ≈630k–1.9M |
| Qwen3-VL 4B (llama.cpp, 256 tokens, Q8_0) | runner CPU | 54 % | 52 % | 53.9 % | 51.1 % | 56.7 % | 20 | 315 / – | free | ≈610k |
| Qwen3-VL 4B (llama.cpp, 256 tokens, Q4_K_M) | runner CPU | 54 % | 52 % | 53.4 % | 50.4 % | 56.4 % | 10 | 315 / – | free | ≈1.2M |
| Qwen3-VL 2B (llama.cpp, 256 tokens) | runner CPU | 51 % | 49 % | 47.8 % | 47.7 % | 47.8 % | 10 | 315 / – | free | ≈1.2M |
| Qwen2.5-VL 3B (Ollama), 234 images | runner CPU | 50 % | 45 % | 42.7 % | 44.7 % | 40.6 % | 89 | – | free | ≈136k |
| Qwen3-VL 4B (llama.cpp, 64 tokens, Q8_0) | runner CPU | 49 % | 51 % | 49.7 % | 47.8 % | 51.5 % | 5.4 | 123 / – | free | ≈2.2M |
| Claude Haiku, reasoning high | Claude quota | 49 % | 49 % | 45.5 % | 51.6 % | 39.3 % | 4.6 | ≈47 / 488 | 2.49 | ≈70k–210k |
| Qwen3-VL 4B (llama.cpp, 64 tokens, Q4_K_M) | runner CPU | 48 % | 50 % | 48.4 % | 46.7 % | 50.1 % | 4.4 | 123 / – | free | ≈2.7M |
| Qwen3-VL 2B (llama.cpp, 64 tokens) | runner CPU | 40 % | 41 % | 39.5 % | 38.7 % | 40.2 % | 2.5 | 123 / – | free | ≈4.8M |
| OmniParser v2 icon captions | runner CPU | 22 % | 21 % | 19.9 % | 20.3 % | 19.5 % | 0.1 | – | free | – |
| Moondream 2 (Ollama) | runner CPU | – | 3 % | (excluded) | – | – | 18 | – | free | – |

Measured on other sets, not directly comparable: the linear probe on our
SigLIP + DINOv2 embeddings (58 % cross-validated overall, 98 % on the 9 % it is
confident about), SigLIP zero-shot (34–51 %), DINOv2 nearest neighbour
(39–52 %), Laya on pictogram names (28 %, name rules 30 %; Clef 29 %, see
*Decision models*). GitHub Models
(GPT-4.1 mini, Llama 4 Scout) was retired on 2026-07-30 before it could be
measured.

## Hosted models (2026-10-03)

The same 244 pictograms through hosted APIs: Vercel's AI Gateway (paid per
token, free monthly credit), and the free tiers of Nous Portal (through Hermes
Agent's subscription proxy on the host) and OpenCode Zen. Hosted models see
the rendered pictograms. Cross agreement was not computed for these.

| Model | Via | vs Claude labels | Exact only | s / image | Tokens per image (in / out) | $ per 1,000 |
|---|---|---|---|---|---|---|
| *Qwen3-VL 4B (Ollama), for comparison* | *runner CPU* | *63 %* | *57 %* | *55–85* | *1,083 / 3* | *free* |
| Qwen 3.7 Flash (135 of 244 answered) | Vercel | 64 % | 55 % | 38 | 125 / 3,143 (reasoning) | ≈0.41 |
| **LongCat 2.5 Preview** | Nous free | **60 %** | 55 % | **3.4** | – | free |
| Gemma 4 26B A4B | Vercel | 58 % | 52 % | 7.4 | 320 / 3 | ≈0.05 |
| Gemini 2.5 Flash-Lite | Vercel | 57 % | 51 % | 11.5 | 306 / 2 | ≈0.03 |
| Ling 3.0 Flash VL | Vercel | 55 % | 49 % | 24 | 136 / 252 | ≈0.07 |
| *Claude Haiku, reasoning off, for comparison* | *Claude quota* | *55 %* | *49 %* | *2.4* | | |
| Nova Lite | Vercel | 54 % | 50 % | 27 | 583 / 3 | ≈0.04 |
| Llama 4 Scout | Vercel | 53 % | 46 % | 12 | 296 / 3 | ≈0.05 |
| space-bunny-alpha (reasoning on / off) | Nous free | 46 % / 45 % | 43 % / 42 % | 6.1 | – | free |
| Ministral 3B | Vercel | 43 % | 38 % | 24 | 164 / 3 | ≈0.02 |
| MiMo 2.6 Flash | Vercel | 43 % | 38 % | 8.5 | 122 / 328 | ≈0.42 |
| GPT-5 nano | Vercel | 40 % | 36 % | 9.7 | 151 / 728 | ≈0.30 |
| StepFun 3.7 Flash (≈50 answered) | Nous free | ≈32 % | ≈28 % | 170 | – | free |
| GPT-6 Luna | Vercel | refused (403) | | | | |

- Seconds are one request at a time, including the providers' rate limits;
  Vercel's free plan throttles (the cheapest models took 1–3 hours for 500
  images). $ per 1,000 from the listed token prices.
- **LongCat is the fast free pass:** 3 points below Qwen3-VL at a twentieth of
  the time, from the host (Nous through the Hermes proxy, or OpenCode Zen;
  the container reaches neither). On Zen it is not among the models whose
  data may be used for training.
- Among paid models, Gemma 4 and Gemini Flash-Lite give 57–58 % for about
  $0.03–0.05 per 1,000 images, so a $5 credit labels ≈100,000–160,000
  pictograms a month, if the rate limits allow it.
- Reasoning models spend the answer budget thinking: Qwen 3.7 Flash, MiMo and
  GPT-5 nano produce 10–1,000× more output tokens for the same names, and
  StepFun and space-bunny often return nothing; asking for no reasoning
  (`think=off`) did not change their scores.

## Decision models: Clef (2026-10-14)

Cloudflare's Clef and Clef Flash (open weights, served on Workers AI) never
write text. They return probabilities for options they are given, so they
can't name a pictogram, only choose a name. Both benchmarks were run through
`~/building_stuff/scripts/jev.mjs`. The answers are in
`work/bench/results/clef-referee.jsonl` and `text-clef*.jsonl`.

**Images, as a referee.** Clef sees the rendered pictogram (256 px) and picks
one name from the distinct answers of all non-Claude models above. There
are 9 options on average and at most 21, for 242 of the 244 pictograms; 2
had fewer than two distinct answers. In 86 % of the cases at least one
option matches the Claude label, so that is the ceiling.

| Model | vs Claude labels | Exact only | s / image | Tokens per image (in / out) | $ per 1,000 |
|---|---|---|---|---|---|
| *Qwen3-VL 4B (Ollama), for comparison* | *63 %* | *57 %* | *55–85* | *1,083 / 3* | *free* |
| *Majority vote over the same answers* | *61 %* | *53 %* | – | – | – |
| Clef | 57 % | 50 % | ≈0.55 | 340 / 0 | ≈0.08 |
| Clef Flash | 56 % | 50 % | ≈0.5 | 340 / 0 | ≈0.013 |
| Clef, same options without the image | 47 % | 40 % | – | 280 / 0 | – |
| Clef, with an extra "other" option | 58 % | – | ≈0.6 | 370 / 0 | ≈0.09 |
| Clef Flash on a runner (llama.cpp, Q4_K_M, 4 CPUs), first 40 pictograms | 17 of 40 | – | 22 | 360 / 0 | free |

- As a referee, Clef is below a plain majority vote over the answers it chooses
  from, and below Qwen3-VL alone. It does use the image: without it, it
  scores 10 points lower.
- Its confidence is what's useful. At p ≥ 0.8, 24 pictograms (10 %) match the
  Claude label 96 % of the time; at p ≥ 0.5, 101 pictograms match 75 % of the
  time. That makes it a cheap way to confirm the long tail's labels, not a
  labeller.
- With "other" added, Clef never picked it here. Even when no option fits,
  it chooses one of the names. The score barely moves (58 %; 95 % at
  p ≥ 0.8 on 22 pictograms).
- Seconds are per request, run 8 at a time (242 images in 16 s).
- **Clef Flash runs on a free runner** (`clefcpp:ggml-org/Clef-Flash-GGUF:Q4_K_M`,
  run 38614598277). The 4-bit model and its image projector take ~7 GB, so they fit
  the runner's 16 GB. It needs llama.cpp b11617 or later, for `/v1/systemone` with
  images.
- Speed: 22 s per pictogram (median 21 s, max 35 s), about 6 s per 100 prompt
  tokens. That's 2.5–4× faster than Qwen3-VL 4B on the same runner, because there
  is nothing to generate. The full Clef (27B) doesn't fit a runner.
- It gave the same answer as Workers AI's Clef Flash for 31 of the 40 pictograms,
  matching the Claude label 17 times against 18 hosted; the gap is from 4-bit
  quantization and the added "other". So the runners reproduce the hosted model,
  without the 10,000-neuron daily cap.
- At 22 s, 20 parallel jobs check ≈3,300 pictograms an hour. A referee pass over the
  130,000 open depictions takes ≈40 hours of runner time, after a naming model has
  given it options. Workers AI
  bills input only, and images come in as embedded data URIs (no remote URLs,
  at most 4 per request).

**Text, picking the object from the names** (the 2,000 items of
`work/bench/text.jsonl`, the same set Laya was measured on):

| Model | Exact | On the 643 items with a real choice | p ≥ 0.9: share of items, correct | $ per 1,000 |
|---|---|---|---|---|
| Name rules | 30 % | 72.9 % | – | free |
| Clef | 29 % | 71.7 % | 60 %, 86 % | ≈0.04 |
| Clef Flash | 29 % | 71.2 % | 56 %, 81 % | ≈0.006 |
| Clef with "other" | 29 % | 70.8 % | 46 %, 43 % | ≈0.05 |
| Laya | 28 % | 69.1 % | 3 %, 90 % | free (CPU) |

- 38 % is the most any model can reach, because only that share of items has
  the Claude label among the candidates. Clef refuses a choice with only one
  option. In the first two rows, those 324 items count as their single
  candidate. In the "other" row every item gets an extra option, "None of
  these", and Clef really answers all of them.
- On text, "other" works as a way out. Clef chose it for 109 items, and for 94
  of them the Claude label really wasn't among the candidates. Of the 324
  single-candidate items, it got 126 right and passed on 22. Its p ≥ 0.9
  answers are no longer reliable with "other" (43 % right), because a sure
  "other" counts as a miss.
- Clef matches the name rules and Laya on this task, so it adds nothing here.

**Many classes at once.** Classifying a pictogram against every known object
would mean one choice over 1,369 options. Clef accepts **at most 255 options
per choice question** (HTTP 422 above that). A request can carry up to 64
questions, but each is judged on its own. Each option costs about 22 input
tokens per request, re-sent every time. A test on 40 benchmark pictograms
used a fixed list of objects that always contained the Claude label:

| Options | Clef | Clef Flash | Tokens per image |
|---|---|---|---|
| 64 | 62 % | 42 % | 1,400 |
| 128 | 48 % | 40 % | 2,900 |
| 254 | 40 % | 28 % | 5,700 |

- Accuracy falls with the number of options. Against all 1,369 objects, Clef
  would need at least 6 questions and ~30k tokens per image (≈$940 for the
  130,000 open depictions), and would land below 40 %.
- What scales is a shortlist first: the embeddings' nearest objects, the name
  rules or the open models' answers. Clef then picks among ≤ ~32 plus
  "other", at ~700 tokens per image (≈$22 on Clef, ≈$3.5 on Flash for
  130,000).
- Workers AI's free plan allows 10,000 neurons a day, which is about 450k Clef
  or 2.9M Clef Flash tokens. After that, calls are refused until 00:00 UTC.
  Metering lags, so the usage figure passed 400 % before the first refusal.
  A full run over 130,000 depictions needs Workers Paid or many days.

## How the columns are computed

- **vs Opus high / vs Claude labels**: share of answers matching the
  reference's object exactly or nearly (WordNet; filler words like "icon"
  dropped; near = path distance ≤ 4 under a specific shared parent).
  Claude's production labels reproduce only 71 % with the same model and
  setup, so no reference is ground truth.
- **Cross**: every model (Moondream excluded) is the answer key in turn; a
  model's agreement is averaged over the other 18 keys after resolving the
  key's answer to a WordNet object. Agreement clusters by family (7 Claude
  keys, 12 open-model keys of which 9 are Qwen3-VL variants), so the
  family-balanced column weights both families equally.
- **s / image**: Claude per call of 24 pictograms on one sheet, divided by 24
  (calls can run in parallel; the quota is the limit). Runners: one image at a
  time on a 4-CPU GitHub runner, setup not counted.
- **Tokens**: Claude's sheet tokens divided by 24; local models' prompt
  tokens per image (almost all image tokens; Ollama scales every image to
  ~1,000 tokens).
- **$ per 1,000**: Claude list price as reported by the CLI; runners are free
  for this public repository.
- **Images per week**: runners: 20 parallel jobs × 168 h ÷ s per image.
  Claude: two `/api/oauth/usage` readings 51 minutes apart put the plan at
  roughly $175–520 of list price per week (7-day utilization moved by one
  whole percent) and at most ~$12 per 5 hours; the range assumes the whole
  quota goes to labelling, which other sessions share.

## Findings

- Reasoning does not help on this task: Opus at high effort hardly reasons
  (58 output tokens per image), Sonnet high scores like Sonnet off, and
  Haiku high is worse and 9× more expensive than Haiku off.
- Qwen3-VL 4B on Ollama agrees best with both families (63 % with Claude
  keys, 56 % with open keys) and ranks with Sonnet and Opus on the
  family-balanced average, without quota.
- llama.cpp makes Qwen3-VL 12–20× faster by capping image tokens, but loses
  8–15 points against Ollama; neither image tokens (64 → 1,024: +5 points)
  nor quantization (Q8_0 = Q4_K_M) explain the gap.
- Of 244 Qwen3-VL answers, 7 (3 %) name no WordNet object ("cc symbol",
  "html5", "api", "ethereum"); those go to the Opus backup.
