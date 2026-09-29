# Depiction hierarchy: design

Date: 2026-09-29
Status: approved in conversation (2026-09-29)
Extends: [catalog design](2026-09-28-pictogram-catalog-design.md), [composites](2026-09-29-composite-pictograms-design.md)

## Purpose

Group pictograms at four levels above the single pictogram:

| Level | Groups | Example |
|---|---|---|
| 4 meaning | everything used to mean the same | *download*: down arrow, floppy disk, cloud with arrow |
| 3 object | what is drawn | *coffee cup*, *mug* |
| 2 depiction | object + **view** + **varieties** | mug seen from the side, **with** saucer, **with** steam |
| 1 style group | the same drawing in different styles | outline / filled / rounded / sharp / duotone / simplified |
| 0 pictogram | | |

The existing name concepts (mixing object and meaning) and depiction
clusters remain as the name level. The hierarchy is built next to them.

## Decisions (from the conversation)

| Topic | Decision |
|---|---|
| Object vs meaning | **Hybrid**: rules for all; AI (blind) for the style groups of established concepts (~20k, about $70), and results propagate to group members |
| Same drawing (style) | fill/stroke (outline, filled, duotone, weight, rounding, grid) and **simplification** |
| Not style | **mirroring** is a different depiction (orientation) |
| Details | the **presence** of a detail (steam, saucer) makes a separate variety; its execution (number of waves, how they bend) is style |
| Composite details | composite elements are classified by the composite analysis on their own; a detail that is a composite element does not create a variety of the base |

## Model

- `depiction` (id, object_id → concept, view, varieties JSON sorted list,
  description, method rules|ai|manual, representative_id, size,
  source_count, name_concept_id)
- `style_group` (id, depiction_id, representative_id, size, source_count,
  styles JSON: counts of style values)
- `style_member` (style_group_id, pictogram_id)
- `meaning_link` (depiction_id, concept_id, source name|alias|ai|manual,
  confidence)

Views are a fixed vocabulary: `front`, `side`, `top`, `bottom`,
`three-quarter`, `isometric`, `partial`, `full`, `unknown`.

## Rules (all pictograms)

1. **Name roles.** The name (without style/size/operator words) is split
   into object words, meaning words, view words and variety words.
   - A token is an **object word** when its WordNet noun sense is a thing:
     `noun.artifact`, `noun.object`, `noun.animal`, `noun.plant`,
     `noun.food`, `noun.body`, `noun.person`, `noun.substance`,
     `noun.shape`.
   - A **meaning word** is an action or abstraction: a verb sense, or a
     noun sense outside those classes (download, delete, settings, home).
   - **View words:** top, topview, overhead, side, profile, front, back,
     iso, isometric, 3d, perspective, partial, half, full.
   - **Variety words** are the remaining modifiers that describe a visible
     feature (hot → steam, saucer, lid, open, closed, …).
   - The object is the longest object phrase that WordNet knows; meanings
     are the meaning words. A name made of object words only (coffee-cup)
     means the object itself (source `name`, lower confidence).
2. **Aliases** add meanings (source `alias`).
3. **Grouping** per name concept, with one average-linkage tree on the
   shape features (ink map + filled silhouette, not mirror-invariant) cut
   twice: tight (style groups, distance ≤ 0.2) and coarse (depictions,
   ≤ 0.45). Cutting the same tree keeps the levels nested.
4. Depictions of different name concepts that share an object are the
   same object at level 3. Meanings connect across objects at level 4.

## AI pass (bounded)

For style-group representatives of established concepts (by source count),
a blind contact sheet asks per cell: the object drawn (short noun phrase),
the view (vocabulary above), the visible features (present, not how they
are drawn), and up to three meanings. Answers:

- the object maps through the concept resolver, and sets `depiction.object_id`
  when the rules had none, or records a conflict;
- view and features refine the depiction. Style groups whose view or
  feature set differs from their depiction's are split off into their own
  depiction (method `ai`);
- meanings become `meaning_link` rows (source `ai`).

Runs are bounded (`--limit`), logged in `ai_run`, and detached.

## Scores

`convention` gains a meaning-level variant: the share of independent
sources that draw a meaning with this object/depiction (e.g. how many sets
draw *download* as a floppy disk).

## Vault and HTML

- **Meaning** view (the concept note of a meaning): its objects, then per
  object its depictions (view, varieties) with style-group contact sheets.
- **Object** view (the concept note of an object): depictions, and a
  "used to mean" list.
- Composite "Combinations" sections stay as they are.

## Testing

- Name roles, table-tested (download, floppy-disk, cloud-download,
  coffee-cup-hot, mug-side, home).
- Nested grouping on hand-built drawings: outline and filled of the same
  cup form one style group; a mirrored cup and a cup with steam form
  separate groups at style level, and the mirrored cup a separate depiction.
- Meaning links across objects (floppy-disk-download and cloud-download
  both reach *download*).
- AI pass with a stubbed model: object/view/feature answers, depiction split.
- Vault: meaning note lists both objects.
