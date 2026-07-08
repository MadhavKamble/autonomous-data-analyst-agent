"""Top-k retrieval over rag_chunks, behind one interface with two strategies.

VectorRetriever  — cosine similarity over pgvector embeddings (development,
                   requires an EmbeddingProvider for the query).
LexicalRetriever — Postgres full-text ranking (deployed free tier, where no
                   embedding backend is reachable).

Both rank the SAME corpus (rag_chunks, migration 004); which one runs is the
RETRIEVER config toggle. Scores are only meaningful within a strategy —
cosine similarity and ts_rank_cd are not comparable to each other.

Never-empty guarantee: if a strategy finds nothing (a question sharing no
lexemes with the corpus, or an index built without embeddings), retrieval
falls back to the schema-doc and guidance chunks instead of returning [].
The SQL generator downstream must always receive schema context — an empty
grounding context is how hallucinated columns happen.

Database access goes through an injected `connect` callable returning a
connection context manager — `pool.connection` inside the FastAPI app (shared
app pool), `psycopg.connect(url)` in standalone scripts. The retriever neither
knows nor cares which.
"""

from __future__ import annotations

import json
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Protocol

import psycopg

from app.config import Settings
from app.rag.embeddings import EmbeddingProvider, create_embedding_provider

# pool.connection and a psycopg.connect(...) closure both satisfy this.
ConnectFn = Callable[[], AbstractContextManager[psycopg.Connection]]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    kind: str  # schema_doc | query_example | guidance
    content: str
    score: float  # strategy-specific; see module docstring


class Retriever(Protocol):
    def retrieve(self, question: str) -> list[RetrievedChunk]: ...


# Fallback: schema docs first (the generator cannot work without them), then
# guidance, then examples. Deterministic order so behavior is testable.
_FALLBACK_SQL = """
    SELECT id, kind, content, 0.0 AS score
    FROM rag_chunks
    ORDER BY CASE kind
                 WHEN 'schema_doc' THEN 0
                 WHEN 'guidance' THEN 1
                 ELSE 2
             END,
             id
    LIMIT %s
"""


def _fallback(conn: psycopg.Connection, top_k: int) -> list[RetrievedChunk]:
    rows = conn.execute(_FALLBACK_SQL, (top_k,)).fetchall()
    return [RetrievedChunk(*row) for row in rows]


class VectorRetriever:
    def __init__(self, connect: ConnectFn, embedder: EmbeddingProvider, top_k: int = 4) -> None:
        self._connect = connect
        self._embedder = embedder
        self._top_k = top_k

    def retrieve(self, question: str) -> list[RetrievedChunk]:
        [query_vector] = self._embedder.embed([question])
        with self._connect() as conn:
            rows = conn.execute(
                # <=> is pgvector cosine distance; report 1 - distance so the
                # score reads as similarity (higher = better). Exact scan by
                # design — see migration 004.
                """
                SELECT id, kind, content, 1 - (embedding <=> %(q)s::vector) AS score
                FROM rag_chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %(q)s::vector
                LIMIT %(k)s
                """,
                {"q": json.dumps(query_vector), "k": self._top_k},
            ).fetchall()
            if not rows:  # index was built without embeddings
                return _fallback(conn, self._top_k)
        return [RetrievedChunk(*row) for row in rows]


class LexicalRetriever:
    def __init__(self, connect: ConnectFn, top_k: int = 4) -> None:
        self._connect = connect
        self._top_k = top_k

    def retrieve(self, question: str) -> list[RetrievedChunk]:
        # websearch_to_tsquery ANDs bare terms, so one out-of-corpus word
        # ("differ", "compare") would zero out the whole match. Natural
        # questions need OR semantics: join words with websearch's OR operator
        # and let ts_rank_cd score chunks by how many terms they cover.
        # Tokenizing ourselves also strips characters websearch treats as
        # syntax; stop words are dropped by the 'english' config server-side.
        words = re.findall(r"[A-Za-z0-9_]+", question)
        if not words:
            with self._connect() as conn:
                return _fallback(conn, self._top_k)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, content, ts_rank_cd(ts, query)::float AS score
                FROM rag_chunks, websearch_to_tsquery('english', %(q)s) AS query
                WHERE ts @@ query
                ORDER BY score DESC, id
                LIMIT %(k)s
                """,
                {"q": " OR ".join(words), "k": self._top_k},
            ).fetchall()
            if not rows:
                return _fallback(conn, self._top_k)
        return [RetrievedChunk(*row) for row in rows]


def create_retriever(settings: Settings, connect: ConnectFn | None = None) -> Retriever:
    """Factory keyed on the RETRIEVER config toggle.

    `connect` defaults to per-call connections against the app database URL
    (standalone scripts); the FastAPI app passes its shared pool's
    `pool.connection` instead.
    """
    if connect is None:
        def connect() -> AbstractContextManager[psycopg.Connection]:
            return psycopg.connect(settings.admin_database_url)

    if settings.retriever == "vector":
        return VectorRetriever(
            connect=connect,
            embedder=create_embedding_provider(settings),
            top_k=settings.rag_top_k,
        )
    return LexicalRetriever(connect=connect, top_k=settings.rag_top_k)
