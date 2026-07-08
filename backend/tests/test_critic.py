"""Critic agent: pass/fail verdict parsing and judgment logic. No real LLM —
ScriptedLLMClient stands in so these are deterministic and fast."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agents.base import AgentOutputError
from app.agents.critic import Critic
from app.agents.schemas import CriticOutput
from tests.fakes import ScriptedLLMClient


def test_pass_verdict_parses_and_reports_passed_true() -> None:
    llm = ScriptedLLMClient([json.dumps({"verdict": "pass", "issues": [], "hint": ""})])
    result = Critic(llm).review("q?", "SELECT 1", "columns: ['x']\nrows: [[1]]")
    assert result.verdict == "pass"
    assert result.passed is True
    assert result.issues == []


def test_fail_verdict_carries_issues_and_hint_for_retry() -> None:
    llm = ScriptedLLMClient(
        [
            json.dumps(
                {
                    "verdict": "fail",
                    "issues": ["averaged an unfiltered fare column"],
                    "hint": "filter status = 'completed' before averaging gross_fare_inr",
                }
            )
        ]
    )
    result = Critic(llm).review("avg fare?", "SELECT avg(gross_fare_inr) FROM rides_historical_nyc", "...")
    assert result.verdict == "fail"
    assert result.passed is False
    assert "filter status = 'completed'" in result.hint
    assert result.issues == ["averaged an unfiltered fare column"]


@pytest.mark.parametrize("raw_verdict", ["PASS", "Pass", " pass ", "FAIL", "Fail"])
def test_verdict_is_case_and_whitespace_normalized(raw_verdict: str) -> None:
    output = CriticOutput.model_validate({"verdict": raw_verdict, "issues": [], "hint": ""})
    assert output.verdict in ("pass", "fail")
    assert output.passed == (output.verdict == "pass")


@pytest.mark.parametrize("bad_verdict", ["maybe", "yes", "", "passed", "failing"])
def test_invalid_verdict_value_is_rejected(bad_verdict: str) -> None:
    with pytest.raises(ValidationError):
        CriticOutput.model_validate({"verdict": bad_verdict, "issues": [], "hint": ""})


def test_garbage_llm_output_raises_agent_output_error_not_a_silent_pass() -> None:
    """A critic that can't be parsed must never be silently treated as a
    pass — that would let a bad result through. base.py raises
    AgentOutputError; the orchestrator (tested separately) converts that into
    a conservative fail, never an automatic pass."""
    llm = ScriptedLLMClient(["I think this looks fine!"])
    with pytest.raises(AgentOutputError) as exc_info:
        Critic(llm).review("q?", "SELECT 1", "...")
    assert exc_info.value.agent == "critic"


def test_missing_verdict_key_raises_agent_output_error() -> None:
    llm = ScriptedLLMClient([json.dumps({"issues": [], "hint": ""})])  # no "verdict"
    with pytest.raises(AgentOutputError):
        Critic(llm).review("q?", "SELECT 1", "...")


def test_review_sends_question_sql_and_result_to_the_model() -> None:
    """The prompt actually carries what the critic is supposed to judge."""
    llm = ScriptedLLMClient([json.dumps({"verdict": "pass", "issues": [], "hint": ""})])
    Critic(llm).review(
        "Which zone has the most cancellations?",
        "SELECT city_zone FROM zone_demand_historical_nyc",
        "columns: ['city_zone']\nrows: [['Harlem']]",
    )
    sent = llm.calls[0]["user"]
    assert "Which zone has the most cancellations?" in sent
    assert "SELECT city_zone FROM zone_demand_historical_nyc" in sent
    assert "Harlem" in sent
