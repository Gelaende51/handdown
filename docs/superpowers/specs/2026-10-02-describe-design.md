# Text, tags and descriptions per pictogram (Claude)

## Goal

Record the text and characters drawn inside a pictogram and make them
searchable; capture as much visual information as possible as tags from the
standard vocabulary (`docs/superpowers/specs/2026-10-02-tags-design.md`);
keep what no tag can say as residual free text; and add an expressive
description and interpretation of the whole composition.

## Per pictogram (Claude Sonnet, reasoning off, sheets of 10, names given as context)

- **text**: one entry per separate run of text or characters, with script
  (latin, han, hiragana, katakana, hangul, arabic, cyrillic, greek, digits,
  symbols, other) and position. Each text group is a compound part of the
  pictogram (`pictogram_text.group_no`), shown as "text part n".
- **tags**: only `namespace:value` from the vocabulary: direction (where the
  main figure faces or points), view, corners, ends, frame, style, count,
  figure, pose, and `feature:<noun>` for each visible element. Features are
  normalised like all others; a tag outside the vocabulary is moved to the
  residual text.
- **residual**: visible details no tag can say.
- **description** (the whole composition: what, arrangement, drawing) and
  **interpretation** (what it conveys).

Stored in `pictogram_text`, `pictogram_description` and the tag table (also
`described`, `feature:text`, `text:<script>`); full-text search over name,
text, description, interpretation and residual (`pictogram_search`, FTS5);
every answer is logged in `classification`.

## Scope

A sample first, for browsing and judging: a third pictograms with detected
text, a third composites, a third random (unique, on topic, not extracted
parts). `handdown describe --sample N`. Review app: image pages show text
parts, tags, residual, description and interpretation; `/search`; the tag
filter's `described` tag selects the sample.
