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
