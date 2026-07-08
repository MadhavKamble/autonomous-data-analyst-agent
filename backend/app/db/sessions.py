"""Conversation persistence. Postgres is the ONLY store — nothing session-
related may live in process memory (Render free tier loses the process on
idle). Every write here happens before /ask returns, so a cold-started
process reconstructs any conversation from the database alone."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    title: str
    created_at: dt.datetime
    last_active_at: dt.datetime


@dataclass(frozen=True)
class MessageRecord:
    id: int
    session_id: UUID
    role: str  # 'user' | 'assistant'
    content: str
    trace: dict | None  # full AskResult payload for assistant messages
    created_at: dt.datetime


class SessionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create_session(self, title: str) -> SessionRecord:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO sessions (title) VALUES (%s) "
                "RETURNING id, title, created_at, last_active_at",
                (title[:80],),
            ).fetchone()
        return SessionRecord(*row)

    def get_session(self, session_id: UUID) -> SessionRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, title, created_at, last_active_at FROM sessions WHERE id = %s",
                (session_id,),
            ).fetchone()
        return SessionRecord(*row) if row else None

    def list_sessions(self, limit: int = 50) -> list[SessionRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, last_active_at FROM sessions "
                "ORDER BY last_active_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [SessionRecord(*row) for row in rows]

    def delete_session(self, session_id: UUID) -> bool:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM sessions WHERE id = %s", (session_id,)
            ).rowcount
        return deleted > 0  # messages cascade

    def add_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        trace: dict | None = None,
    ) -> MessageRecord:
        """Insert a message and bump the session's activity timestamp in one
        transaction — the pair must never diverge."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO messages (session_id, role, content, trace) "
                "VALUES (%s, %s, %s, %s) "
                "RETURNING id, session_id, role, content, trace, created_at",
                (session_id, role, content, Jsonb(trace) if trace is not None else None),
            ).fetchone()
            conn.execute(
                "UPDATE sessions SET last_active_at = now() WHERE id = %s", (session_id,)
            )
        return MessageRecord(*row)

    def list_messages(self, session_id: UUID) -> list[MessageRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, session_id, role, content, trace, created_at "
                "FROM messages WHERE session_id = %s ORDER BY id",
                (session_id,),
            ).fetchall()
        return [MessageRecord(*row) for row in rows]
