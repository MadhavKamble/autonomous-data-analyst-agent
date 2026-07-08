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

    # -- Embeddings (vector retriever only) ----------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"


@lru_cache
def get_settings() -> Settings:
    return Settings()
