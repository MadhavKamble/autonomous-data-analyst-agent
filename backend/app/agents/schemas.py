"""Typed output contracts for the four agents.

Every agent returns structured JSON validated against one of these models —
the orchestrator never string-matches free text. A response that doesn't
validate raises AgentOutputError (see base.py), which the orchestrator treats
as a failed attempt, not a crash.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PlannerOutput(BaseModel):
    steps: list[str] = Field(min_length=1, description="short imperative sub-steps")
    tables: list[str] = Field(default_factory=list, description="tables the plan expects to use")
    retrieval_query: str = Field(
        min_length=1,
        description="search phrase for the RAG step — the planner decides what context is needed",
    )


class SQLGeneratorOutput(BaseModel):
    sql: str = Field(min_length=1)
    rationale: str = ""

    @field_validator("sql")
    @classmethod
    def strip_sql(cls, value: str) -> str:
        # Trailing semicolons are legal but complicate single-statement
        # validation downstream; normalize here.
        return value.strip().rstrip(";").strip()


class CriticOutput(BaseModel):
    verdict: str  # "pass" | "fail"
    issues: list[str] = Field(default_factory=list)
    hint: str = Field(
        default="",
        description="concrete guidance for the next SQL attempt; empty on pass",
    )

    @field_validator("verdict")
    @classmethod
    def normalize_verdict(cls, value: str) -> str:
        verdict = value.strip().lower()
        if verdict not in ("pass", "fail"):
            raise ValueError(f"verdict must be 'pass' or 'fail', got {value!r}")
        return verdict

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


class SummarizerOutput(BaseModel):
    answer: str = Field(min_length=1, description="plain-English grounded answer")
    caveats: list[str] = Field(default_factory=list)
