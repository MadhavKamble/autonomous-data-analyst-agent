"""Orchestrator: bounded retry loop + hard LLM-call budget (app/orchestrator/pipeline.py).

Uses a ScriptedLLMClient (no real model, fully deterministic) but a REAL
executor against agent_ro and a real LexicalRetriever against the seeded
rag_chunks corpus — the retry loop's interaction with the actual database
(guardrail rejections, execution errors, row data) is exactly what production
sees; only the LLM is faked. This mirrors the ad hoc verification done live
against Groq during development, now pinned as regression tests.
"""

from __future__ import annotations

import json

import psycopg

from app.db.executor import SQLExecutor
from app.orchestrator.pipeline import BUSY_MESSAGE, Pipeline
from app.rag.retriever import LexicalRetriever
from tests.fakes import ScriptedLLMClient

PLAN = json.dumps({"steps": ["do it"], "tables": ["zone_demand"], "retrieval_query": "zones"})
GOOD_SQL = json.dumps({"sql": "SELECT city_zone FROM zone_demand LIMIT 3", "rationale": "r"})
CRITIC_FAIL = json.dumps({"verdict": "fail", "issues": ["wrong"], "hint": "try again"})
CRITIC_PASS = json.dumps({"verdict": "pass", "issues": [], "hint": ""})
SUMMARY = json.dumps({"answer": "Three zones returned.", "caveats": []})
EVIL_SQL = json.dumps({"sql": "DROP TABLE rides_historical_nyc", "rationale": ""})


def make_pipeline(
    llm: ScriptedLLMClient,
    admin_database_url: str,
    agent_database_url: str,
    max_attempts: int = 3,
    budget: int = 8,
) -> Pipeline:
    # Mirrors create_pipeline's wiring exactly: retrieval (rag_chunks, sessions)
    # runs under the app role; the executor is the ONLY thing that ever
    # touches agent_ro. Retrieval over agent_ro would fail — rag_chunks has no
    # grant for that role by design (migration 004).
    return Pipeline(
        llm=llm,
        retriever=LexicalRetriever(lambda: psycopg.connect(admin_database_url), top_k=2),
        executor=SQLExecutor(agent_database_url, row_cap=200, timeout_seconds=10),
        max_attempts=max_attempts,
        llm_call_budget=budget,
    )


def test_attempts_exhausted_stops_at_max_attempts_with_clean_failure(
    admin_database_url: str, agent_database_url: str
) -> None:
    llm = ScriptedLLMClient([PLAN, GOOD_SQL, CRITIC_FAIL, GOOD_SQL, CRITIC_FAIL, GOOD_SQL, CRITIC_FAIL])
    result = make_pipeline(llm, admin_database_url, agent_database_url).ask("q?")

    assert result.status == "failed"
    assert len(result.trace.attempts) == 3
    assert llm.call_count == 7  # 1 planner + 3 x (generator + critic)
    assert result.trace.llm_calls_used == 7
    assert "tried 3 times" in result.failure_reason


def test_budget_smaller_than_loop_needs_stops_with_busy_message(
    admin_database_url: str, agent_database_url: str
) -> None:
    llm = ScriptedLLMClient([PLAN, GOOD_SQL, CRITIC_FAIL, GOOD_SQL, CRITIC_FAIL])
    result = make_pipeline(llm, admin_database_url, agent_database_url, budget=3).ask("q?")

    assert result.status == "failed"
    assert result.failure_reason == BUSY_MESSAGE
    assert llm.call_count == 3  # never exceeds the budget, even mid-loop
    assert result.trace.llm_calls_used == 3


def test_garbage_generator_output_burns_attempts_not_the_whole_run(
    admin_database_url: str, agent_database_url: str
) -> None:
    """A generator that never returns valid JSON must not crash the pipeline
    — each bad response costs one attempt, and the run still ends cleanly."""
    llm = ScriptedLLMClient([PLAN, "not json at all"])
    result = make_pipeline(llm, admin_database_url, agent_database_url).ask("q?")

    assert result.status == "failed"
    assert llm.call_count == 4  # 1 planner + 3 garbage-generator attempts
    assert all(attempt.failure for attempt in result.trace.attempts)
    assert len(result.trace.attempts) == 3


def test_happy_path_returns_answer_and_real_data(
    admin_database_url: str, agent_database_url: str
) -> None:
    llm = ScriptedLLMClient([PLAN, GOOD_SQL, CRITIC_PASS, SUMMARY])
    result = make_pipeline(llm, admin_database_url, agent_database_url).ask("q?")

    assert result.status == "ok"
    assert result.answer == "Three zones returned."
    assert result.data is not None
    assert result.data.columns == ["city_zone"]
    assert len(result.data.rows) == 3
    assert llm.call_count == 4
    assert result.trace.llm_calls_used == 4


def test_write_attempt_from_model_recovers_next_attempt_without_calling_critic(
    admin_database_url: str, agent_database_url: str,
) -> None:
    """If a hallucinating model emits a write, the guardrail (not a DB round
    trip) rejects it immediately, its error becomes retry feedback, and the
    critic is deliberately skipped for that attempt — budget economy: a
    failed execution already IS a verdict. This is a defense-in-depth check
    on top of test_readonly_enforcement.py, which proves the DB itself would
    also reject it if this pre-check were somehow bypassed."""
    llm = ScriptedLLMClient([PLAN, EVIL_SQL, GOOD_SQL, CRITIC_PASS, SUMMARY])
    result = make_pipeline(llm, admin_database_url, agent_database_url).ask("q?")

    assert result.status == "ok"
    first_attempt = result.trace.attempts[0]
    assert first_attempt.execution is not None
    assert first_attempt.execution.success is False
    assert first_attempt.critic is None  # budget economy: critic skipped
    assert "only SELECT" in first_attempt.execution.error
    assert llm.call_count == 5  # plan, evil-gen, retry-gen, critic, summary


def test_failed_run_trace_is_json_serializable(
    admin_database_url: str, agent_database_url: str
) -> None:
    """The trace is the /ask response contract — it must serialize cleanly
    even on failure, since the API persists and returns it either way."""
    llm = ScriptedLLMClient([PLAN, GOOD_SQL, CRITIC_FAIL, GOOD_SQL, CRITIC_FAIL, GOOD_SQL, CRITIC_FAIL])
    result = make_pipeline(llm, admin_database_url, agent_database_url).ask("q?")
    json.dumps(result.model_dump(mode="json"))  # raises on anything non-serializable


def test_pipeline_never_raises_even_when_llm_client_itself_throws(
    admin_database_url: str, agent_database_url: str
) -> None:
    """The never-leak guarantee: an unexpected exception anywhere in the loop
    must come back as a clean AskResult, never propagate to the caller."""

    class ExplodingLLM:
        def complete(self, **kwargs):
            raise RuntimeError("simulated unexpected failure")

    result = make_pipeline(ExplodingLLM(), admin_database_url, agent_database_url).ask("q?")
    assert result.status == "failed"
    assert result.failure_reason is not None
    assert "please try again" in result.failure_reason.lower()
