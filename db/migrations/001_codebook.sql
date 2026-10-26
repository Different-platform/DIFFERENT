-- Codebook layer (Requirements 1.1, 2.1, 5.2)
--
-- Holds the parsed NHIS codebook: one row per variable per survey year,
-- its value labels, and the section it belongs to. This is what retrieval
-- searches and what citations point at.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE sections (
    id           bigserial PRIMARY KEY,
    survey_year  int  NOT NULL,
    code         text NOT NULL,          -- 'FDS'
    name         text NOT NULL,          -- 'Food Security'
    n_variables  int,
    embedding    vector(768),
    UNIQUE (survey_year, code)
);

CREATE TABLE codebook_variables (
    id             bigserial PRIMARY KEY,
    survey_year    int  NOT NULL,
    variable_name  text NOT NULL,        -- 'HISPALLP_A'
    section_code   text,
    section_name   text,
    module         text,                 -- parsed from 'Module:' (additive, see note)
    description    text,
    question_text  text,
    keywords       text,
    data_type      text,                 -- 'Numeric' | 'Character' (additive)
    universe       text,                 -- raw skip-pattern expression
    universe_description text,           -- plain-English universe (additive)
    page           int  NOT NULL,        -- required for Req 2.1 citations
    is_recode      boolean NOT NULL DEFAULT false,
    source_era     text NOT NULL
                   CHECK (source_era IN ('2019plus', 'legacy_2017_2018')),
    embed_text     text,                 -- exactly what was embedded
    embedding      vector(768),
    tsv            tsvector GENERATED ALWAYS AS (
                       to_tsvector('english',
                           coalesce(description, '') || ' ' ||
                           coalesce(question_text, '') || ' ' ||
                           coalesce(keywords, ''))
                   ) STORED,
    UNIQUE (survey_year, variable_name)
);

-- is_sentinel is computed per (variable_id, code) by the ingestion job and
-- NEVER from a global rule (Requirement 5.1, 5.3). The variable_id foreign
-- key scoping every row is what structurally prevents cross-variable leakage:
-- HISPALLP_A code 7 is a real racial category while 97/98/99 are sentinels,
-- and a different variable may legitimately treat 7 as a sentinel.
CREATE TABLE value_labels (
    id           bigserial PRIMARY KEY,
    variable_id  bigint NOT NULL REFERENCES codebook_variables(id) ON DELETE CASCADE,
    code         numeric NOT NULL,       -- numeric, not int: see note below
    label        text NOT NULL,
    frequency    int,
    percent      numeric(6, 3),
    is_sentinel  boolean NOT NULL,
    UNIQUE (variable_id, code)
);

-- Which years each variable actually appears in. Computed at ingestion from
-- parsed records, never inferred from search results (a naive answer implying
-- a variable was dropped is misleading).
CREATE TABLE variable_availability (
    variable_name text PRIMARY KEY,
    years         int[] NOT NULL
);

-- Without the HNSW index pgvector falls back to a sequential scan.
-- vector_cosine_ops matches the normalized output of sentence-transformers.
CREATE INDEX codebook_variables_embedding_idx
    ON codebook_variables USING hnsw (embedding vector_cosine_ops);
CREATE INDEX codebook_variables_tsv_idx
    ON codebook_variables USING gin (tsv);
CREATE INDEX codebook_variables_year_name_idx
    ON codebook_variables (survey_year, variable_name);
CREATE INDEX sections_embedding_idx
    ON sections USING hnsw (embedding vector_cosine_ops);
CREATE INDEX value_labels_variable_idx
    ON value_labels (variable_id);

-- ---------------------------------------------------------------------------
-- Two deliberate deviations from design.md, both correctness fixes:
--
-- 1. value_labels.code is `numeric`, not `int`. design.md specifies int, but
--    continuous NHIS variables use fractional sentinels — BMI_A encodes
--    "Unknown" as 99.9. An int column cannot store that, so the sentinel
--    would be silently truncated to 99 and the SQL verifier would then
--    compare against the wrong value. numeric holds both 7 and 99.9.
--
-- 2. module, section_name, data_type, universe_description, frequency, and
--    percent are additive columns. The parser extracts all of them from the
--    codebook page at no extra cost, and universe_description in particular
--    is what lets an answer explain structural missingness — which design.md
--    itself calls out as the difference between a correct analysis and a
--    published mistake.
-- ---------------------------------------------------------------------------
