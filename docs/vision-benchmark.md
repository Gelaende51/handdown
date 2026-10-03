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
(39–52 %), Laya on pictogram names (28 %, name rules 30 %). GitHub Models
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
