"""Planner agent: decomposes the question and decides what context to retrieve.

Its retrieval_query output drives the RAG step — the planner, not the raw user
question, decides what schema context the SQL generator needs. That is the
'decides what schema context is needed' responsibility from the design.
"""

from __future__ import annotations

from app.agents.base import Agent
from app.agents.schemas import PlannerOutput


class Planner(Agent[PlannerOutput]):
    name = "planner"
    template_file = "planner.txt"
    output_model = PlannerOutput

    def plan(self, question: str) -> PlannerOutput:
        return self.run(question=question)
