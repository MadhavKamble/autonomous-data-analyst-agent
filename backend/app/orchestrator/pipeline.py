"""The orchestrator: plan → retrieve → generate → execute → critique → summarize,
with a bounded retry loop and a hard per-question LLM-call budget.

Control flow rules this module owns:

- Fixed pipeline, not an autonomous tool-choosing agent: every question takes
  the same auditable path, which is what makes the trace meaningful and the
  cost predictable.
- The retry loop is bounded two ways: MAX_SQL_ATTEMPTS attempts, and a hard
  LLM_CALL_BUDGET on total LLM calls (worst case 1 planner + 3×(generator +
  critic) + 1 summarizer = 8). The budget counts CALLS, not attempts, so a
  storm of malformed-JSON responses cannot spend more than the cap either.
- Budget economy: when execution itself fails (guardrail or database error),
  the error text becomes the retry feedback directly and the critic is NOT
  called — a database error already is a verdict. Critic calls are reserved
  for results that executed and need semantic judgment.
- ask() never raises. Every outcome — success, retries exhausted, budget
  exhausted, LLM backend down, even an unexpected bug — returns an AskResult
  with a clean user-facing failure_reason and the trace accumulated so far.
  Exceptions must not leak to the HTTP layer or the frontend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.agents.base import AgentOutputError
from app.agents.critic import Critic
from app.agents.planner import Planner
from app.agents.schemas import CriticOutput
from app.agents.sql_generator import SQLGenerator
from app.agents.summarizer import Summarizer
from app.config import Settings
from app.db.executor import ExecutionResult, SQLExecutor
from app.llm.client import LLMBackendError, LLMClient, create_llm_client
from app.orchestrator.trace import (
    AskResult,
    AttemptTrace,
    CriticTrace,
    ExecutionTrace,
    ReasoningTrace,
    ResultData,
    RetrievedChunkTrace,
)
from app.rag.retriever import ConnectFn, RetrievedChunk, Retriever, create_retriever

logger = logging.getLogger(__name__)

BUSY_MESSAGE = (
    "The agent could not finish within its per-question reasoning budget. "
    "Please try rephrasing the question or asking something simpler."
)
BACKEND_DOWN_MESSAGE = (
    "The language-model backend is currently unavailable or rate-limited. "
    "Please try again in a moment."
)
INTERNAL_ERROR_MESSAGE = "Something went wrong inside the agent. Please try again."


class BudgetExhausted(Exception):
    """Internal signal: the per-question LLM-call budget ran out."""


@dataclass
class LLMBudget:
    limit: int
    used: int = 0

    def spend(self) -> None:
        """Reserve one LLM call; raises BEFORE the call so the cap is hard."""
        if self.used >= self.limit:
            raise BudgetExhausted()
        self.used += 1


class Pipeline:
    def __init__(
        self,
        llm: LLMClient,
        retriever: Retriever,
        executor: SQLExecutor,
        max_attempts: int = 3,
        llm_call_budget: int = 8,
    ) -> None:
        self._planner = Planner(llm)
        self._sql_generator = SQLGenerator(llm)
        self._critic = Critic(llm)
        self._summarizer = Summarizer(llm)
        self._retriever = retriever
        self._executor = executor
        self._max_attempts = max_attempts
        self._llm_call_budget = llm_call_budget

    # -- public entry point ---------------------------------------------------

    def ask(self, question: str) -> AskResult:
        budget = LLMBudget(self._llm_call_budget)
        trace = ReasoningTrace(llm_call_budget=budget.limit)
        try:
            return self._run(question, budget, trace)
        except BudgetExhausted:
            return self._fail(BUSY_MESSAGE, budget, trace)
        except LLMBackendError as error:
            logger.warning("LLM backend error: %s", error)
            return self._fail(BACKEND_DOWN_MESSAGE, budget, trace)
        except Exception:  # the never-leak guarantee for the HTTP layer
            logger.exception("unexpected pipeline error")
            return self._fail(INTERNAL_ERROR_MESSAGE, budget, trace)

    # -- pipeline stages --------------------------------------------------------

    def _run(self, question: str, budget: LLMBudget, trace: ReasoningTrace) -> AskResult:
        # 1. Plan (1 call). No retry at this stage: the budget arithmetic
        # reserves retries for SQL generation, where they pay off.
        budget.spend()
        try:
            plan = self._planner.plan(question)
        except AgentOutputError as error:
            logger.warning("planner produced invalid output: %s", error.reason)
            return self._fail(
                "The agent could not form a plan for this question. Try rephrasing it.",
                budget,
                trace,
            )
        trace.planner = plan

        # 2..4. Bounded generate → execute → critique loop.
        feedback = ""
        for attempt_number in range(1, self._max_attempts + 1):
            attempt = AttemptTrace(attempt=attempt_number)
            trace.attempts.append(attempt)

            # Retrieval is per-attempt and free (no LLM call). The planner's
            # query drives it; critic/executor feedback enriches it on retries
            # so the next attempt can surface different chunks.
            chunks = self._retriever.retrieve(
                f"{plan.retrieval_query} {feedback}".strip() if feedback else plan.retrieval_query
            )
            attempt.retrieved = [_chunk_trace(c) for c in chunks]

            budget.spend()
            try:
                generated = self._sql_generator.generate(
                    question, plan.steps, chunks, feedback=feedback
                )
            except AgentOutputError as error:
                # Malformed generator output burns the attempt, not the run.
                attempt.failure = f"SQL generator returned invalid output: {error.reason}"
                feedback = "Your previous response was not valid JSON with a 'sql' key. " \
                           "Return exactly the JSON object described."
                continue
            attempt.sql = generated.sql
            attempt.rationale = generated.rationale

            execution = self._executor.execute(generated.sql)
            attempt.execution = _execution_trace(execution)

            if not execution.success:
                # Guardrail or database error: that IS the verdict. Feed the
                # error straight back and save the critic call for results
                # that need semantic judgment.
                feedback = f"The SQL failed to execute: {execution.error}"
                continue

            budget.spend()
            critique = self._safe_critique(question, generated.sql, execution)
            attempt.critic = CriticTrace(
                verdict=critique.verdict, issues=critique.issues, hint=critique.hint
            )
            if critique.passed:
                return self._summarize(question, generated.sql, execution, budget, trace)
            feedback = critique.hint or "; ".join(critique.issues) or "the result did not answer the question"

        return self._fail(
            f"The agent tried {self._max_attempts} times but could not produce a query "
            "that answers this question. The reasoning trace shows each attempt.",
            budget,
            trace,
        )

    def _safe_critique(self, question: str, sql: str, execution: ExecutionResult) -> CriticOutput:
        """A critic that fails to answer must never crash the run — treat its
        invalid output as a conservative 'fail' so the loop keeps its shape."""
        try:
            return self._critic.review(question, sql, execution.preview())
        except AgentOutputError as error:
            logger.warning("critic produced invalid output: %s", error.reason)
            return CriticOutput(
                verdict="fail",
                issues=["critic output was unparseable; retrying to be safe"],
                hint="",
            )

    def _summarize(
        self,
        question: str,
        sql: str,
        execution: ExecutionResult,
        budget: LLMBudget,
        trace: ReasoningTrace,
    ) -> AskResult:
        data = ResultData(
            columns=execution.columns, rows=execution.rows, truncated=execution.truncated
        )
        budget.spend()
        try:
            summary = self._summarizer.summarize(question, sql, execution.preview())
            answer, caveats = summary.answer, summary.caveats
        except AgentOutputError as error:
            # The data is good (critic passed) — a summarizer glitch should
            # degrade the prose, not the outcome.
            logger.warning("summarizer produced invalid output: %s", error.reason)
            answer = "The query succeeded; see the result table below."
            caveats = ["The plain-English summary could not be generated for this answer."]
        trace.llm_calls_used = budget.used
        return AskResult(
            status="ok", answer=answer, caveats=caveats, data=data, trace=trace
        )

    def _fail(self, reason: str, budget: LLMBudget, trace: ReasoningTrace) -> AskResult:
        trace.llm_calls_used = budget.used
        return AskResult(status="failed", failure_reason=reason, trace=trace)


# -- trace helpers -------------------------------------------------------------

def _chunk_trace(chunk: RetrievedChunk) -> RetrievedChunkTrace:
    return RetrievedChunkTrace(
        chunk_id=chunk.chunk_id,
        kind=chunk.kind,
        score=round(chunk.score, 4),
        preview=chunk.content[:150],
    )


def _execution_trace(execution: ExecutionResult) -> ExecutionTrace:
    return ExecutionTrace(
        success=execution.success,
        columns=execution.columns,
        sample_rows=execution.rows[:5],
        row_count=execution.row_count,
        truncated=execution.truncated,
        error=execution.error,
        duration_ms=execution.duration_ms,
    )


def create_pipeline(settings: Settings, connect: ConnectFn | None = None) -> Pipeline:
    """Composition root: everything the pipeline needs, wired from config.

    `connect` (app-state DB access for retrieval) comes from the FastAPI app's
    shared pool; omitted in scripts, which then use per-call connections. The
    executor is NOT pooled by design — see db/engine.py.
    """
    return Pipeline(
        llm=create_llm_client(settings),
        retriever=create_retriever(settings, connect=connect),
        executor=SQLExecutor(
            agent_db_url=settings.agent_database_url,
            row_cap=settings.sql_row_cap,
            timeout_seconds=settings.sql_timeout_seconds,
        ),
        max_attempts=settings.max_sql_attempts,
        llm_call_budget=settings.llm_call_budget,
    )
