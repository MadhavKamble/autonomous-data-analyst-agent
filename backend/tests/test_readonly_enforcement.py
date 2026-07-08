"""THE test this project's design docs point to when explaining SQL safety.

Every statement here goes through a RAW psycopg connection as agent_ro —
db/guardrails.py and db/executor.py are never imported in this file. That is
deliberate: it proves the read-only boundary is the agent_ro role's Postgres
GRANTs (migration 002), not the sqlglot pre-check. A hallucinated or injected
write statement that somehow bypassed the guardrail (or reached the database
through some future code path that forgets to call it) must still be
rejected — by Postgres itself.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors as pg_errors

from tests.conftest import DATA_TABLES

# Postgres error classes that count as "the database itself said no", keyed
# by SQLSTATE via psycopg's typed exceptions rather than string-matching:
#   InsufficientPrivilege (42501) — the GRANT is genuinely missing. DROP TABLE
#     specifically requires table OWNERSHIP, not just a privilege grant, so
#     its error text is "must be owner of table ..." rather than "permission
#     denied for table ..." — same exception class, different Postgres wording.
#   ReadOnlySqlTransaction (25006) — blocked by the read-only transaction
#     default (migration 002's ALTER ROLE ... SET default_transaction_read_only)
#     before privileges are even checked. Documented as a fail-fast
#     convenience, NOT the boundary — see test_write_rejected_even_after_
#     disabling_session_readonly below, which eliminates this path.
DATABASE_LEVEL_REJECTION = (pg_errors.InsufficientPrivilege, pg_errors.ReadOnlySqlTransaction)

# The exact statement types the design doc calls out: DROP TABLE, INSERT,
# UPDATE, DELETE, TRUNCATE.
WRITE_STATEMENTS: list[tuple[str, str]] = [
    ("DROP TABLE rides_historical_nyc", "DROP TABLE"),
    ("INSERT INTO zone_demand (event_date) VALUES ('2099-01-01')", "INSERT"),
    ("UPDATE rides_historical_nyc SET status = 'completed'", "UPDATE"),
    ("DELETE FROM rides_historical_nyc", "DELETE"),
    ("TRUNCATE zone_demand", "TRUNCATE"),
]
# Bonus DDL coverage beyond the required five.
EXTRA_STATEMENTS: list[tuple[str, str]] = [
    ("CREATE TABLE evil (id int)", "CREATE TABLE"),
    ("ALTER TABLE zone_demand ADD COLUMN evil int", "ALTER TABLE"),
]


def _assert_database_level_rejection(exc: psycopg.Error, label: str) -> None:
    assert isinstance(exc, DATABASE_LEVEL_REJECTION), (
        f"{label} did not fail with a Postgres privilege/read-only error "
        f"(got {type(exc).__name__}: {exc}) — the database itself must be "
        "the reason this failed, not some other error."
    )


@pytest.mark.parametrize("sql,label", WRITE_STATEMENTS, ids=[label for _, label in WRITE_STATEMENTS])
def test_write_rejected_at_database_level(agent_database_url: str, sql: str, label: str) -> None:
    """Connect as agent_ro directly and attempt a write. No sqlglot, no
    SQLExecutor — if this fails, it's Postgres saying no, not our code."""
    with psycopg.connect(agent_database_url) as conn:
        with pytest.raises(psycopg.Error) as exc_info:
            conn.execute(sql)
    _assert_database_level_rejection(exc_info.value, label)


@pytest.mark.parametrize("sql,label", EXTRA_STATEMENTS, ids=[label for _, label in EXTRA_STATEMENTS])
def test_extra_ddl_rejected_at_database_level(agent_database_url: str, sql: str, label: str) -> None:
    with psycopg.connect(agent_database_url) as conn:
        with pytest.raises(psycopg.Error) as exc_info:
            conn.execute(sql)
    _assert_database_level_rejection(exc_info.value, label)


@pytest.mark.parametrize("sql,label", WRITE_STATEMENTS, ids=[label for _, label in WRITE_STATEMENTS])
def test_write_rejected_even_after_disabling_session_readonly(
    agent_database_url: str, sql: str, label: str
) -> None:
    """migration 002 sets default_transaction_read_only=on for agent_ro as a
    fail-fast convenience, but documents it as NOT the security boundary — a
    session can turn that off for itself. Prove the GRANTs hold regardless.

    autocommit=True matters here: default_transaction_read_only governs
    transactions that START after the SET takes effect. Under psycopg's
    default (autocommit=False), the SET and the write would share the one
    already-open transaction, whose read-only-ness was fixed at BEGIN time —
    so the SET would appear too late and this test would (wrongly) observe
    'read-only transaction' instead of exercising the GRANT check at all.
    With autocommit, the SET commits immediately and the write begins a
    genuinely fresh, non-read-only transaction — matching how the original
    live verification did it (separate auto-committing psql -c statements).
    """
    with psycopg.connect(agent_database_url, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")
        assert conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "off"
        with pytest.raises(psycopg.Error) as exc_info:
            conn.execute(sql)
    # Strictly InsufficientPrivilege here (not ReadOnlySqlTransaction) — the
    # read-only escape hatch is closed by autocommit above, so the ONLY
    # remaining reason this can fail is the missing GRANT itself.
    assert isinstance(exc_info.value, pg_errors.InsufficientPrivilege), (
        f"{label} succeeded (or failed for the wrong reason) after disabling "
        f"the read-only session default: {type(exc_info.value).__name__}: "
        f"{exc_info.value} — this would mean the GRANT is not actually the boundary."
    )


def test_agent_ro_cannot_read_infrastructure_tables(agent_database_url: str) -> None:
    """migration 002/003/004 deliberately grant SELECT on the three data
    tables ONLY — the executor role can't read sessions, messages, the RAG
    corpus, or the migration ledger, even though those tables live in the
    same database."""
    with psycopg.connect(agent_database_url) as conn:
        for table in ("schema_migrations", "sessions", "messages", "rag_chunks"):
            with pytest.raises(pg_errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM {table} LIMIT 1")
            conn.rollback()  # failed statement aborts the transaction


def test_agent_ro_can_still_read_data_tables(agent_database_url: str) -> None:
    """Sanity check: agent_ro isn't failing everything due to a misconfigured
    role (e.g. no grants at all) — it can do the one thing it exists for."""
    with psycopg.connect(agent_database_url) as conn:
        for table in DATA_TABLES:
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert count > 0, f"expected seeded rows in {table}"


def test_data_integrity_preserved_across_every_rejected_write(
    agent_database_url: str, admin_database_url: str
) -> None:
    """Self-contained end-to-end proof: snapshot row counts, hammer every
    write statement above (both plain and with read-only disabled) again in
    one session, then assert nothing changed. Doesn't depend on other tests
    having run first."""
    with psycopg.connect(admin_database_url) as conn:
        before = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in DATA_TABLES
        }

    with psycopg.connect(agent_database_url) as conn:
        for sql, _label in WRITE_STATEMENTS + EXTRA_STATEMENTS:
            with pytest.raises(psycopg.Error):
                conn.execute(sql)
            conn.rollback()

    # Separate autocommit connection: see test_write_rejected_even_after_
    # disabling_session_readonly for why the SET needs its own committed
    # transaction to actually take effect before the write is attempted.
    with psycopg.connect(agent_database_url, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")
        for sql, _label in WRITE_STATEMENTS:
            with pytest.raises(psycopg.Error):
                conn.execute(sql)

    with psycopg.connect(admin_database_url) as conn:
        after = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in DATA_TABLES
        }

    assert before == after, f"row counts changed after rejected writes: {before} -> {after}"
