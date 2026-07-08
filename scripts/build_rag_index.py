#!/usr/bin/env python3
"""Build (or rebuild) the RAG corpus in rag_chunks.

Three chunk kinds, two sources:

  schema_doc     GENERATED from the live database catalog — the COMMENT ON
                 metadata in migration 001 plus actual column types. Schema
                 docs therefore cannot drift from the real database: change a
                 comment in a migration, apply it, rebuild the index.
  query_example  backend/app/rag/corpus/query_examples.yaml (hand-curated).
                 Every example's SQL is EXPLAIN-validated against the live
                 schema before indexing — a broken example is a build error,
                 not a runtime hallucination aid.
  guidance       backend/app/rag/corpus/guidance.yaml (hand-curated rules).

Embeddings are computed via the configured Ollama provider when reachable.
If Ollama is down (or --skip-embeddings is passed), chunks are still written
for lexical retrieval; existing embeddings are preserved for chunks whose
content did not change, so a lexical-only rebuild never destroys vector state.

Idempotent: upserts by chunk id and deletes chunks that no longer exist.

Usage:
    python scripts/build_rag_index.py [--skip-embeddings] [--database-url ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))  # standalone script, not an installed package

from app.config import get_settings  # noqa: E402
from app.rag.embeddings import create_embedding_provider  # noqa: E402

CORPUS_DIR = REPO_ROOT / "backend" / "app" / "rag" / "corpus"
DATA_TABLES = ["zone_demand", "rides_historical_nyc", "zone_demand_historical_nyc"]

SCHEMA_QUERY = """
    SELECT c.column_name,
           c.data_type,
           col_description(t.oid, c.ordinal_position) AS column_comment
    FROM information_schema.columns c
    JOIN pg_class t
      ON t.relname = c.table_name
     AND t.relnamespace = 'public'::regnamespace
    WHERE c.table_schema = 'public' AND c.table_name = %s
    ORDER BY c.ordinal_position
"""


def build_schema_chunks(conn: psycopg.Connection) -> list[dict]:
    """One chunk per data table, composed from live catalog metadata."""
    chunks = []
    for table in DATA_TABLES:
        table_comment = conn.execute(
            "SELECT obj_description(%s::regclass, 'pg_class')", (table,)
        ).fetchone()[0]
        columns = conn.execute(SCHEMA_QUERY, (table,)).fetchall()
        if not columns:
            sys.exit(f"table '{table}' not found — run migrations first")

        lines = [f"TABLE {table}", table_comment or "", "Columns:"]
        for name, data_type, comment in columns:
            lines.append(f"- {name} ({data_type}): {comment or 'no description'}")
        chunks.append(
            {"id": f"schema:{table}", "kind": "schema_doc", "content": "\n".join(lines)}
        )
    return chunks


def load_corpus_chunks() -> list[dict]:
    chunks = []
    for entry in yaml.safe_load((CORPUS_DIR / "query_examples.yaml").read_text()):
        content = f"Question: {entry['question']}\nSQL:\n{entry['sql'].strip()}"
        if entry.get("notes"):
            content += f"\nNotes: {entry['notes'].strip()}"
        chunks.append(
            {
                "id": f"example:{entry['id']}",
                "kind": "query_example",
                "content": content,
                "sql": entry["sql"],  # kept aside for EXPLAIN validation
            }
        )
    for entry in yaml.safe_load((CORPUS_DIR / "guidance.yaml").read_text()):
        chunks.append(
            {
                "id": f"guidance:{entry['id']}",
                "kind": "guidance",
                "content": f"{entry['title']}\n{entry['content'].strip()}",
            }
        )
    return chunks


def validate_examples(conn: psycopg.Connection, chunks: list[dict]) -> None:
    """EXPLAIN every query example: plans without executing, so it proves the
    SQL parses and references only real tables/columns at zero cost."""
    for chunk in chunks:
        if "sql" not in chunk:
            continue
        try:
            conn.execute(f"EXPLAIN {chunk['sql']}")
        except psycopg.Error as exc:
            sys.exit(f"query example '{chunk['id']}' failed EXPLAIN validation:\n{exc}")
        conn.rollback()  # discard any plan-time state between examples


UPSERT_SQL = """
    INSERT INTO rag_chunks (id, kind, content, embedding, updated_at)
    VALUES (%(id)s, %(kind)s, %(content)s, %(embedding)s::vector, now())
    ON CONFLICT (id) DO UPDATE SET
        kind = excluded.kind,
        content = excluded.content,
        -- Preserve an existing embedding when this build supplies none AND
        -- the text is unchanged (lexical-only rebuild); otherwise take the
        -- new value — a NULL for changed content correctly invalidates the
        -- stale vector rather than serving it.
        embedding = CASE
            WHEN excluded.embedding IS NULL AND rag_chunks.content = excluded.content
                THEN rag_chunks.embedding
            ELSE excluded.embedding
        END,
        updated_at = now()
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", help="overrides ADMIN_DATABASE_URL")
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="index for lexical retrieval only (no Ollama required)",
    )
    args = parser.parse_args()

    settings = get_settings()
    db_url = args.database_url or settings.admin_database_url
    if not db_url:
        sys.exit("No database URL. Pass --database-url or set ADMIN_DATABASE_URL.")

    with psycopg.connect(db_url) as conn:
        chunks = build_schema_chunks(conn) + load_corpus_chunks()
        validate_examples(conn, chunks)

        embeddings: list[list[float] | None] = [None] * len(chunks)
        if args.skip_embeddings:
            print("embeddings: skipped (--skip-embeddings)")
        else:
            provider = create_embedding_provider(settings)
            if provider.is_available():
                embeddings = provider.embed([c["content"] for c in chunks])
                print(f"embeddings: {len(embeddings)} x {provider.dimensions} dims via Ollama")
            else:
                print(
                    f"embeddings: SKIPPED — Ollama not reachable at "
                    f"{settings.ollama_base_url}. Chunks indexed for lexical "
                    "retrieval; unchanged chunks keep any existing embeddings.",
                    file=sys.stderr,
                )

        with conn.transaction():
            for chunk, embedding in zip(chunks, embeddings):
                conn.execute(
                    UPSERT_SQL,
                    {
                        "id": chunk["id"],
                        "kind": chunk["kind"],
                        "content": chunk["content"],
                        "embedding": json.dumps(embedding) if embedding else None,
                    },
                )
            deleted = conn.execute(
                "DELETE FROM rag_chunks WHERE id != ALL(%s)", ([c["id"] for c in chunks],)
            ).rowcount

    by_kind: dict[str, int] = {}
    for chunk in chunks:
        by_kind[chunk["kind"]] = by_kind.get(chunk["kind"], 0) + 1
    summary = ", ".join(f"{count} {kind}" for kind, count in sorted(by_kind.items()))
    print(f"indexed {len(chunks)} chunks ({summary}); removed {deleted} stale")


if __name__ == "__main__":
    main()
