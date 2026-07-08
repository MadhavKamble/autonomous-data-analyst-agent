"""Unit tests for the sqlglot pre-check (db/guardrails.py).

This is the fail-fast UX layer, not the security boundary — see
test_readonly_enforcement.py for the actual boundary (agent_ro's Postgres
GRANTs). These tests are pure and need no database connection.
"""

from __future__ import annotations

import pytest

from app.db.guardrails import GuardrailViolation, validate_select_only

ALLOWED = [
    "SELECT 1",
    "select city_zone from zone_demand",
    "WITH t AS (SELECT city_zone FROM zone_demand) SELECT * FROM t",
    "SELECT event_hour::integer FROM zone_demand "
    "UNION ALL SELECT event_hour FROM zone_demand_historical_nyc",
    "SELECT city_zone, count(*) FROM rides_historical_nyc GROUP BY city_zone ORDER BY 2 DESC LIMIT 10",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries_pass(sql: str) -> None:
    validate_select_only(sql)  # must not raise


REJECTED = [
    ("INSERT INTO zone_demand (event_date) VALUES ('x')", "INSERT"),
    ("DROP TABLE rides_historical_nyc", "DROP TABLE"),
    ("UPDATE rides_historical_nyc SET status = 'completed'", "UPDATE"),
    ("DELETE FROM rides_historical_nyc", "DELETE"),
    ("TRUNCATE zone_demand", "TRUNCATE"),
    ("CREATE TABLE evil (id int)", "CREATE TABLE"),
    ("SELECT 1; SELECT 2", "multi-statement"),
    ("SELECT * INTO evil FROM zone_demand", "SELECT INTO"),
    ("SELECT * FROM zone_demand FOR UPDATE", "row lock (FOR UPDATE)"),
    ("SELECT * FROM zone_demand FOR SHARE", "row lock (FOR SHARE)"),
    ("", "empty string"),
    ("   ", "whitespace only"),
    # sqlglot parses this leniently as an ALIAS expression rather than raising
    # a parse error — proof the whitelist (not a parse-error blacklist) is
    # what catches it.
    ("SELEC whoops", "garbage that parses as something else"),
]


@pytest.mark.parametrize("sql,label", REJECTED, ids=[label for _, label in REJECTED])
def test_rejected_queries_raise(sql: str, label: str) -> None:
    with pytest.raises(GuardrailViolation):
        validate_select_only(sql)


def test_rejection_message_is_useful_as_retry_feedback() -> None:
    """The exception message doubles as feedback fed back to the SQL
    generator on retry — it should name the actual problem."""
    with pytest.raises(GuardrailViolation, match="only SELECT"):
        validate_select_only("DROP TABLE rides_historical_nyc")

    with pytest.raises(GuardrailViolation, match="one SQL statement"):
        validate_select_only("SELECT 1; SELECT 2")
