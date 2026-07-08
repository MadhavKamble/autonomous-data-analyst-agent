"""Embedding providers behind a minimal swappable interface.

Only the vector retriever needs embeddings. The Protocol exists so the backend
can move to a hosted embedding API later (or a different local model) by
adding one class and one factory branch — retrieval and indexing code depend
only on `embed()` and `dimensions`.

Current implementation: local Ollama with nomic-embed-text (768 dims), used in
development. The deployed free-tier configuration uses RETRIEVER=lexical and
never constructs a provider at all.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import Settings


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input, in order."""
        ...


class OllamaEmbeddingProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int = 768,
        timeout: float = 60.0,
    ) -> None:
        self.dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def is_available(self) -> bool:
        """Cheap liveness probe, used by the index builder to degrade gracefully."""
        try:
            httpx.get(f"{self._base_url}/api/tags", timeout=3.0).raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        # /api/embed accepts a batch: one HTTP round trip for the whole corpus.
        response = httpx.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": texts},
            timeout=self._timeout,
        )
        response.raise_for_status()
        embeddings = response.json()["embeddings"]

        # Fail loudly on shape drift: a wrong-dimension vector would otherwise
        # surface as an opaque pgvector error at insert/query time.
        if len(embeddings) != len(texts):
            raise ValueError(f"asked for {len(texts)} embeddings, got {len(embeddings)}")
        for vector in embeddings:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"model '{self._model}' returned {len(vector)}-dim vectors, "
                    f"expected {self.dimensions} (vector column width, migration 004)"
                )
        return embeddings


def create_embedding_provider(settings: Settings) -> OllamaEmbeddingProvider:
    """Factory — the single place a future hosted provider gets wired in."""
    return OllamaEmbeddingProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_embed_model,
        dimensions=settings.embedding_dimensions,
    )
