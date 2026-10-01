# Takeaways

Challenges, oddities and insights from building handdown. Library issues are
candidates for bug reports, feature requests or pull requests.

## Environment

- The dev container's proxy only allows GitHub (incl. raw/codeload/API), the
  npm registry and PyPI. Wikimedia Commons, Wikidata, ARASAAC, Hugging Face,
  jsDelivr and search engines are blocked, so standards/signage sources on
  Commons are recorded as `blocked-network` rather than harvested.
- ~750 MB free RAM rules out torch/CLIP; depiction clustering uses 16x16
  scale-normalized ink maps plus filled silhouettes instead.
- Unauthenticated GitHub API: 60 core requests/h, 10 searches/min. Harvesting
  uses codeload tarballs and raw.githubusercontent.com, which are not API-limited.

## Libraries

- **NLTK 3.10**: `nltk.download` refuses to fetch through an HTTP proxy unless
  `NLTK_ALLOW_PROXIED_URLOPEN=1` is set (SSRF protection). Error text is clear.
- **NLTK / OMW**: `lemma_names(lang)` needs `omw-2.0`, not the older `omw-1.4`
  the docs still mention; German (`deu`) is not in OMW (OdeNet is a candidate).
  The first multilingual lookup takes ~50 s (loads every language), later ones
  are milliseconds.
- **numpy 2**: `ndarray.ptp()` was removed; use `np.ptp(a)`.
- **resvg-py**: returns PNG bytes only; decode with Pillow. `skip_system_fonts`
  makes rendering faster and deterministic.
- **sqlite3**: connections are thread-bound; `multiprocessing.Pool.imap` runs
  the task generator in a feeder thread, so a generator that reads from the
  database fails. Read chunks in the main thread instead.
- **sqlite3 upsert**: `INSERT … ON CONFLICT DO UPDATE` still enforces NOT NULL
  on the inserted row before resolving the conflict; omit the column instead of
  passing NULL.
- **SQLite locking**: holding an open write transaction while downloading a
  tarball blocked a parallel process for > 60 s ("database is locked"). Commit
  before any network I/O.

## Data

- Iconify icon bodies may use `xlink:href` without declaring the namespace
  (the declaration lives in the consumer's wrapper); add `xmlns:xlink` only
  when the body uses it — adding it everywhere changes every raw SVG and
  forces a full reprocess.
- 1-colour icons with an opacity layer (duotone: Phosphor, Solar, IconPark,
  Material "twotone") make up ~33k of Iconify. In pure black and white the
  light layer drops out, so they are `derivable`, not `threshold`.
- SSIM of an upscaled 16 px render against 48 px punishes harmless edge blur
  (a plain square scores 0.63). For a black-and-white target, IoU of the
  thresholded images is the better legibility signal; SSIM is kept at 30 %.
- A DCT perceptual hash is unstable on perfectly symmetric shapes (many
  near-zero coefficients); a 16x16 average hash is more robust for icons.
- Icon names mix meaning ("delete") and depiction ("trash"). WordNet merges
  synonyms of the depicted object (trash can = ashcan = garbage can), but
  meaning-level grouping needs the AI pass.
- Deduplication must not drop duplicates from concept assignment: the same
  X shape named "close" in one set and "trash-can" in another is exactly the
  evidence for depiction clusters and convention strength.
- Symbol, music and pictographic-script fonts place glyphs far outside the
  hhea ascent/descent box (Noto Egyptian Hieroglyphs, Bravura). Framing a glyph
  by its drawn bounds (fontTools `BoundsPen`) plus a margin is the only
  reliable way to get a pictogram out of a font.
- The monochrome Noto Emoji font is no longer in `googlefonts/noto-emoji`; it
  lives in `google/fonts` under `ofl/notoemoji/`.

## Discovery

- Snowball discovery explodes without context filtering: mining the READMEs of
  52 "awesome" lists once yielded 58,701 GitHub links (awesome lists link to
  everything). Links are now kept only when their own line scores as
  icon/pictogram-related, and that line is stored as the candidate's note so
  triage can score it.
- Harvesting is the cheapest reliable triage for GitHub repositories: the
  median repository is < 1 MB, and "fewer than 10 SVG files" rejects tools,
  wrappers and apps that keyword heuristics let through.

## Public repository

- Actions artifacts of a public repository are downloadable by any signed-in
  GitHub user, so shards are age-encrypted on the runner (pyrage: streaming
  `encrypt_file`/`decrypt_file`, no system `age` binary needed) to a public key
  kept as a repository variable; the private key stays on the host.
- `export-shard` refuses to write plaintext when `GITHUB_ACTIONS=true`, and the
  workflow's plan job fails before any runner starts if the key is missing.
- The container can dispatch and watch workflows (`gh workflow run`, `gh run
  view`), but artifacts and job logs are served from Azure blob storage
  (`*.blob.core.windows.net`), which the proxy blocks: downloads and log reads
  happen on the host.
- Free-plan concurrency (20 jobs) queues the rest of a large matrix; the
  harvest step that took about an hour through the container's proxy takes about 2 minutes on a
  runner.
- zsh does not word-split unquoted variables: `set -- $spec` in a loop passes
  one argument (the dispatch failed with "Required input 'adapter' not
  provided").

## Wikimedia Commons

- `extmetadata` values are not always HTML strings: numbers (e.g. dates as
  floats) and per-language dicts occur. Treating them as strings aborted every
  category on its first page.
- Eight runners at once got HTTP 429 from the Commons API. Wikimedia expects a
  User-Agent with a contact URL, `maxlag`, and honouring `Retry-After`; four
  runners with 0.5 s between requests is the current setting.
- A category walk to depth 2 can reach tens of thousands of files (road
  signs); cap per source so a runner stays inside the 6 h job limit.

## SQLite at 1M rows

- A correlated-subquery `UPDATE` (dedupe) ran > 12 min holding the write lock
  and blocked every other writer; `UPDATE … FROM (grouped subquery)` does the
  same in 2 min.

## Long jobs in the dev container

- The container has a 3 GB memory limit (`cradle.json`), and Claude Code stops
  its background shells under memory pressure while the session is idle.
  Long pipeline steps run detached instead (`setsid nohup data/finish.sh &`,
  a script under the private `data/` directory) and set `oom_score_adj` to
  1000, so that if memory really runs out the kernel kills the job rather
  than the session. Progress is read from the log file.
- `pkill -f`/`pgrep -f` with a pattern that also appears in the calling
  shell's command line kills or finds that shell; use a bracket pattern
  (`[f]inish.sh`).

## Vision models on pictograms

- Zero-shot SigLIP (base) is poor on monochrome pictograms even with a local softmax over label text embeddings: 34–51 % top-1 agreement with Claude's object labels. Sigmoid scores saturate (a pawn scored 1.0 for "grail").
- DINOv2 nearest neighbour against Claude-labelled groups: 39–52 %. Visual neighbours share shape, not object.
- A linear probe (softmax regression on concatenated unit SigLIP + DINOv2 features, numpy only) trained on 1,724 Claude-labelled depictions across 77 objects: 77 % in 5-fold cross-validation, 94 % above p ≥ 0.7, 98 % above p ≥ 0.9. The cheap path is Claude on a representative sample, probe on the rest.
- SQLite lets `GROUP BY` take bare columns from the row that holds `MAX()`, which picks each depiction's largest style group without a window function.
- At full scale (16,992 examples, 396 objects) the probe's overall CV accuracy drops to 58 %, but the confidence curve holds: 94 % at p ≥ 0.7 (25 % coverage), 98 % at p ≥ 0.9 (9 %). Errors left above 0.9 are mostly near-synonyms (file/document, hearts/heart, thumb/hand).
- Coverage on groups Claude never saw is lower than CV predicts: p ≥ 0.9 set 4,843 of 134,855 unlabelled depictions (3.6 %), dominated by generic shapes (arrow, letter, face, bubble, cloud). The assessed sample is not representative of the long tail.
- GitHub Models (free inference with the workflow token) was retired on 2026-07-30. Its endpoint now redirects a POST to a page that answers `200 text/plain: OK`; httpx does not follow redirects by default and `raise_for_status()` passes a 3xx, so the first symptom was an empty non-JSON body. Check a service's current status before building on it.
- `timm` on a runner pulls the CUDA torchvision from PyPI next to CPU torch (`operator torchvision::nms does not exist`): install torch and torchvision together from the CPU index.
- Pictogram names describe meaning more than the drawing: for 62 % of 2,000 Claude-assessed depictions, Claude's object is not among the WordNet senses of the names' object words ("users" → silhouette, "lamp" → light bulb, "candle" → fire, "video off" → camera). Name rules match Claude on 30 %. Text classification (Laya) can only choose among what the names offer, so it fits meanings, not drawn objects.
- Laya (0.3.22): a choice question's options share one `head_max_len` budget (default 192 tokens); raise it per request for 8 glossed candidates, or shortlist first (`laya.shortlist`).
- Laya zero-shot on names → drawn object (2,000 Claude-assessed depictions, run 36782921650, ~15 min on a CPU runner): 28 % agreement with Claude against 30 % for the name rules; on the 643 items with Claude's object among several candidates, 69 % against the rules' 73 %. Its p ≥ 0.9 answers (20 % of items) were only 37 % right, so its calibration does not carry over to this task. Not adopted; fine-tuning on Claude's labels would still be capped by the 38 % the names cover.
- Vision benchmark (500 Claude-assessed pictograms, CPU runners, runs 36772275690 / 36773311723): agreement with Claude, exact / with near matches: Qwen3-VL 4B 57 / 63 % (85 s per image), Gemma 3 4B 52 / 57 % (163 s), Qwen2.5-VL 3B 39 / 45 % (89 s), OmniParser icon captions 19 / 24 % (0.1 s; it names functions, not drawings), Moondream 2 2 % (mostly empty or "coffee cup"). Chance level (always "cup") is 6 % near. The Ollama models only got through 127–244 images before the 350-minute job limit.
- In 16 Qwen3-VL/Claude disagreements read by eye, each was right in 6, and 4 were ties or both wrong. Claude's labels have clear errors too ("phone off" → cane, "star off" → airplane, a pixel microphone → wave), so agreement with Claude understates a good model.
- A benchmark scorer must strip filler words and look at every word: models answer "folder icon", "cloud upload", "arrow left". Checking only the last word turned 57 % into 43 %.
- Ollama (Qwen3-VL) scales every image to about 1024 × 1024: the prompt is 1,083 tokens whether the render is 256, 128 or 64 px, and reading it is the whole cost on a 4-CPU runner (35–81 s; the answer itself takes 0.1–0.3 s). Differences between variants are runner CPU noise; `num_thread=4` alone took 85 s down to 55 s. Agreement with Claude did not drop at 64 px, and Qwen3-VL 2B matched 4B (52 vs 48 % on the same 50). To go faster, cap the image tokens (llama.cpp), not the render size.
- llama.cpp (b11319) with `--image-min-tokens/--image-max-tokens` runs Qwen3-VL on a 4-CPU runner at 2.5 s per image (2B, 64 image tokens) to 10 s (256 tokens), against 55 s through Ollama, at similar agreement with Claude on 50 images (40–48 % vs 48 %). `llama-server -hf Qwen/Qwen3-VL-<n>B-Instruct-GGUF` fetches the model and its mmproj by itself.
- Image tokens are not why Ollama beats llama.cpp on Qwen3-VL 4B: on the same 244 images llama.cpp reaches 50 / 52 / 54 / 55 % at 64 / 256 / 576 / 1,024 image tokens (4.5 / 10.5 / 43 / 79 s), while Ollama reaches 63 % at the same 1,083-token prompt. The difference lies in Ollama's quantization or chat template; more tokens buy 5 points for 17× the time.
