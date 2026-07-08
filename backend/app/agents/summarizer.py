"""Summarizer agent: turns the final result into a grounded plain-English
answer. Only ever sees real result data — it runs after the critic passes."""

from __future__ import annotations

from app.agents.base import Agent
from app.agents.schemas import SummarizerOutput


class Summarizer(Agent[SummarizerOutput]):
    name = "summarizer"
    template_file = "summarizer.txt"
    output_model = SummarizerOutput

    def summarize(self, question: str, sql: str, result_preview: str) -> SummarizerOutput:
        return self.run(question=question, sql=sql, result_preview=result_preview)
