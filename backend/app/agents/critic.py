"""Critic agent: judges whether an executed result actually answers the
question. Its fail verdict (with a concrete hint) drives the bounded retry
loop; its pass verdict is the gate to summarization."""

from __future__ import annotations

from app.agents.base import Agent
from app.agents.schemas import CriticOutput


class Critic(Agent[CriticOutput]):
    name = "critic"
    template_file = "critic.txt"
    output_model = CriticOutput

    def review(self, question: str, sql: str, result_preview: str) -> CriticOutput:
        return self.run(question=question, sql=sql, result_preview=result_preview)
