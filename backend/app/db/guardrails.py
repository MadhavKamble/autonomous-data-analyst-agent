"""sqlglot pre-check on generated SQL.

READ THIS FIRST — what this layer is and is not:

This is a UX/fail-fast layer, NOT the security boundary. The authoritative
enforcement is the agent_ro database role (migration 002): it has SELECT on
exactly three tables and nothing else, so writes, DDL and reads of
infrastructure tables fail inside Postgres regardless of anything Python does
or misses. The test suite deliberately attacks the executor with writes while
BYPASSING this pre-check, and must see the database itself reject them.

What this layer buys, then, is a clean reasoning-trace message *before* a
round trip to the database: "generated SQL must be a single SELECT statement"
reads better to a user (and re-prompts the SQL generator better) than a raw
Postgres permission error. It also cheaply rejects things permissions alone
would allow but we never want from an agent, like stacking multiple
statements in one string.

Checks (whitelist, not blacklist):
  1. Exactly one statement.
  2. Root must be SELECT or a set operation over SELECTs (UNION/…); WITH/CTEs
     parse into the SELECT node and are fine.
  3. No SELECT ... INTO (a write dressed as a SELECT).
  4. No row locking (FOR UPDATE/SHARE — meaningless read-only, so fail clean).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


class GuardrailViolation(Exception):
    """Generated SQL rejected before touching the database. Message is
    user/trace-facing and doubles as retry feedback to the SQL generator."""


# exp.Union is sqlglot's base for set operations, so UNION/INTERSECT/EXCEPT
# of SELECTs all pass through this whitelist.
_ALLOWED_ROOTS = (exp.Select, exp.Union)


def validate_select_only(sql: str) -> None:
    """Raise GuardrailViolation unless `sql` is one read-only SELECT statement."""
    if not sql or not sql.strip():
        raise GuardrailViolation("generated SQL is empty")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as error:
        raise GuardrailViolation(f"generated SQL does not parse: {error}") from error

    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        raise GuardrailViolation(
            f"expected exactly one SQL statement, got {len(statements)}"
        )

    statement = statements[0]
    if not isinstance(statement, _ALLOWED_ROOTS):
        raise GuardrailViolation(
            f"only SELECT queries are allowed, got {statement.key.upper()}"
        )

    if statement.find(exp.Into) is not None:
        raise GuardrailViolation("SELECT ... INTO is a write and is not allowed")

    for select in statement.find_all(exp.Select):
        if select.args.get("locks"):
            raise GuardrailViolation("row locking (FOR UPDATE/SHARE) is not allowed")
