-- ===========================================================================
-- 004: rag_chunks — the retrieval corpus for grounding the SQL generator.
-- (003 is reserved for conversation/session tables, landing in a later step.)
--
-- One table serves BOTH retrieval strategies, selected by config:
--   * vector  (dev):  embedding vector(768)  — nomic-embed-text via Ollama
--   * lexical (prod): ts tsvector            — Postgres full-text search,
--     used on Render where the local Ollama instance is unreachable
-- The corpus is data; the retrieval strategy is config. Rebuilding the index
-- (scripts/build_rag_index.py) repopulates content and embeddings.
--
-- No ANN index (ivfflat/hnsw) on purpose: the corpus is a few dozen chunks,
-- so an exact sequential scan is both faster than index maintenance and
-- exactly correct. Revisit only if the corpus grows by orders of magnitude.
--
-- Deliberately NO grants to agent_ro: retrieval runs under the app role
-- before SQL generation; the read-only executor role cannot even see how the
-- system prompts itself.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id         text PRIMARY KEY,          -- stable slug, e.g. 'schema:zone_demand', 'example:revenue-by-zone'
    kind       text NOT NULL CHECK (kind IN ('schema_doc', 'query_example', 'guidance')),
    content    text NOT NULL,             -- exactly what the SQL generator will see
    embedding  vector(768),               -- NULL until embedded; vector retriever skips NULLs
    -- Generated, so lexical search can never operate on stale text.
    ts         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_ts ON rag_chunks USING gin (ts);
