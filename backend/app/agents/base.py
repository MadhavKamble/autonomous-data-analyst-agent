"""Shared agent scaffolding: template rendering, LLM call, JSON validation.

Each concrete agent is: a prompt template file + an output model + a typed
run() wrapper. This base keeps them uniform and tiny.

Template format (backend/app/prompts/<name>.txt): a system section and a user
section separated by a `=== USER ===` line. The system section is static —
byte-stable across calls, which is what lets provider-side prompt caching
help. The user section is a string.Template ($placeholders), chosen over
str.format so the JSON examples in prompts never need brace-escaping.

Output handling: agents request API-level JSON mode AND instruct JSON in the
prompt, then validate against the agent's pydantic model. Anything that fails
(unparseable text, missing keys, wrong types) raises AgentOutputError with the
raw response attached — the orchestrator counts it as a failed attempt within
the retry budget; it is never a crash and never silently ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.client import LLMClient

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_USER_DELIMITER = "=== USER ==="

OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentOutputError(Exception):
    """The LLM's response could not be parsed/validated into the output model."""

    def __init__(self, agent: str, reason: str, raw: str) -> None:
        super().__init__(f"{agent}: {reason}")
        self.agent = agent
        self.reason = reason
        self.raw = raw


def extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM response.

    JSON mode makes clean output the norm, but smaller models still
    occasionally wrap it in code fences or prose; scan to the first '{' and
    raw_decode from there instead of failing on the wrapper.
    """
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


class Agent(Generic[OutputT]):
    # Subclasses set these three class attributes.
    name: str
    template_file: str
    output_model: type[OutputT]

    def __init__(self, llm: LLMClient, max_tokens: int = 1024) -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        raw = (PROMPTS_DIR / self.template_file).read_text()
        if _USER_DELIMITER not in raw:
            raise ValueError(f"{self.template_file} is missing the '{_USER_DELIMITER}' line")
        system, user = raw.split(_USER_DELIMITER, 1)
        self._system_prompt = system.strip()
        self._user_template = Template(user.strip())

    def run(self, **context: str) -> OutputT:
        """Render the user prompt, call the LLM, validate the JSON output.

        substitute() (not safe_substitute) so a missing context key is a
        programming error caught immediately, not an LLM prompt with a
        literal '$placeholder' in it.
        """
        user_prompt = self._user_template.substitute(context)
        response = self._llm.complete(
            system=self._system_prompt,
            user=user_prompt,
            json_mode=True,
            max_tokens=self._max_tokens,
            temperature=0.0,  # deterministic-ish: repeatability over creativity
        )
        try:
            data = extract_json(response.text)
            return self.output_model.model_validate(data)
        except (ValueError, ValidationError) as exc:
            raise AgentOutputError(self.name, str(exc), response.text) from exc
