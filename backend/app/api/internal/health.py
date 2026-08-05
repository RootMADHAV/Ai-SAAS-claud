"""Internal health-check endpoints -- liveness and readiness.

Mounted under ``/internal`` by ``app/main.py``, separate from the public
``/api/v1`` surface -- the "Public/internal API split" named in
PROJECT_STATE.md sections 1 and 3. Restricting ``/internal`` at the
network level is explicitly named as Phase 10 work in that same
section; not attempted here.

PROJECT_STATE.md section 3's original phrasing names this split as
"``/internal`` health/metrics/admin." Only health is implemented in this
milestone -- deliberately, not by oversight:
  - ``/internal/metrics`` would need the ``observability/`` package
    (still an empty scaffold -- see PROJECT_STATE.md section 4's folder
    structure), which no milestone has built yet. Emitting a metrics
    endpoint with nothing real behind it would be exactly the kind of
    stub this session's rules prohibit.
  - ``/internal/admin`` would need RBAC (Phase 6 on the roadmap in
    docs/implementation_progress.md, far past this milestone) to mean
    anything -- an "admin" endpoint with no authorization model behind
    it is not a real feature.

Health needs neither: liveness checks nothing external, and readiness
checks only the one hard dependency every ``/api/v1`` route actually
has today (the database).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_session_factory

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """The process is up and able to handle a request at all. Checks
    nothing external -- matching the conventional meaning of a liveness
    probe (an orchestrator like Kubernetes uses this to decide whether
    to restart the container, not whether it can currently serve real
    traffic; that is readiness, below)."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> JSONResponse:
    """Confirms the database is reachable, via a plain ``SELECT 1`` that
    touches no RLS-protected table -- so it needs no
    ``app.current_org_id`` context, unlike every other query in this
    codebase (see ``session_scoped_to_org``'s docstring for why that
    context is otherwise required).

    Deliberately does not also probe MinIO or Redis: this milestone's
    one hard, already-wired dependency is the database (every
    ``/api/v1`` route needs it to even resolve an organization); a
    fuller readiness check spanning every dependency
    ``RunScanWorkflowUseCase`` transitively has is future work, not
    silently assumed equivalent to this narrower check.

    Catches ``Exception`` broadly and reports 503 rather than letting an
    unhandled error become a generic 500 -- the same broad-catch
    judgment call already made, and explained, in
    ``tests/conftest.py``'s ``_postgres_is_reachable`` for the same
    reason: at a boundary whose entire job is "is the thing reachable,"
    any failure mode means the same thing (no), and 503 is the correct,
    conventional signal for a readiness probe to return.
    """
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ok"})
