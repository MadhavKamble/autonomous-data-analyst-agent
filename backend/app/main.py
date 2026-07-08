"""FastAPI application factory.

Startup wires the composition root: one shared app-role connection pool
(sessions, RAG retrieval, health) and one Pipeline instance (stateless per
request — all conversation state lives in Postgres, so a Render spin-down
between requests loses nothing). The agent_ro executor inside the pipeline
manages its own per-execution connections; see db/engine.py for why.

Run locally:  uvicorn app.main:app --reload --port 8000  (from backend/)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db.engine import create_app_pool
from app.db.sessions import SessionStore
from app.orchestrator.pipeline import create_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = create_app_pool(settings)
    pool.open()
    app.state.pool = pool
    app.state.session_store = SessionStore(pool)
    # Retrieval shares the app pool; the read-only executor does not (by design).
    app.state.pipeline = create_pipeline(settings, connect=pool.connection)
    try:
        yield
    finally:
        pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Autonomous Data-Analyst Agent",
        description="Multi-agent NL→SQL system over a ride-sharing data snapshot, "
        "with a full reasoning trace per answer.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
