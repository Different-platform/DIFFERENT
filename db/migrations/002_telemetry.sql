-- Telemetry layer (Requirements 17.1, 20.1, 20.2)
--
-- Conversation history (needed so follow-up questions can be rewritten into
-- standalone ones) plus one query_log row per assistant turn.

CREATE TABLE conversations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable, with no foreign key target: no accounts table exists in this
    -- version and no login is required (Req 20.2). Reserved so that adding
    -- auth later means adding an FK to an already-populated column rather
    -- than a breaking migration (Req 20.1).
    user_id       uuid,
    title         text,
    default_year  int,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id               bigserial PRIMARY KEY,
    conversation_id  uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             text NOT NULL CHECK (role IN ('user', 'assistant')),
    content          text NOT NULL DEFAULT '',
    citations        jsonb,        -- [{variable_name, section, page, survey_year}]
    verdict          text CHECK (verdict IN ('MEASURED', 'RELATED', 'ABSENT')),
    generated_sql    text,
    sql_verified     boolean,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- One row per assistant turn. The eval harness and any future dashboard both
-- read this table; it is the only place pipeline behaviour is recorded.
CREATE TABLE query_log (
    id                  bigserial PRIMARY KEY,
    message_id          bigint REFERENCES messages(id) ON DELETE SET NULL,
    conversation_id     uuid,
    question            text NOT NULL,
    rewritten_question  text,
    intent              text,        -- codebook | data | both
    verdict             text,        -- MEASURED | RELATED | ABSENT, NULL on error
    top_variable        text,
    top_similarity      real,
    retrieved           jsonb,       -- [{variable_name, score, section, page}]
    generated_sql       text,
    sql_verified        boolean,
    verification_notes  jsonb,
    error               text,        -- set when the pipeline failed (e.g. LLM timeout)
    latency_ms          int,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at);
CREATE INDEX query_log_created_idx     ON query_log (created_at);
CREATE INDEX query_log_verdict_idx     ON query_log (verdict);
