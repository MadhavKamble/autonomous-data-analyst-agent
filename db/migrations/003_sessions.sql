-- ===========================================================================
-- 003: conversation state — sessions and messages.
--
-- ALL conversation state lives here, never in process memory: the backend
-- runs on Render's free tier, which spins the process down after inactivity
-- and loses everything in RAM. A cold-started process must be able to
-- reconstruct any conversation from these tables alone.
--
-- messages.trace holds the FULL AskResult JSON (answer, caveats, result data,
-- reasoning trace, budget usage) for assistant messages — so the frontend's
-- expandable trace panel works identically for live answers and reloaded
-- history.
--
-- Deliberately NO grants to agent_ro (same rule as rag_chunks): the SQL
-- executor role cannot read other users' conversations, even if a generated
-- query names these tables.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS sessions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title          text NOT NULL DEFAULT '',        -- first question, truncated; shown in the session list
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_active_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,                       -- question text / plain-English answer (or failure message)
    trace      jsonb,                               -- full AskResult for assistant messages; NULL for user messages
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Serving one conversation in order is THE access pattern.
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions (last_active_at DESC);
