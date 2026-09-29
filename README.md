# handdown

A research catalog of pictograms from as many sources as can be found: icon
sets, standards and signage, AAC symbol systems, fonts and Unicode, map
symbols. Every pictogram is recorded with its source and metadata, grouped by
**meaning** and by **depiction**, and rated for how well it works as a small,
pure black-and-white symbol. It is the idea base for designing a consistent
pictogram set (possibly a font) later.

**AI disclosure**
- Agent: Claude Opus 5.5 (`claude-opus-5-5`) by Anthropic, via Claude Code. It
  wrote the code, the documentation and the source research in collaboration
  with the author. Tokens used: roughly 0.3 M in context over the first
  session; this figure is updated per session.
- Author: design decisions, requirements and review.

Translations: [Deutsch](docs/i18n/README.de.md)

## What it does

- **Collect**: adapters for Iconify (about 240 sets via npm), GitHub
  repositories and npm packages. Every platform, source, search query and
  "found via" link is recorded, so the discovery trail is auditable.
- **Discover**: snowball search. GitHub and npm searches are built from
  domains × languages × platform vocabulary. READMEs and awesome lists of
  known sources are mined for further sources. Web searches go into the same
  log.
- **Normalize**: every SVG is sanitized (scripts, external references and
  entities are removed), squared, and turned into pure black and white. Its
  colour class is recorded: native, derivable (incl. duotone), threshold, or
  none.
- **Measure**: legibility at 8–32 px, minimum stroke, counters that close up
  at 16 px, simplicity, optical balance, grid consistency, symmetry,
  components, holes, container shape, style.
- **Group**: meanings come from names and aliases via WordNet (with labels in
  about 30 languages). Depictions are clustered by shape, independent of
  outline or filled style.
- **Rate**: convention strength (how many independent sources draw a meaning
  this way), distinctiveness from other meanings, and a weighted combined
  score. You can adjust the weights, and hand-set overrides always win.
- **Browse**: an Obsidian vault (one note per meaning; your `overrides:` and
  `notes:` survive regeneration), and a static HTML catalog with size and
  1-bit previews and a menu-bar mock-up.

## Installation

```sh
uv sync
NLTK_ALLOW_PROXIED_URLOPEN=1 uv run python -c "import nltk; [nltk.download(p, download_dir='data/nltk') for p in ('wordnet','omw-2.0')]"
```

## Usage

```sh
uv run handdown init                 # database + sources.yaml registry
uv run handdown harvest iconify      # ~390k pictograms
uv run handdown discover             # find new sources on GitHub/npm
uv run handdown triage               # accept/reject candidates (reasons in notes)
uv run handdown harvest git-svg      # accepted GitHub sources
uv run handdown process --workers 4  # normalize, render, measure
uv run handdown dedupe
uv run handdown concepts && uv run handdown cluster && uv run handdown score
uv run handdown export vault         # vault/ (Obsidian)
uv run handdown export html          # site/ (serve with: python -m http.server -d site)
uv run handdown status
```

All harvested graphics stay in `data/`, which is private and never committed.
Sources keep their own licenses. The catalog records each license, but
recording it doesn't make a graphic redistributable.

## Related projects

- [Iconify](https://iconify.design): an icon framework and aggregator. It is
  handdown's largest input, but it has no meaning grouping or rating.
- [OpenSymbols](https://www.opensymbols.org): an aggregator of AAC symbol
  libraries. It is AAC-only and searches by keyword.
- [pictograms.info](https://www.pictograms.info): a reference on pictogram
  systems and standards. It is an editorial site, not a dataset.
- [The Noun Project](https://thenounproject.com): a very large pictogram
  community. It is proprietary and offers no cross-source comparison.

## Project

- License: [AGPL-3.0-or-later](LICENSE) (code only; harvested pictograms keep
  their own licenses)
- Design: [docs/superpowers/specs](docs/superpowers/specs/2026-09-28-pictogram-catalog-design.md)
- Notes on libraries and quirks: [dev/takeaways.md](dev/takeaways.md)
- Parallel harvests on GitHub Actions: [docs/actions.md](docs/actions.md)
- Issues: the project's GitHub issue tracker (once the repository is published)
