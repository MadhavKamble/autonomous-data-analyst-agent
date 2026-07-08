"""SQL executor: runs generated SQL against the read-only agent_ro connection.

Layering (the distinction matters — see guardrails.py):
  1. guardrails.validate_select_only — Python-side pre-check, exists only to
     fail fast with a clean message. NOT the security boundary.
  2. The agent_ro role's grants — the actual enforcement. Even SQL that
     sneaks past (or bypasses) the pre-check cannot write, run DDL, or read
     infrastructure tables, because Postgres itself refuses.

Belt-and-braces on the session too: the connection is opened read-only with a
statement timeout, so even a pathological SELECT (cartesian join) is bounded
server-side, and results are capped at row_cap rows client-side.

Everything — guardrail rejections, database errors, successes — comes back as
a structured ExecutionResult. The executor never raises on bad SQL: a failed
execution is data for the critic/retry loop, not an exception.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from decimal import Decimal

import psycopg

from app.db.guardrails import GuardrailViolation, validate_select_only


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)  # JSON-safe values
    row_count: int = 0  # rows returned (post-cap)
    truncated: bool = False  # True if row_cap cut the result off
    error: str | None = None  # guardrail or database error, user/trace-facing
    duration_ms: int = 0

    def preview(self, max_rows: int = 10) -> str:
        """Compact JSON-ish preview handed to the critic and summarizer."""
        if not self.success:
            return f"EXECUTION ERROR: {self.error}"
        shown = self.rows[:max_rows]
        lines = [
            f"columns: {self.columns}",
            f"row_count: {self.row_count}" + (" (truncated by row cap)" if self.truncated else ""),
            f"rows (first {len(shown)}):",
        ]
        lines += [str(row) for row in shown]
        return "\n".join(lines)


def _json_safe(value):
    """Coerce driver types into values that survive JSON serialization
    (trace and API responses) without losing meaning."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return value.hex()
    return value


class SQLExecutor:
    def __init__(self, agent_db_url: str, row_cap: int = 200, timeout_seconds: int = 10) -> None:
        self._db_url = agent_db_url
        self._row_cap = row_cap
        # Applied at session level; role-level defaults from migration 002
        # back this up if a future caller forgets.
        self._options = (
            f"-c statement_timeout={timeout_seconds * 1000} "
            "-c default_transaction_read_only=on"
        )

    def execute(self, sql: str) -> ExecutionResult:
        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        # Layer 1: fail fast with a clean message (UX, not security).
        try:
            validate_select_only(sql)
        except GuardrailViolation as violation:
            return ExecutionResult(success=False, error=str(violation), duration_ms=elapsed_ms())

        # Layer 2: the read-only role. Any write/DDL that reaches this point
        # is rejected by Postgres permissions, and surfaces here as an error.
        try:
            with psycopg.connect(self._db_url, options=self._options) as conn:
                cursor = conn.execute(sql)
                columns = [d.name for d in cursor.description] if cursor.description else []
                # Fetch one extra row purely to detect truncation.
                raw_rows = cursor.fetchmany(self._row_cap + 1)
        except psycopg.Error as error:
            # First line only: Postgres appends LINE/HINT context that is
            # noise in a trace (the critic gets enough from the message).
            message = str(error).strip().splitlines()[0]
            return ExecutionResult(success=False, error=message, duration_ms=elapsed_ms())

        truncated = len(raw_rows) > self._row_cap
        rows = [[_json_safe(v) for v in row] for row in raw_rows[: self._row_cap]]
        return ExecutionResult(
            success=True,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=elapsed_ms(),
        )
