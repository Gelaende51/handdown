# Symbols and ideas: the two top rungs

## Goal

The hierarchy's two top rungs become **symbol** and **idea**:

1. pictogram (image) → 2. style group → 3. depiction → **4. symbol** → **5. idea**

- A **symbol** is a sign in Wikipedia's sense: a mark understood as standing
  for something by convention or resemblance (heart symbol, recycling symbol,
  the floppy-disk save icon, the trefoil). A symbol is realised by a set of
  depictions: many drawings of a floppy disk make up the save symbol.
- An **idea** is what symbols stand for: saving, love, danger, recycling. Ideas
  are concepts (WordNet synsets, Wikidata items, terms).

Decisions (2026-10-02):
- The drawn **object** (mug, floppy disk) stays as an attribute of the
  depiction; it helps form symbols but is no longer a rung.
- Symbols are formed **from the data**, linked to Wikipedia/Wikidata where an
  article exists, refined by Claude on the larger groups and by review.
- **Relations**, all four kinds:
  - depiction → symbol, the *form*: canonical, variant, stylisation, metaphor
  - symbol → idea, the *kind* of sign: convention, resemblance, metaphor, index
  - symbol ↔ symbol: variant of, derived from, composed of, opposite of, same idea
  - idea ↔ idea: broader, narrower, related (WordNet, Wikidata)
- Relations **within a rung** (symbol ↔ symbol, idea ↔ idea) are shown
  differently on the web pages from relations **between rungs**: rungs are the
  navigation (breadcrumb up, grids of members down); siblings are a separate,
  lateral panel with the relation named on each link.

## Data

| table | rows | key columns |
|---|---|---|
| `symbol` | one per symbol | `id` (`sym:<slug>`), `label`, `description`, `wikidata_qid`, `wikipedia` (en title), `method`, `size`, `source_count` |
| `symbol_depiction` | depiction ↔ symbol | `symbol_id`, `depiction_id`, `form`, `method`, `confidence` |
| `symbol_idea` | symbol → idea | `symbol_id`, `concept_id`, `kind`, `method`, `confidence` |
| `symbol_relation` | symbol ↔ symbol | `symbol_a`, `symbol_b`, `relation`, `method` |
| `idea_relation` | idea ↔ idea | `concept_a`, `concept_b`, `relation`, `source` |

`meaning_link` (depiction → concept) stays as the evidence ideas are derived
from. Every decision by a model is logged in `classification` (`field =
'symbol'`, `'idea'`, `'form'`, `'relation'`).

## Forming symbols (rules)

- A depiction's **key** is its drawn object (or, without one, the concept its
  names point to) plus its **leading idea**: the strongest meaning link that
  is not the object itself. Depictions with the same key form one symbol:
  floppy disk + save → "floppy disk (save)"; arrow + download → "arrow
  (download)". A depiction whose meanings are only the object itself forms a
  literal symbol ("mug").
- Keys with few depictions (fewer than 3 sources) join the object's literal
  symbol rather than forming their own.
- **Form**: the depiction with the most sources is `canonical`, the others
  `variant`; stylisation and metaphor come from Claude.
- **Kind**: `resemblance` when the idea is the object or close to it in WordNet
  (mug → cup), else `convention`; metaphor and index come from Claude.
- **Symbol ↔ symbol** (rules): `same idea` for symbols sharing their leading
  idea (floppy disk (save), arrow into tray (save)); `opposite of` from
  WordNet antonyms of the leading ideas (lock/unlock, show/hide); `composed of`
  from composites whose parts link to depictions of other symbols.
- **Idea ↔ idea**: WordNet hypernyms and hyponyms among the ideas in use, and
  Wikidata's "subclass of" where the idea has a QID.

## Wikipedia and Wikidata

A GitHub Actions job (Wikidata is not reachable from the container) fetches
Wikidata items that are symbols (instance or subclass of *symbol*, Q80071)
with their English Wikipedia article, labels and aliases, and commits the
list (Wikidata is CC0). Symbols are matched by label and alias ("heart symbol",
"recycling symbol"); a match sets `wikidata_qid` and `wikipedia`.

## Claude refinement

For the largest symbols (most depictions and sources), Claude sees a sheet of
their depictions with names and leading ideas and returns: the symbol's name
and Wikipedia title if one exists, the form of each depiction, and the kind of
sign for each idea. Piloted on a sample before it runs at scale.

## Web and review

- Vault and site: a page per symbol (breadcrumb: idea ← symbol; grid of its
  depictions with their form) and per idea (grid of its symbols with the kind
  of sign). Sibling relations appear in a separate panel beside the
  hierarchy, each link naming its relation (variant of, opposite of, broader).
- Review app: the level "object" becomes "symbol" and "meaning" becomes
  "idea"; marks on the old levels are kept and mapped.

## Impact

- Functionality: the catalog answers "which signs mean save?" and "how is the
  heart symbol drawn?", which the object rung could not.
- Resources: rule forming runs locally in minutes; the Wikidata fetch on
  Actions; Claude refinement costs quota (pilot first).
- Compatibility: depiction, style group and meaning tables are unchanged; the
  review levels are renamed with a mapping.
