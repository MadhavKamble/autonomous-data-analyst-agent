"""Test doubles for LLMClient. No test in this suite calls a real LLM: agent
logic (prompt rendering, JSON validation, retry wiring) is deterministic and
should be tested as such — live-model behavior was verified manually and is
documented in the README, not re-asserted here (it's non-deterministic by
nature)."""

from __future__ import annotations

from app.llm.client import LLMResponse


class ScriptedLLMClient:
    """Returns canned response texts in order; repeats the last one once the
    script runs out. Records every (system, user) prompt pair it was called
    with, so tests can assert on what an agent actually sent."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = True,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        text = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return LLMResponse(text=text, model="fake", prompt_tokens=0, completion_tokens=0)

    @property
    def call_count(self) -> int:
        return len(self.calls)
