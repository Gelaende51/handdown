# Composite pictograms: design

Date: 2026-09-29
Status: approved 2026-09-29 (with relative sizing)
Extends: [pictogram catalog design](2026-09-28-pictogram-catalog-design.md)

## Purpose

Many pictograms combine simpler ones: a bell with a slash ("off"), a user
with a small plus ("add user"), a house in a circle, two stacked documents
("copy"). At least 18 % of the catalog carries a modifier in its name, and
the shape analysis finds about 108k framed and 26k slashed pictograms.

For the later consistent set (possibly a font), the catalog records:

- which **elements** a composite is made of;
- which **operators** join them (negation, frame, repetition, modifier, …);
- how the parts are **arranged** (above, over, touching, merged, …);
- whether the combination is **generic** (reproducible from standard parts)
  or **artistically unique**;
- which **implementation** a font would use: combining mark, ligature,
  sequence, or a unique glyph.

It also establishes which combinations work in one glyph, which are
impossible or contradictory, and which are better written as a sequence of
glyphs.

## Decisions

| Topic | Decision |
|---|---|
| Frames | **Always meaning**: every frame is an operator. A framed and an unframed house are different composites. The frame shape is recorded (circle, square, rounded square, triangle, octagon, diamond, shield, speech bubble, other). |
| AI | Rules (names + shape) for all pictograms. AI only where name and shape disagree, plus a sample for the generic/unique judgment and to measure rule accuracy. |
| Grouping | Many-to-many: a composite is listed under every element it contains. |

## Model

### Roles of parts

| Role | Examples | Meaning |
|---|---|---|
| base | bell in "bell-off" | carries the main meaning |
| negation | diagonal slash, X over, prohibition circle-slash | not / off / forbidden |
| frame | circle, square, triangle, octagon, shield, bubble | enclosure; sign class |
| modifier | small plus, check, x, clock, lock, dot, arrow | action or state applied to the base |
| repetition | 2x (duplicate/copy), 3+ (plural/group) | quantity |
| partner | two equal elements (car + house) | free combination |
| text | letters, digits, labels | label or quantity (localization flag) |
| decoration | motion lines, rays, sparkles | emphasis or motion |

### Relations between two parts

`above`, `below`, `left_of`, `right_of` (separate, with a gap);
`over` / `under` (overlapping, with front/back order); `touching`;
`merged` (shared strokes, not separable); `cutout` (the modifier knocks a
gap into the base); `inside` / `surrounds`; `crossing`;
`corner:{tl,tr,bl,br}`; `sequence` (A → B); `mirrored`, `rotated`.

### Relative sizing

Every part records its size relative to the base and to the whole glyph:

- `area_ratio`: ink area of the part / ink area of the base;
- `extent_ratio`: longest bounding-box side of the part / that of the base;
- `glyph_share`: longest bounding-box side of the part / the glyph's ink box;
- `px16`: the part's longest side in pixels at a 16 px render.

Size classes derived from `extent_ratio`: **dominant** (> 1.25), **equal**
(0.8–1.25), **minor** (0.4–0.8), **tiny** (< 0.4).

Sizing carries meaning and legibility:

- big + small of the same element reads as parent/child or before/after;
  equal sizes read as peers (partners), which is why they compete at 16 px;
- a modifier smaller than about 5 px at 16 px stops being recognizable; the
  analysis measures the smallest recognizable modifier size per modifier;
- frames are sized relative to their content (padding inside the frame);
- repetitions record whether copies are equal (plural) or diminishing
  (stack/depth).

### Generic vs unique

- **generic**: every part is a standard element, joined by a standard
  operator, and separable in the drawing.
- **unique**: parts are fused (`merged`), stylized beyond their standalone
  form, or the combination only works in this drawing.

### Font implementation type

| Type | When | Unicode precedent |
|---|---|---|
| `mark` | negation or frame over any base | U+0338 combining long solidus overlay; U+20DD/20DE/20DF enclosing circle/square/diamond; U+20E0 enclosing circle backslash (prohibition); U+20E4 enclosing upward triangle (warning) |
| `ligature` | base + corner modifier, repetition | — |
| `sequence` | free combinations of equal partners; falls back to glyphs written one after the other | emoji ZWJ sequences |
| `unique` | artistically unique composites | own glyph |

## Detection

1. **Names** (all pictograms): split name and aliases (existing tokenizer),
   then map tokens through an operator vocabulary: off/slash/disabled/no →
   negation; circle/square/box/badge/shield/… → frame; plus/add, minus,
   check, x/close, clock, lock, star, heart, alert, question, … → modifier;
   multiple/stack/copy/duplicate/group → repetition. The remaining tokens
   are the base element(s) and resolve to concepts as before.
2. **Shape** (all measured pictograms, from the 64 px render):
   - connected components, and their bounding boxes, areas and centroids;
   - repetition: components (or the whole ink) that match each other after
     translation (IoU ≥ 0.85 of normalized masks);
   - slash: a long thin component (or ink along the diagonal) crossing
     ≥ 60 % of the bounding box at 30–60°;
   - frame: the existing container detection, extended by shape labels;
   - corner modifier: a component ≤ 30 % of the ink bounding box in one
     corner, and whether a gap surrounds it (cutout);
   - pairwise relations from bounding boxes and mask overlap: gap → beside;
     overlap → over/under; distance 0 → touching; a single component whose
     parts are named → merged;
   - text: the existing `has_text`.
3. **Merge**: name and shape evidence are combined. They agree → confidence
   high. They disagree (name says "off", no slash seen) → `conflict`, queued
   for the AI pass.
4. **AI** (headless, batched like the rating): a contact sheet plus a
   structured answer (parts, roles, relations, generic/unique, font type).
   It covers all conflicts and a sample of about 2,000 composites across
   operators; the sample measures rule precision per operator.

## Storage

- `composition` (pictogram_id PK, kind generic|unique, font_type,
  confidence, method rules|ai|manual, conflict flag, notes)
- `composition_part` (pictogram_id, part_no, role, concept_id nullable,
  shape_label, position, count, area_ratio, extent_ratio, glyph_share, px16,
  size_class)
- `composition_relation` (pictogram_id, part_a, part_b, relation)
- `operator` vocabulary (id, role, names, unicode_mark, description)

Vault overrides use the same keys (`pictogram:<id>: {composition: …}`).

## Organization in vault and HTML

- Every concept note gets a **Combinations** section, grouped by operator:
  negated · framed (per frame shape) · repeated · with modifier (per
  modifier) · with partner. Each entry links the other element(s).
- Operator notes (`operators/negation.md`, `operators/frame-triangle.md`, …)
  compare how sources solve the same combination and list the rule
  exceptions.
- An index **Composition rules** summarizes the analysis below with counts
  from the catalog.

## Analysis: what works in one glyph

Computed from the catalog (legibility of composites vs their base, per
operator and part count) and summarized in the index note. Working
hypotheses, to be confirmed or refuted by the data:

- **Works at 16 px:** base + one corner modifier of at least ~5 px; base + negation; base in
  a frame; 2–3 repetitions; big + small; base + motion lines.
- **Degrades:** two equally weighted complex partners (8 px each at 16 px);
  three operators at once (frame + negation + badge); text inside the glyph.
- **Impossible or contradictory:** double negation; two sign classes (a
  prohibition circle inside a warning triangle); a badge on a badge; two
  corner modifiers at 16 px; contradicting modifiers (check + x); negating a
  bare frame; repetition > 3 at small size (reads as texture).
- **Better as a sequence:** relations between two objects, time order and
  processes, comparisons, quantities > 3, anything whose meaning depends on
  order.

The catalog records, per composite, which of these classes it falls into
(`fit`: glyph | degrades | contradictory | sequence).

## Testing

- Hand-built composites with known structure (including known size ratios): bell + slash, house in each
  frame shape, 2x and 3x repetition, base + cutout corner badge, two
  partners side by side, merged strokes, text inside.
- The name-vocabulary mapping is table-tested.
- Merge logic: agreeing, conflicting and name-only cases.
- Vault: a composite appears under each of its elements.

## Out of scope for now

- Generating new composites from elements (a later design task for the
  font project).
- Semantic composition across languages (idioms like "light bulb = idea").
