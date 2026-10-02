# Composite parts as pictograms, linked to their depictions

## Goal

Every composite links to the depiction of each part it contains ("bell-off"
→ the bell depiction and the slash depiction), and every part exists as a
pictogram of its own, so it is grouped, rated and browsable like any other.
Decisions (2026-10-02): all composites, generic and artistically unique;
extracted parts are full pictograms, marked; parts are cut out by rules and,
where parts overlap, also by AI, both kept for comparison.

## Extraction

1. **Rules (every part, reference for the AI path):**
   - *Vector*: the composite's SVG is split into elements and subpaths
     (`M … Z`). Each piece is rendered and assigned to the part whose region
     it covers most (part regions come from the shape analysis: connected ink
     areas, slash, frame, corner badge). A part whose pieces render to most of
     its region becomes an SVG pictogram made of just those pieces.
   - *Mask*: otherwise the part's connected ink area is cut out of the 1-bit
     render as a raster pictogram (`format = 'raster'`).
   - Parts that share ink with another part (crossing slash drawn in the same
     path, badge cutting into the base) and cannot be separated get no rule
     extraction; they are recorded with `extraction = 'not separable'`.
2. **AI (parts that overlap: crossing, cutout, touching, over, merged):** Claude
   (Sonnet, reasoning off: as good as Opus on this catalog's images,
   `docs/vision-benchmark.md`) gets the composite image, its SVG path data and
   the part to extract, and returns the part as SVG path data (removing the
   other parts and closing the gaps they cut). Batched per sheet, paced by the
   usage limit (exit 75), piloted on a sample before it runs at scale.

## Data

- `pictogram.derived_from` (composite pictogram id), `pictogram.part_no` and
  `pictogram.extraction` (`vector` | `mask` | `ai`): extracted parts are
  pictograms of the virtual source `derived:composite-parts`; their
  `original_id` is `<source>/<original id>#part<n>[/ai]`, so they survive
  rebuilds; their name is the part's label.
- `composition_part.depiction_id`: the depiction of the part, taken from the
  extracted pictogram's style group (AI extraction preferred when both
  exist), else, for non-separable parts, the most frequent depiction of the
  part's concept.
- Every extraction and link is logged in `classification` (`field =
  'part'`), with method and model.

## Pipeline

`handdown extract-parts` (rules, local, workers) → `process` → `concepts
--only-missing` → `hierarchy` (incremental) → `link-parts`. `extract-parts
--ai --limit N` for the AI path. The vault and site list "Contains: …" on a
composite and "Part of: …" on a depiction.

## Impact

- Functionality: composites become navigable by their parts; the depiction
  hierarchy gains pictograms of parts that otherwise appear only inside
  composites.
- Resources: up to ~450,000 part pictograms; rule extraction runs locally;
  the AI path costs quota (pilot first: cost per part, success rate).
- Counts and ratings: extracted parts are marked and can be left out of
  catalog counts and rating percentiles.
