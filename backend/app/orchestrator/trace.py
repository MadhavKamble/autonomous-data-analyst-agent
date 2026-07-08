"""Reasoning-trace models — the /ask response contract.

Returning the full trace to the frontend is a deliberate design choice, not a
debug leftover: the user sees what was planned, which context was retrieved,
every SQL attempt with the critic's judgment, and how much of the LLM budget
was spent. These models are the single source of truth for that shape — the
FastAPI layer returns them directly and the React trace panel mirrors them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.schemas import PlannerOutput


class RetrievedChunkTrace(BaseModel):
    chunk_id: str
    kind: str
    score: float
    preview: str  # first ~150 chars — enough for the panel without bloating payloads


class ExecutionTrace(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    sample_rows: list[list] = Field(default_factory=list)  # first 5 only; full data lives in AskResult.data
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
    duration_ms: int = 0


class CriticTrace(BaseModel):
    verdict: str
    issues: list[str] = Field(default_factory=list)
    hint: str = ""


class AttemptTrace(BaseModel):
    attempt: int
    retrieved: list[RetrievedChunkTrace] = Field(default_factory=list)
    sql: str | None = None
    rationale: str = ""
    execution: ExecutionTrace | None = None
    # None when the critic never ran: the attempt died before/at execution
    # (feedback then comes from the guardrail/database error, saving a call).
    critic: CriticTrace | None = None
    # Set when the attempt failed outside SQL semantics, e.g. the generator
    # returned unparseable JSON.
    failure: str | None = None


class ReasoningTrace(BaseModel):
    planner: PlannerOutput | None = None
    attempts: list[AttemptTrace] = Field(default_factory=list)
    llm_calls_used: int = 0
    llm_call_budget: int = 0


class ResultData(BaseModel):
    """The final tabular result (already capped by the executor's row cap)."""

    columns: list[str]
    rows: list[list]
    truncated: bool


class AskResult(BaseModel):
    status: Literal["ok", "failed"]
    answer: str | None = None
    caveats: list[str] = Field(default_factory=list)
    # Clean, user-facing sentence when status == "failed". Never a stack trace.
    failure_reason: str | None = None
    data: ResultData | None = None
    trace: ReasoningTrace
