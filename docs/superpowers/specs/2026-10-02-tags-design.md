# Tags: standardised attributes for filtering

## Why

Recurring attributes came from three places in three forms: measurements
(style, frame shape, symmetry, stroke caps), name rules (direction words,
view words, a few variety words) and Claude's free-text features (7,483
distinct strings: "small badge", "small badge icon", "small badge
duplicate" …). Corner rounding was not measured at all, and "facing left"
was only known where a name said "left".

## Vocabulary (`tags.py`)

`namespace:value`, one vocabulary for every origin:

| namespace | values | from |
|---|---|---|
| style | outline, filled, mixed, duotone | measurement |
| frame | circle, square, triangle, octagon, diamond, shield, hexagon | measurement, composite frame parts, features |
| symmetry | horizontal, vertical, rotational | measurement |
| mirror-safe | (flag) | measurement |
| ends | round, square, butt | stroke caps |
| corners | rounded, sharp, mixed | vector outlines: junctions turning more than 35° are sharp corners; curves and arcs are rounded |
| format, color | svg, glyph, raster; native, derivable, threshold | catalog |
| view | front, side, top, … | depiction |
| direction | left, right, up, down | name rules (points-…), later Claude and the vision model ("facing") |
| feature | badge, slash, steam, check, cross, plus, wheel, … | name rules, composite parts, Claude's features |

Free text becomes a feature by dropping size and filler words ("small",
"icon", "duplicate"), merging synonyms ("x mark" = cross, "circle outline" =
frame:circle) and singularising. Free-text features on fewer than 20
pictograms stay out of the filters; the standard vocabulary always stays.

## Data and UI

`pictogram_tag (tag, pictogram_id)` and `tag_count (tag, n)`, rebuilt by
`handdown tags` for every unique, on-topic pictogram (depiction tags apply to
the depiction's pictograms). The review app's `/browse` filters by any
combination of tags, optionally within a source, symbol or idea, with counts
per tag within the selection (catalog counts above 50,000 pictograms).

## Later

"Facing left/right" for objects other than arrows needs the image: it is to
be asked in the Claude refinement and the vision-model labelling.
