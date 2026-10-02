-- handdown catalog schema, version 1
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS platform (
    id TEXT PRIMARY KEY,               -- slug, e.g. "iconify", "github"
    name TEXT NOT NULL,
    url TEXT,
    kind TEXT,                         -- aggregator|code-host|wiki|standards-body|foundry|museum|company|community|package-registry|other
    api_url TEXT,
    reachable TEXT,                    -- yes|blocked-network|unknown
    notes TEXT,
    found_via TEXT,
    first_seen TEXT
);

CREATE TABLE IF NOT EXISTS source (
    id TEXT PRIMARY KEY,               -- slug, e.g. "tabler"
    platform_id TEXT REFERENCES platform(id),
    name TEXT NOT NULL,
    url TEXT,
    license_spdx TEXT,
    license_url TEXT,
    author TEXT,
    author_url TEXT,
    designer TEXT,
    year INTEGER,
    version TEXT,
    accessed_at TEXT,
    found_via TEXT,
    harvest_status TEXT NOT NULL DEFAULT 'candidate',  -- candidate|accepted|rejected|blocked-network|harvested|failed
    adapter TEXT,
    adapter_args TEXT,                 -- JSON
    system TEXT,
    region TEXT,
    domain TEXT,
    category TEXT,
    grid_size REAL,
    stats TEXT,                        -- JSON
    popularity TEXT,                   -- JSON (stars, downloads)
    notes TEXT
);

CREATE TABLE IF NOT EXISTS pictogram (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source(id),
    original_id TEXT NOT NULL,
    original_name TEXT,
    original_url TEXT,
    raw_path TEXT,
    norm_path TEXT,
    sha256 TEXT,
    phash TEXT,
    duplicate_of INTEGER REFERENCES pictogram(id),
    format TEXT,                       -- svg|glyph|raster|other
    traced INTEGER DEFAULT 0,
    color_class TEXT,                  -- native|derivable|threshold|none
    color_count INTEGER,
    svg_valid INTEGER,
    uses TEXT,                         -- JSON
    fill_rule TEXT,
    font_ready INTEGER,
    file_size INTEGER,
    viewbox TEXT,
    grid_size REAL,
    style TEXT,                        -- outline|filled|duotone|mixed
    stroke_width REAL,
    corner_radius REAL,
    stroke_caps TEXT,
    padding_ratio REAL,
    symmetry TEXT,                     -- JSON {h,v,r}
    mirror_safe INTEGER,
    components INTEGER,
    holes INTEGER,
    figure_ground_ratio REAL,
    container_shape TEXT,
    perspective TEXT,
    node_count INTEGER,
    path_count INTEGER,
    has_text INTEGER,
    has_numerals INTEGER,
    has_person INTEGER,
    person_gendered INTEGER,
    negation TEXT,
    designer TEXT,
    year INTEGER,
    lineage TEXT,
    standard TEXT,
    regulatory_status TEXT,
    deprecated_by TEXT,
    unicode_codepoint TEXT,
    license_override TEXT,
    author_override TEXT,
    raw_tags TEXT,                     -- JSON
    raw_categories TEXT,               -- JSON
    raw_description TEXT,
    harvested_at TEXT,
    normalized_at TEXT,
    measured_at TEXT,
    UNIQUE (source_id, original_id)
);
CREATE INDEX IF NOT EXISTS pictogram_sha ON pictogram(sha256);
CREATE INDEX IF NOT EXISTS pictogram_phash ON pictogram(phash);

CREATE TABLE IF NOT EXISTS concept (
    id TEXT PRIMARY KEY,               -- "wn:trash_can.n.01" | "qid:Q..." | "term:trash can"
    label TEXT NOT NULL,
    wikidata_qid TEXT,
    wordnet_synset TEXT,
    labels TEXT,                       -- JSON lang -> label
    definition TEXT,
    domain TEXT,
    parent_id TEXT,
    referent_type TEXT
);

CREATE TABLE IF NOT EXISTS pictogram_concept (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL REFERENCES concept(id),
    confidence REAL,
    method TEXT,                       -- tag-match|dictionary|ai|manual
    PRIMARY KEY (pictogram_id, concept_id)
);
CREATE INDEX IF NOT EXISTS pc_concept ON pictogram_concept(concept_id);

CREATE TABLE IF NOT EXISTS depiction_cluster (
    id INTEGER PRIMARY KEY,
    concept_id TEXT NOT NULL REFERENCES concept(id),
    description TEXT,
    representative_id INTEGER REFERENCES pictogram(id),
    representation TEXT,
    metaphor_chain TEXT,
    convention_strength REAL,
    size INTEGER,
    source_count INTEGER
);

CREATE TABLE IF NOT EXISTS cluster_member (
    cluster_id INTEGER NOT NULL REFERENCES depiction_cluster(id) ON DELETE CASCADE,
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    PRIMARY KEY (cluster_id, pictogram_id)
);

CREATE TABLE IF NOT EXISTS semantic_meta (
    cluster_id INTEGER PRIMARY KEY REFERENCES depiction_cluster(id) ON DELETE CASCADE,
    concreteness REAL,
    semantic_distance REAL,
    ambiguity INTEGER,
    alternatives TEXT,                 -- JSON
    cultural_risk TEXT,                -- JSON
    timelessness REAL,
    anachronism INTEGER,
    model TEXT,
    run_id TEXT,
    assessed_at TEXT
);

CREATE TABLE IF NOT EXISTS rating (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    value REAL,
    detail TEXT,                       -- JSON (e.g. legibility curve)
    method TEXT,
    method_version TEXT,
    model TEXT,
    run_id TEXT,
    computed_at TEXT,
    is_override INTEGER DEFAULT 0,
    PRIMARY KEY (pictogram_id, metric, is_override)
);

CREATE TABLE IF NOT EXISTS confusion_pair (
    concept_a TEXT NOT NULL,
    concept_b TEXT NOT NULL,
    cluster_id INTEGER,
    evidence TEXT,
    strength REAL,
    PRIMARY KEY (concept_a, concept_b, evidence)
);

CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    engine TEXT,
    seed TEXT,
    run_at TEXT,
    candidates_found INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS harvest_error (
    id INTEGER PRIMARY KEY,
    source_id TEXT,
    item TEXT,
    stage TEXT,
    error TEXT,
    at TEXT
);

CREATE TABLE IF NOT EXISTS ai_run (
    id TEXT PRIMARY KEY,
    job TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd REAL,
    log_path TEXT,
    at TEXT
);

CREATE TABLE IF NOT EXISTS override (
    target TEXT NOT NULL,              -- "concept:<id>" | "pictogram:<id>" | "cluster:<id>"
    field TEXT NOT NULL,
    value TEXT,
    note TEXT,
    synced_at TEXT,
    PRIMARY KEY (target, field)
);

-- Original SVG text as harvested (the untouched source files stay in data/raw).
CREATE TABLE IF NOT EXISTS raw_svg (
    pictogram_id INTEGER PRIMARY KEY REFERENCES pictogram(id) ON DELETE CASCADE,
    svg TEXT NOT NULL
);

-- Shape feature (16x16 normalized ink map, float16 bytes) for depiction clustering.
CREATE TABLE IF NOT EXISTS feature (
    pictogram_id INTEGER PRIMARY KEY REFERENCES pictogram(id) ON DELETE CASCADE,
    vec BLOB NOT NULL
);

-- Composite pictograms (see docs/superpowers/specs/2026-09-29-composite-pictograms-design.md)
CREATE TABLE IF NOT EXISTS composition (
    pictogram_id INTEGER PRIMARY KEY REFERENCES pictogram(id) ON DELETE CASCADE,
    kind TEXT,            -- generic | unique
    font_type TEXT,       -- mark | ligature | sequence | unique
    fit TEXT,             -- glyph | degrades | contradictory | sequence
    conflict INTEGER DEFAULT 0,
    confidence REAL,
    method TEXT,          -- rules | ai | manual
    computed_at TEXT
);
CREATE TABLE IF NOT EXISTS composition_part (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    part_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    label TEXT,
    concept_id TEXT,
    position TEXT,
    count INTEGER DEFAULT 1,
    area_ratio REAL, extent_ratio REAL, glyph_share REAL, px16 REAL, size_class TEXT,
    PRIMARY KEY (pictogram_id, part_no)
);
CREATE INDEX IF NOT EXISTS composition_part_concept ON composition_part(concept_id);
CREATE TABLE IF NOT EXISTS composition_relation (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    part_a INTEGER NOT NULL, part_b INTEGER NOT NULL, relation TEXT NOT NULL,
    PRIMARY KEY (pictogram_id, part_a, part_b)
);

-- Depiction hierarchy (docs/superpowers/specs/2026-09-29-depiction-hierarchy-design.md)
CREATE TABLE IF NOT EXISTS depiction (
    id INTEGER PRIMARY KEY,
    name_concept_id TEXT,
    object_id TEXT,
    view TEXT DEFAULT 'unknown',
    varieties TEXT DEFAULT '[]',
    description TEXT,
    method TEXT DEFAULT 'rules',
    representative_id INTEGER,
    size INTEGER,
    source_count INTEGER
);
CREATE INDEX IF NOT EXISTS depiction_object ON depiction(object_id);
CREATE TABLE IF NOT EXISTS style_group (
    id INTEGER PRIMARY KEY,
    depiction_id INTEGER REFERENCES depiction(id) ON DELETE CASCADE,
    representative_id INTEGER,
    size INTEGER,
    source_count INTEGER,
    styles TEXT,
    assessed_at TEXT
);
CREATE INDEX IF NOT EXISTS style_group_depiction ON style_group(depiction_id);
CREATE TABLE IF NOT EXISTS style_member (
    style_group_id INTEGER NOT NULL REFERENCES style_group(id) ON DELETE CASCADE,
    pictogram_id INTEGER NOT NULL,
    PRIMARY KEY (style_group_id, pictogram_id)
);
CREATE INDEX IF NOT EXISTS style_member_pictogram ON style_member(pictogram_id);
CREATE TABLE IF NOT EXISTS meaning_link (
    depiction_id INTEGER NOT NULL REFERENCES depiction(id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY (depiction_id, concept_id, source)
);
CREATE INDEX IF NOT EXISTS meaning_link_concept ON meaning_link(concept_id);

-- Vision models (docs/superpowers/specs/2026-09-30-vision-models-design.md)
CREATE TABLE IF NOT EXISTS embedding (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (pictogram_id, model)
);
CREATE TABLE IF NOT EXISTS vision_label (
    pictogram_id INTEGER NOT NULL REFERENCES pictogram(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label_id TEXT NOT NULL,
    score REAL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (pictogram_id, kind, rank)
);
CREATE INDEX IF NOT EXISTS vision_label_label ON vision_label(label_id);
CREATE TABLE IF NOT EXISTS label_embedding (
    label_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    vec BLOB NOT NULL
);

-- Classification error marks from the review app
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    target_kind TEXT NOT NULL,      -- image | style_group | depiction | object | meaning
    target_id TEXT NOT NULL,
    levels TEXT NOT NULL,           -- JSON list: style_group | depiction | object | meaning | composite
    correct_value TEXT,
    note TEXT,
    context TEXT,                   -- JSON snapshot of the classification when marked
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT
);

-- Every image and text classification with its origin (append-only), so other
-- classifiers can be compared later. The pictogram is named by source and
-- original id, which survive catalog rebuilds; subject ids are as of the time.
CREATE TABLE IF NOT EXISTS classification (
    id INTEGER PRIMARY KEY,
    source_id TEXT,
    original_id TEXT,
    pictogram_id INTEGER,
    subject TEXT,                   -- depiction | style_group | pictogram
    subject_id INTEGER,
    field TEXT NOT NULL,            -- object | view | varieties | meaning | composition
    value TEXT,                     -- normalized (e.g. wn:cup.n.01)
    raw TEXT,                       -- the classifier's own answer
    confidence REAL,
    method TEXT NOT NULL,           -- ai | probe | vlm | bench | laya
    model TEXT,                     -- model and settings
    input TEXT NOT NULL,            -- image | text
    run TEXT,                       -- session, GitHub run or file it came from
    context TEXT,                   -- JSON: settings, input text, applied or not
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS classification_once ON classification(
    method, IFNULL(model, ''), IFNULL(run, ''), IFNULL(pictogram_id, -1), IFNULL(subject_id, -1), field, IFNULL(value, '')
);
CREATE INDEX IF NOT EXISTS classification_pictogram ON classification(source_id, original_id);

-- Symbols and ideas: the two top rungs (docs/superpowers/specs/2026-10-02-symbols-and-ideas-design.md).
-- A symbol is a sign (heart symbol, the floppy-disk save icon) realised by a set of depictions;
-- ideas are the concepts symbols stand for.
CREATE TABLE IF NOT EXISTS symbol (
    id TEXT PRIMARY KEY,            -- sym:<slug>
    label TEXT NOT NULL,
    description TEXT,
    object_id TEXT,                 -- the drawn object it is formed around
    lead_id TEXT,                   -- its leading idea (NULL: a literal symbol)
    wikidata_qid TEXT,
    wikipedia TEXT,                 -- English Wikipedia article title
    method TEXT NOT NULL DEFAULT 'rules',  -- rules | ai | manual
    size INTEGER,                   -- depictions
    source_count INTEGER
);
CREATE TABLE IF NOT EXISTS symbol_depiction (
    symbol_id TEXT NOT NULL REFERENCES symbol(id) ON DELETE CASCADE,
    depiction_id INTEGER NOT NULL REFERENCES depiction(id) ON DELETE CASCADE,
    form TEXT NOT NULL,             -- canonical | variant | stylisation | metaphor
    method TEXT NOT NULL DEFAULT 'rules',
    confidence REAL,
    PRIMARY KEY (symbol_id, depiction_id)
);
CREATE INDEX IF NOT EXISTS symbol_depiction_depiction ON symbol_depiction(depiction_id);
CREATE TABLE IF NOT EXISTS symbol_idea (
    symbol_id TEXT NOT NULL REFERENCES symbol(id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL,       -- the idea
    kind TEXT NOT NULL,             -- convention | resemblance | metaphor | index
    method TEXT NOT NULL DEFAULT 'rules',
    confidence REAL,
    PRIMARY KEY (symbol_id, concept_id)
);
CREATE INDEX IF NOT EXISTS symbol_idea_concept ON symbol_idea(concept_id);
CREATE TABLE IF NOT EXISTS symbol_relation (    -- siblings: within the symbol rung
    symbol_a TEXT NOT NULL REFERENCES symbol(id) ON DELETE CASCADE,
    symbol_b TEXT NOT NULL REFERENCES symbol(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,         -- variant of | derived from | composed of | opposite of | same idea
    method TEXT NOT NULL DEFAULT 'rules',
    PRIMARY KEY (symbol_a, symbol_b, relation)
);
CREATE TABLE IF NOT EXISTS idea_relation (      -- siblings: within the idea rung
    concept_a TEXT NOT NULL,
    concept_b TEXT NOT NULL,        -- for 'broader': concept_b is broader than concept_a
    relation TEXT NOT NULL,         -- broader | related
    source TEXT NOT NULL,           -- wordnet | wikidata | ai | manual
    PRIMARY KEY (concept_a, concept_b, relation)
);
