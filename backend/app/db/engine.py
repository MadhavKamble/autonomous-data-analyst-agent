"""Connection management: one pooled role, one per-call role.

Two database identities exist on purpose (see migration 002):

  app role  (ADMIN_DATABASE_URL)  — sessions, RAG corpus, health checks.
      Served by a single shared ConnectionPool created at app startup:
      these queries are small and frequent, and on Neon a fresh TLS
      connection costs real latency per request.

  agent_ro  (AGENT_DATABASE_URL)  — generated SQL only, executed by
      SQLExecutor over per-call connections, NOT this pool. Deliberate:
      each execution wants a fresh session with its own read-only +
      statement_timeout options, at most a few connections per question,
      and no chance of a hostile query occupying a shared app connection.
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool

from app.config import Settings


def create_app_pool(settings: Settings) -> ConnectionPool:
    return ConnectionPool(
        settings.admin_database_url,
        min_size=1,
        # Modest cap: Neon's free tier allows few connections, and Render free
        # runs a single process — 5 concurrent app-state queries is plenty.
        max_size=5,
        # open=False: the pool connects on open() in the app lifespan, not at
        # construction — keeps import side-effect-free for tests/scripts.
        open=False,
        name="app-pool",
    )
