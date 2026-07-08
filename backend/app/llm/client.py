"""LLM client seam: the one interface every agent talks through.

Agents depend only on this Protocol, so the backend is a config toggle
(LLM_BACKEND=groq|ollama), not a code change:

  groq    llama-3.3-70b-versatile — primary, hardened wrapper with 429
          backoff and token accounting lands in step 8.
  ollama  llama3.2:3b locally — offline development and the fallback when
          Groq rate limits bite.

`json_mode=True` asks the backend to *enforce* JSON output at the API level
(Ollama `format: json`, Groq `response_format: json_object`) rather than
relying on prompt discipline alone; all four agents use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import Settings


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = True,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


class LLMBackendError(Exception):
    """The backend failed to produce a completion (network, HTTP, model error)."""


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_backend == "ollama":
        from app.llm.ollama_client import OllamaLLMClient

        return OllamaLLMClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_llm_model,
        )
    # Groq wrapper (backoff, 429 handling, token budget) is step 8.
    raise NotImplementedError(
        "LLM_BACKEND=groq is wired up in step 8; set LLM_BACKEND=ollama for local development."
    )
