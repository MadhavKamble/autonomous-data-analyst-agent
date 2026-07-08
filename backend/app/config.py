"""Central configuration, loaded once from the environment / backend/.env.

Every runtime toggle the system supports lives here (and is documented in
backend/.env.example) — components never read os.environ directly, so the full
configuration surface is visible in one place.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds vars for standalone scripts
    )

    # -- Database -----------------------------------------------------------
    # Owner/app connection: migrations, seeding, RAG index, sessions.
    admin_database_url: str = ""
    # Read-only agent_ro connection: used by the SQL executor ONLY.
    agent_database_url: str = ""

    # -- RAG retrieval ------------------------------------------------------
    # vector: pgvector + local Ollama embeddings (development).
    # lexical: Postgres full-text search (deployed free tier, where the local
    # Ollama instance is unreachable). Same corpus either way.
    retriever: Literal["vector", "lexical"] = "vector"
    rag_top_k: int = 4  # small on purpose: grounding context counts against Groq's ~12K TPM
    embedding_dimensions: int = 768  # must match vector(768) in migration 004

    # -- LLM backend ----------------------------------------------------------
    # groq: llama-3.3-70b-versatile (primary; client wrapper lands in step 8).
    # ollama: llama3.2:3b locally — offline dev and the Groq-rate-limit fallback.
    llm_backend: Literal["groq", "ollama"] = "groq"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_llm_model: str = "llama3.2:3b"

    # -- Ollama endpoint (shared by fallback LLM + dev embeddings) ------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"

    # -- Orchestration bounds --------------------------------------------------
    # Worst case per question: 1 planner + max_sql_attempts × (generator +
    # critic) + 1 summarizer = 8 LLM calls. llm_call_budget hard-caps TOTAL
    # calls (not attempts), so no failure mode can spend more.
    max_sql_attempts: int = 3
    llm_call_budget: int = 8
    sql_row_cap: int = 200
    sql_timeout_seconds: int = 10

    # -- HTTP ------------------------------------------------------------------
    # Comma-separated allowed origins for CORS; the deployed frontend's origin
    # plus localhost dev servers. "*" is acceptable for a public portfolio API
    # with no credentials, but explicit origins are the better default story.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
