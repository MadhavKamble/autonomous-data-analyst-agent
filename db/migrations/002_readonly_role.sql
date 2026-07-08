-- ===========================================================================
-- 002: agent_ro — the read-only role the SQL executor connects as.
--
-- This is the AUTHORITATIVE defense against a hallucinating (or injected)
-- model writing to the database: agent_ro simply has no privilege that could
-- modify anything, so INSERT/UPDATE/DELETE/DDL fail inside Postgres itself,
-- regardless of what any prompt or Python-side check does or misses.
--
-- Privilege model:
--   * SELECT on exactly the three data tables — nothing else, ever.
--   * No grants on infrastructure tables (schema_migrations now; sessions and
--     rag_chunks later). The agent cannot even read the app's own state.
--   * PUBLIC-inherited escape hatches closed: no CREATE on schema public,
--     no TEMP tables on the database.
--
-- NO SECRETS IN MIGRATIONS: the role is created NOLOGIN here; login + password
-- are provisioned separately by scripts/set_agent_password.py so no credential
-- ever enters version control.
--
-- Related design choice: scripts/export_from_delta.py refreshes data with
-- TRUNCATE + COPY instead of DROP + CREATE precisely so these grants survive
-- a re-export.
-- ===========================================================================

-- CREATE ROLE has no IF NOT EXISTS; guard so the migration is safe to run
-- against a database where the role was provisioned out-of-band.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON zone_demand, rides_historical_nyc, zone_demand_historical_nyc TO agent_ro;

-- Close paths every role inherits from PUBLIC:
-- (CREATE on schema public is already revoked by default since Postgres 15;
-- explicit here so the guarantee doesn't depend on server version/config.)
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $$
BEGIN
    -- TEMP tables are a write path (and a disk-consumption path) — revoke the
    -- PUBLIC grant, keep it for the admin/owner role applying this migration.
    -- Dynamic SQL because the database name differs between local and Neon.
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO %I', current_database(), current_user);
END $$;

-- Defense-in-depth session defaults. Honest caveat: a session can override
-- its own role-level settings (SET default_transaction_read_only = off), so
-- these are NOT the security boundary — the missing grants above are. These
-- exist to fail fast with clear errors and to bound runaway queries even if
-- the application-side executor forgets to set its own limits.
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '10s';
ALTER ROLE agent_ro SET idle_in_transaction_session_timeout = '30s';
