"""HTTP endpoints. Design notes that answer "why is it built this way":

- Endpoints are sync `def`, so FastAPI runs them on its threadpool. The
  pipeline is deliberately synchronous (one request = one full agent run; no
  background workers or job polling on free hosting), and psycopg here is the
  sync driver — blocking a worker thread is correct; blocking the event loop
  would not be.
- /ask persists the user message BEFORE running the pipeline and the
  assistant message (with the full AskResult as JSONB) BEFORE returning.
  Process memory holds nothing: a Render spin-down between requests loses
  zero conversation state.
- /health checks actual database connectivity (SELECT 1 through the app
  pool), not merely process liveness — UptimeRobot's ping must validate the
  stack the demo depends on, and its 5-minute cadence doubles as the
  keep-warm signal.
- /ask is rate-limited per client IP (app/api/rate_limit.py) — there is no
  user auth in this demo, so an IP-keyed budget is the only thing stopping
  one visitor from spending the whole shared Groq daily quota.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.rate_limit import enforce_rate_limit
from app.api.schemas import (
    AskRequest,
    AskResponse,
    HealthResponse,
    MessageOut,
    SessionSummary,
)
from app.db.sessions import SessionStore
from app.orchestrator.pipeline import Pipeline

logger = logging.getLogger(__name__)
router = APIRouter()


def _store(request: Request) -> SessionStore:
    return request.app.state.session_store


def _pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


# -- health -------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse | JSONResponse:
    try:
        with request.app.state.pool.connection(timeout=5) as conn:
            conn.execute("SELECT 1")
    except Exception:
        logger.exception("health check: database unreachable")
        return JSONResponse(
            status_code=503,
            content=HealthResponse(status="degraded", database="unreachable").model_dump(),
        )
    return HealthResponse(status="ok", database="ok")


# -- ask ------------------------------------------------------------------------

@router.post("/ask", response_model=AskResponse, dependencies=[Depends(enforce_rate_limit)])
def ask(request: Request, body: AskRequest) -> AskResponse:
    store = _store(request)

    if body.session_id is not None:
        session = store.get_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        session_id = session.id
    else:
        session_id = store.create_session(title=body.question).id

    store.add_message(session_id, "user", body.question)

    # Synchronous by design; the pipeline's ask() never raises (bounded
    # retries, hard LLM budget, clean failure messages) so every outcome —
    # including failures — is persisted and returned as a normal response.
    result = _pipeline(request).ask(body.question)

    content = result.answer if result.status == "ok" else (result.failure_reason or "")
    message = store.add_message(
        session_id, "assistant", content or "", trace=result.model_dump(mode="json")
    )
    return AskResponse(session_id=session_id, message_id=message.id, result=result)


# -- sessions -------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(request: Request) -> list[SessionSummary]:
    return [SessionSummary(**vars(s)) for s in _store(request).list_sessions()]


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(request: Request, session_id: UUID) -> list[MessageOut]:
    store = _store(request)
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            trace=m.trace,  # pydantic revalidates the stored AskResult JSON
            created_at=m.created_at,
        )
        for m in store.list_messages(session_id)
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(request: Request, session_id: UUID) -> None:
    if not _store(request).delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
