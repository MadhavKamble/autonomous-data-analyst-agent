"""SQL generator agent, tested against the known schema (db/migrations/001).

No real LLM: ScriptedLLMClient returns realistic canned SQL, so these tests
are deterministic. "Against known schema" is taken literally — every SQL
string a fake response hands back is EXPLAIN'd against the real dev database,
proving it parses and references only columns that actually exist, the same
way scripts/build_rag_index.py validates the curated examples.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from app.agents.base import AgentOutputError
from app.agents.sql_generator import SQLGenerator
from app.rag.retriever import RetrievedChunk
from tests.fakes import ScriptedLLMClient

SAMPLE_CHUNKS = [
    RetrievedChunk(
        chunk_id="schema:zone_demand_historical_nyc",
        kind="schema_doc",
        content="TABLE zone_demand_historical_nyc: event_date (text), event_hour (integer), "
        "city_zone (text), ride_count (bigint), completed_rides (bigint), "
        "cancelled_rides (bigint), gross_revenue_inr (double precision).",
        score=0.9,
    ),
    RetrievedChunk(
        chunk_id="guidance:data-semantics",
        kind="guidance",
        content="cancelled_rides counts status='cancelled' only.",
        score=0.7,
    ),
]


def explain(admin_database_url: str, sql: str) -> None:
    """Asserts `sql` parses and type-checks against the real schema, without
    executing it (EXPLAIN never runs a SELECT's side-effect-free plan)."""
    with psycopg.connect(admin_database_url) as conn:
        conn.execute(f"EXPLAIN {sql}")


def test_generated_sql_is_valid_against_the_real_schema(admin_database_url: str) -> None:
    canned_sql = (
        "SELECT city_zone, round(100.0 * sum(cancelled_rides) / nullif(sum(ride_count), 0), 1) "
        "AS cancellation_pct FROM zone_demand_historical_nyc GROUP BY city_zone "
        "ORDER BY cancellation_pct DESC LIMIT 10"
    )
    llm = ScriptedLLMClient([json.dumps({"sql": canned_sql, "rationale": "ranks zones by rate"})])
    output = SQLGenerator(llm).generate(
        "Which zones have the highest cancellation rate?", ["plan step"], SAMPLE_CHUNKS
    )
    assert output.sql == canned_sql
    explain(admin_database_url, output.sql)  # raises if it references a bad column/table


def test_generated_sql_referencing_nonexistent_column_fails_explain(admin_database_url: str) -> None:
    """Negative control: proves explain() above would actually catch a
    hallucinated column, rather than trivially passing everything."""
    llm = ScriptedLLMClient(
        [json.dumps({"sql": "SELECT nonexistent_column FROM zone_demand", "rationale": ""})]
    )
    output = SQLGenerator(llm).generate("bad question", ["step"], SAMPLE_CHUNKS)
    with pytest.raises(psycopg.Error, match="column .* does not exist"):
        explain(admin_database_url, output.sql)


def test_trailing_semicolon_and_whitespace_are_stripped() -> None:
    llm = ScriptedLLMClient([json.dumps({"sql": "  SELECT 1;  ", "rationale": "r"})])
    output = SQLGenerator(llm).generate("q?", ["step"], SAMPLE_CHUNKS)
    assert output.sql == "SELECT 1"


def test_prompt_includes_question_plan_and_retrieved_context() -> None:
    llm = ScriptedLLMClient([json.dumps({"sql": "SELECT 1", "rationale": "r"})])
    SQLGenerator(llm).generate(
        "How many rides were cancelled?",
        ["Filter for cancelled rides", "Sum the count"],
        SAMPLE_CHUNKS,
    )
    sent = llm.calls[0]["user"]
    assert "How many rides were cancelled?" in sent
    assert "Filter for cancelled rides" in sent
    assert "cancelled_rides counts status='cancelled' only" in sent  # from the chunk content


def test_retry_feedback_is_threaded_into_the_prompt() -> None:
    """On a retry, the critic's hint (or an execution error) must reach the
    model — this is how the bounded retry loop actually improves attempt N+1."""
    llm = ScriptedLLMClient([json.dumps({"sql": "SELECT 1", "rationale": "r"})])
    SQLGenerator(llm).generate(
        "q?", ["step"], SAMPLE_CHUNKS, feedback="column s.date does not exist"
    )
    sent = llm.calls[0]["user"]
    assert "A previous attempt was rejected" in sent
    assert "column s.date does not exist" in sent


def test_no_feedback_on_first_attempt_leaves_prompt_clean() -> None:
    llm = ScriptedLLMClient([json.dumps({"sql": "SELECT 1", "rationale": "r"})])
    SQLGenerator(llm).generate("q?", ["step"], SAMPLE_CHUNKS)  # feedback defaults to ""
    assert "previous attempt" not in llm.calls[0]["user"].lower()


def test_missing_sql_key_raises_agent_output_error() -> None:
    llm = ScriptedLLMClient([json.dumps({"rationale": "oops, forgot the sql field"})])
    with pytest.raises(AgentOutputError) as exc_info:
        SQLGenerator(llm).generate("q?", ["step"], SAMPLE_CHUNKS)
    assert exc_info.value.agent == "sql_generator"


def test_non_json_response_raises_agent_output_error_with_raw_text_attached() -> None:
    llm = ScriptedLLMClient(["Sure! Here's a query: SELECT * FROM zone_demand"])
    with pytest.raises(AgentOutputError) as exc_info:
        SQLGenerator(llm).generate("q?", ["step"], SAMPLE_CHUNKS)
    assert "SELECT * FROM zone_demand" in exc_info.value.raw
