# handdown — pictogram catalog: design

Date: 2026-09-28
Status: draft for review

## 1. Purpose

Build an extensive, all-encompassing **reference and research catalog of
pictograms** gathered from every reachable source. The catalog is the idea
base for a later, separate project: designing one consistent pictogram set,
possibly shipped as a font.

Every pictogram is recorded with its source and full metadata, grouped by
**meaning** and, within a meaning, by **depiction**, and rated for how well
it works as a small monochrome symbol.

### Inclusion criteria

A pictogram is catalogued when it

- is a vector graphic, or can be turned into one (font glyph, traceable raster);
- is monochrome, or has a monochrome version that can be derived;
- is meant to be discernible at font size or as a small menu-bar icon
  (this is measured, not assumed — see §5).

Items that fail a criterion are still stored locally but flagged and
filtered out of default views.

### Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Purpose | Research catalog now; idea base for a consistent set / font later |
| Local copies | Copy **everything** locally, regardless of license; `data/` is private and never committed |
| Source breadth | Everything: icon sets, standards and signage, AAC systems, fonts and Unicode, raster sources auto-traced — "go everywhere" |
| Approach | Aggregator-first harvest, per-source adapters, snowball discovery |
| Grouping | Two axes: concept (meaning) → depiction cluster |
| Rating | Hybrid: automated metrics plus blind AI judgement per depiction cluster, with manual override |
| Interface | Obsidian-compatible vault, plus a static HTML export generated from the same data |
| Monochrome | Pure black and white; no greys |
| Code license | AGPL-3.0-or-later |
| Remote | Existing GitHub remote |

## 2. Architecture overview

```
 sources.yaml ─┐
 search seeds ─┼─> discover ─> triage ─> harvest ─> normalize ─> render
 link mining  ─┘      ▲                                   │
                      └──────── new platforms/links ◄─────┘
                                                          ▼
          export vault/html ◄─ rate ◄─ cluster ◄─ conceptualize
                   ▲
                   └── sync (overrides, notes) ◄── vault edits
```

- **SQLite is the single source of truth.** The vault and the HTML are
  generated from it. The only fields edited by hand are `overrides:` and
  `notes:`, and `sync` reads those back.
- Every stage is idempotent and resumable. Its state lives in the database,
  and `handdown status` reports counts and errors per stage and source.
- A CLI, `handdown <stage> [...]`, drives everything.

## 3. Data model

### Tables

**platform**: where things are hosted or come from.
id, name, url, kind (aggregator | code-host | wiki | standards-body |
foundry | museum | company | community | other), api_url, notes,
first_seen, found_via.

**source**: one set or collection.
id, platform_id, name, url, license_spdx (or `unknown` / `proprietary` /
`custom:<text>`), license_url, author, designer, year, version,
accessed_at, found_via (source id | platform id | search_log id),
harvest_status (candidate | accepted | rejected | harvested | failed),
adapter, stats (JSON), stars/downloads (JSON), system (e.g. "ISO 7010"),
region, domain.

**pictogram**: one graphic.
- Identity: id, source_id, original_id, original_name, original_url,
  raw_path, norm_path, sha256, phash, duplicate_of.
- Format: format (svg | glyph | raster | other), traced (bool),
  color_class (native | derivable | threshold | none), svg_valid, uses
  (JSON: transforms, masks, clip paths, strokes, text), fill_rule,
  font_ready (bool), file_size.
- Form: viewbox, grid_size, style (outline | filled | duotone | mixed),
  stroke_width, corner_radius, stroke_caps, padding_ratio, symmetry
  (h / v / rotational), mirror_safe, components, holes, figure_ground_ratio,
  container_shape (none | circle | triangle | square | other), perspective
  (front | profile | three-quarter | other), node_count, path_count.
- Content: has_text, has_numerals, has_person, person_gendered,
  negation (none | slash | cross | check).
- Provenance: designer, year, lineage (free text or pictogram id),
  standard (number and edition), regulatory_status (standard | recommended
  | informal), deprecated_by, unicode_codepoint.
- Raw metadata: raw_tags, raw_categories, raw_description (JSON, verbatim
  from the source).
- harvested_at.

**concept**: a meaning.
id, wikidata_qid, wordnet_synset, labels (JSON, lang → label), domain,
parent_id, referent_type (object | action | state | place | abstract |
instruction).

**pictogram_concept**: pictogram_id, concept_id, confidence, method
(tag-match | dictionary | ai | manual).

**depiction_cluster**: id, concept_id, description, representative_id,
representation (iconic | indexical | symbolic | metonymic),
metaphor_chain, convention_strength.

**semantic_meta**: the AI-assessed attributes, per cluster (pictograms
inherit them): concreteness, semantic_distance, ambiguity (count plus the
list of alternative meanings), cultural_risk (JSON: region → note),
timelessness, anachronism (bool), model, run_id, assessed_at.
These are **metadata, not ratings**. They are shown and filterable but never
feed the combined score.

**rating**: pictogram_id, metric (see §5), value 0–100, method,
method_version, model, run_id, computed_at, is_override.

**confusion_pair**: concept_a, concept_b, cluster_id, evidence (AI blind
guess | embedding distance), strength.

**search_log**: id, query, engine, seed (source or platform id), run_at,
candidates_found, notes.

**harvest_error**: source_id, item, stage, error, at.

**ai_run**: id, job, model, input_tokens, output_tokens, cache tokens,
cost estimate, log path. Every entry is also logged via `headless-log.mjs`.

### Files

```
data/                      # private, gitignored — ALL local copies
  raw/<source>/...         # originals, untouched
  norm/<sha>.svg           # sanitized, monochrome, unified viewBox
  png/<size>/<sha>.png     # 8, 12, 16, 24, 32, 48, 64 px
  embed/                   # float16 embedding matrix + index
  cache/http/              # HTTP cache
catalog.sqlite             # gitignored (contains data from any license)
vault/                     # generated, gitignored by default
site/                      # generated HTML, gitignored
```

### Deduplication

An identical sha256, or a near-identical phash after normalization, is
linked through `duplicate_of`. The **original** publisher is the source;
aggregators (Iconify and similar) count as platforms. Cross-source
duplicates stay visible because they feed convention strength (§5).

## 4. Pipeline

### 4.1 discover

Fills the candidate queue (`source.harvest_status = candidate`). Inputs:

- **Link mining**: READMEs, "alternatives" and "similar projects" sections,
  awesome-lists, and site link pages of every harvested source.
- **Search seeds**: templated queries built from registry entries —
  platform × domain × language (e.g. `pictogram set site:github.com`,
  `Piktogramme SVG`, `ピクトグラム SVG`). Each query is logged in
  `search_log`. Newly found platforms are added to the seed pool, so the
  sources become the base for new searches.
- **Hub crawling**: Wikimedia Commons categories, Iconify collection list,
  GitHub topics (`icons`, `pictograms`, `svg-icons`, `icon-font`),
  npm/PyPI icon packages, Google Fonts, font listings, Unicode data.

Every candidate records `found_via`.

### 4.2 triage

Heuristics sort each candidate into accept / reject / needs-adapter:
vector available, monochrome plausible, size, overlap with harvested
sources. Ambiguous candidates are listed for the operator (`handdown triage
--review`). Licensing does not block acceptance, because copies are private.

### 4.3 harvest

Adapter interface: `discover_items() → fetch(item) → metadata(item)`.
Planned adapters:

1. **git-svg**: generic repository containing SVG directories (covers most
   sets). Metadata comes from the directory structure, a metadata
   JSON/YAML if present, and LICENSE detection.
2. **iconify**: the Iconify JSON collections (about 200 sets). Original
   authors and licenses come from each collection's `info`.
3. **commons**: the Wikimedia Commons API over categories. Per-file license,
   author and description come from `extmetadata`.
4. **arasaac**: the ARASAAC API.
5. **openmoji**: outline and black variants.
6. **noun-project**: API. Needs an operator-provided key; skipped if absent.
7. **font**: extracts glyphs to SVG with fontTools (icon fonts, dingbats,
   Unicode symbol fonts such as Noto Sans Symbols).
8. **raster**: downloads, then traces in `normalize`.
9. **generic-web**: a page with SVG links, for small one-off sources.

Politeness: rate limit per host, HTTP caching, `robots.txt` respected, and a
User-Agent with project URL and contact. Per-item failures go to
`harvest_error` and never abort a source.

### 4.4 normalize

1. Parse with defusedxml. Strip scripts, event handlers, external
   references, `foreignObject`, embedded raster data (kept separately for
   traced items).
2. Classify color: `native` (one color) / `derivable` (separable color
   shapes flatten without merging) / `threshold` (gradients or greys need
   thresholding; lossy, flagged) / `none` (illustration). Output is pure
   black shapes only; greys are never kept.
3. Convert strokes to fills (picosvg) and resolve transforms, clip paths
   and masks where possible. Set `font_ready`.
4. Unify the viewBox to a square with the original padding recorded.
   Optimize with scour.
5. Trace rasters with potrace (`traced = true`).
6. Extract form metadata: style, stroke width, symmetry, components,
   holes, container shape, has_text (text elements or OCR on the 64 px
   render), and so on.

### 4.5 render

resvg renders each normalized SVG to PNG at 8/12/16/24/32/48/64 px, black
on white, plus a 1-bit (thresholded) 16 px variant for the pure
black-and-white view.

### 4.6 conceptualize

1. Dictionary pass: names, tags and categories → WordNet synsets and a
   Wikidata QID lookup (cached). Standard systems map through their
   official meaning tables where available.
2. AI pass for unresolved items, batched as headless runs: given name, tags
   and source context, the model proposes a concept (QID) with confidence.
3. Multilingual labels come from Wikidata.

### 4.7 cluster

open_clip ViT-B/32 embeddings of the 64 px renders, then HDBSCAN within
each concept to form depiction clusters, with the item nearest the medoid
as representative. Cluster descriptions ("trash can", "X mark") come from
a headless AI run on the cluster's contact sheet.

### 4.8 rate

See §5.

### 4.9 export / sync

See §6.

## 5. Rating

All scores are 0–100 and stored with method and version, so they can be
recomputed.

### Scored metrics

| Metric | Method | Scope |
|---|---|---|
| `meaning` | AI blind guess of the meaning from a 16 + 48 px contact sheet, graded against the concept | cluster, inherited |
| `depiction` | AI blind guess of what is depicted, graded against the cluster description | cluster, inherited |
| `familiarity` | AI judgement of how commonly this depiction is encountered for this meaning | cluster, inherited |
| `convention` | share of independent sources (deduplicated) that use this depiction for the concept | cluster |
| `distinctiveness` | embedding distance to the nearest pictogram of a *different* concept, adjusted by confusion pairs from the blind tests | pictogram |
| `legibility` | SSIM of 8/12/16/24/32 px renders (upscaled) vs 48 px, stored as a curve plus a summary value; minimum feature size (px) at 16 px; share of anti-alias "mud" pixels at 16 px | pictogram |
| `simplicity` | node, path and component count, perimetric complexity, PNG compression ratio, edge density; combined as a percentile across the catalog | pictogram |
| `balance` | distance between the optical centroid and the viewBox center | pictogram |
| `consistency` | how easily it fits a target grid and style: stroke-width variance, off-grid coordinates, corner-radius variance | pictogram |

Inherited cluster scores are adjusted per pictogram by its legibility
difference from the representative.

### Combined score

A weighted mean. Weights live in `config.toml` and can be changed without
recomputing anything. Defaults: meaning 25, depiction 15, familiarity 10,
convention 10, distinctiveness 10, legibility 15, simplicity 10,
balance 2.5, consistency 2.5.

### Explicitly not scored

Silhouette strength, contrast robustness, and any comparison with ISO 9186
thresholds are out of scope. Concreteness, semantic distance, ambiguity,
cultural risk and timelessness are **metadata only** (§3 `semantic_meta`).

### AI run discipline

- One headless run per cluster, not per pictogram. The run gets the contact
  sheet and no names, so the guesses are blind.
- `claude -p --model sonnet --tools ""` in an `xargs -P 4` loop, with a
  quota check before each launch, a stop file at the threshold, `flock`
  around database writes, and every run logged via `headless-log.mjs` and
  in `ai_run`.
- `handdown rate --ai --domain <d> | --top <n> | --forecast` rates on
  demand; the full catalog is never rated implicitly.

### Overrides

Any metric, semantic_meta field, concept assignment or cluster membership
can be overridden in the vault frontmatter. Overrides always win and are
marked `is_override`.

## 6. Vault and HTML export

### Vault (Obsidian-compatible, plain CommonMark + wikilinks)

```
vault/
  concepts/<domain>/<label>.md
  sources/<source>.md
  platforms/<platform>.md
  systems/<system>.md
  styles/<style>.md
  _index/ top-rated.md, confusions.md, triage.md, errors.md, stats.md
  _media -> ../data/png
```

**Concept note**
- Frontmatter: QID, labels, domain, referent type, and `overrides:` /
  `notes:` (the only hand-edited parts).
- Body: per depiction cluster, a contact sheet (representative at
  12/16/24/48 px, 1-bit 16 px), convention %, semantic metadata, a variants
  table (preview, source link, license, style, scores), and confusion pairs
  as wikilinks.

**Source / platform notes**: license, author, found_via chain, search
history, stats, errors.

`handdown sync` reads `overrides:` and `notes:` back. Regeneration keeps
them. Notes that were renamed or deleted in the vault are reported, never
silently dropped.

### HTML (`handdown export html`)

A static site rendered from the database (it includes `notes:`), with no
server.
- Client-side search (MiniSearch) and filters: source, platform, license,
  style, domain, color class, font_ready, score ranges, semantic metadata.
- Concept pages that mirror the notes.
- **Comparison view**: all depictions of a concept side by side at a chosen
  size, pure black on white, with a 16 px menu-bar mock-up toggle.
- Sharded JSON indexes and lazy loading, so hundreds of thousands of items
  stay usable.
- Only sanitized SVGs and PNGs are included.

## 7. Cross-cutting concerns

**Resources** (estimated for about 500k pictograms): raw about 1 GB, PNG
about 3 GB, embeddings about 1 GB (float16). CPU embedding takes hours. AI
rating is the dominant cost at roughly 20–40k clusters, so it is on demand
with a forecast.

**Security**
- All downloaded SVGs are untrusted: sanitized before rendering or export.
- defusedxml is used, so no XXE.
- Downloads have size limits and archives are safely extracted (no path
  traversal).
- Keys (Noun Project, GitHub) come from the environment only.

**Dependencies**: Python 3.12, uv, Typer, SQLModel + Alembic, httpx +
hishel, tenacity, defusedxml, picosvg, svgelements, scour, fontTools,
potrace (pypotrace or the CLI), resvg, Pillow, scikit-image, open_clip +
torch (CPU), hdbscan, nltk (WordNet), Jinja2, MiniSearch (vendored JS).
Library quirks go to `dev/takeaways.md`.

**Compatibility**: normalized SVG is pure fill and SVG-1.1-compatible,
suitable for font tooling later. The vault is plain Markdown. The HTML
works offline.

**Redundancy**: deduplicated via sha256 and phash; the original publisher
is canonical.

**Localization**: concept labels come from Wikidata (multilingual). CLI
messages, vault templates and HTML UI strings use gettext catalogs: en and
de first, then common languages via headless translation runs.

**Project files**: `README.md` (with the AI disclosure) plus
`docs/i18n/README.<bcp47>.md`, `LICENSE` (AGPL-3.0-or-later),
`dev/takeaways.md`, and a codegraph.

## 8. Testing

- pytest. Adapters are tested against recorded HTTP fixtures (respx
  cassettes); tests never touch the network.
- normalize: golden files, including hostile SVGs (script, XXE, external
  href, huge path) and odd ones (strokes only, masks, greys, gradients).
- Metrics: hand-built pictograms with known properties (symmetry, component
  count, hairlines).
- Vault: a round trip test — overrides and notes survive regeneration, and
  deletions are reported.
- A small end-to-end run over a fixture corpus of about 50 pictograms from
  3 sources.
- CI on GitHub Actions: ruff, mypy, pytest.

## 9. Phases

1. Core: DB schema, CLI skeleton, `sources.yaml` registry, git-svg and
   iconify adapters, normalize, render, status.
2. Metrics (legibility, simplicity, balance, consistency), conceptualize
   (dictionary pass), cluster, vault export and sync.
3. Discovery snowball and further adapters (commons, arasaac, openmoji,
   font, raster, generic-web, noun-project).
4. AI passes (concepts, cluster descriptions, blind ratings, semantic
   metadata), convention and distinctiveness, HTML export.
5. Later: a comprehension test mode in the HTML (human answers as real
   comprehension data).

Each phase gets its own implementation plan.

## 10. Open points

- No GitHub remote is configured in the container yet (`gh` is not logged
  in). The operator needs to provide one before pushing.
- Noun Project API key: optional, operator-provided.
- README contents: the operator decides which features to showcase and
  which donation designations apply.

## 11. Implementation notes (deviations from this design)

Recorded while building phases 1–4 on 2026-09-28/29.

| Design | Implemented | Why |
|---|---|---|
| SQLModel + Alembic | plain `sqlite3` + `schema.sql` (`CREATE … IF NOT EXISTS`) | fewer dependencies; the schema is small |
| open_clip embeddings + HDBSCAN | 16×16 scale-normalized ink map + filled silhouette, average-linkage clustering (scipy) | no model weights reachable (Hugging Face blocked), ~750 MB free RAM; `cluster.prepare` is the swap point |
| Wikidata QIDs for concepts | WordNet synsets (NLTK, OMW labels in ~30 languages); `term:` concepts for unknown phrases | wikidata.org blocked; `wikidata_qid` column kept for later |
| picosvg stroke→fill conversion | not yet; `font_ready` flags pictograms without strokes, masks or text | phase for the font project |
| potrace for raster sources | not yet | all raster sources found so far are on blocked hosts |
| Jinja2 + MiniSearch | inline templates + vanilla JS filter | no CDN; 50k entries filter fast enough client-side |
| Commons, ARASAAC, OpenSymbols, BCI adapters | sources recorded as `blocked-network` | proxy allowlist; GitHub mirrors used where they exist (ISO 7001/7010, GHS, road signs via karlnorling/*) |
| Rating by blind AI test | `handdown ai --limit N`: blind call + informed call per batch of 12 clusters | usage limits are not visible from the container, so runs are bounded and logged in `ai_run` |
| Harvest | tarball adapters also **triage by content** (reject < 10 SVGs) and delete the tarball after extraction | disk budget |
