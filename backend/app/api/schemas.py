"""Request/response models for the HTTP API. The interesting shape —
AskResult with its reasoning trace — is defined in orchestrator/trace.py and
returned verbatim; these models are the thin envelope around it."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from pydantic import BaseModel, Field

from app.orchestrator.trace import AskResult


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Optional: continue an existing conversation. Absent -> a new session is
    # created and its id returned, so the client never mints ids itself.
    session_id: UUID | None = None


class AskResponse(BaseModel):
    session_id: UUID
    message_id: int  # the persisted assistant message (proof it hit the DB)
    result: AskResult


class SessionSummary(BaseModel):
    id: UUID
    title: str
    created_at: dt.datetime
    last_active_at: dt.datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    trace: AskResult | None = None  # full stored payload for assistant messages
    created_at: dt.datetime


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    database: str  # "ok" | "unreachable"
