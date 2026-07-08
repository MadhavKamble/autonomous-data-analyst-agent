"""SQL generator agent: writes one read-only PostgreSQL query, grounded in the
retrieved schema context. On retries it also receives the critic's feedback."""

from __future__ import annotations

from app.agents.base import Agent
from app.agents.schemas import SQLGeneratorOutput
from app.rag.retriever import RetrievedChunk


class SQLGenerator(Agent[SQLGeneratorOutput]):
    name = "sql_generator"
    template_file = "sql_generator.txt"
    output_model = SQLGeneratorOutput

    def generate(
        self,
        question: str,
        plan_steps: list[str],
        chunks: list[RetrievedChunk],
        feedback: str = "",
    ) -> SQLGeneratorOutput:
        return self.run(
            question=question,
            plan="\n".join(f"{i}. {step}" for i, step in enumerate(plan_steps, 1)),
            context="\n\n---\n\n".join(chunk.content for chunk in chunks),
            # On the first attempt this renders as an empty line; on retries it
            # carries the critic's diagnosis of the previous attempt.
            feedback=(
                f"A previous attempt was rejected. Fix this: {feedback}" if feedback else ""
            ),
        )
