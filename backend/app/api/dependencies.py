"""FastAPI dependency providers -- the composition root's per-request
wiring for the Scanning API (``app/api/v1/scans.py``).

Everything here builds on already-existing, already-verified layers
(Milestone 2's repositories, Milestone 3's ``ScannerPort`` adapter,
Milestone 4's use cases). Nothing in this module invents a new port, a
new use case, or a new persistence concept -- it only wires existing
pieces together per HTTP request, which is exactly Milestone 5's scope
(PROJECT_STATE.md section 15: "route handlers that call
TriggerScanUseCase/RunScanWorkflowUseCase ... and the Milestone 2
repositories").

One request = one RLS-scoped transaction: ``get_org_session`` opens
exactly one ``session_scoped_to_org`` (app/infrastructure/db/session.py,
Milestone 2) per request, and every repository dependency below depends
on that same function -- FastAPI caches a dependency's result per
request by default, so several separate ``Depends(get_org_session)``
calls within one request resolve to the same session, not several
transactions.

Milestone 7 update -- least-privilege, not unrelated cleanup: this API
process no longer runs ``RunScanWorkflowUseCase`` itself (that now
happens inside the ``ingestion_worker`` Celery task,
``app/workers/tasks.py`` -- see ``app/api/v1/scans.py``'s ``run_scan``
route, which validates a scan's existence/scanner-name synchronously and
then dispatches a task instead of calling the use case directly). Two
consequences, both applied here:
  - ``AppState`` no longer holds ``storage``/``analysis_service`` --
    this process never touches ``MinioStoragePort`` or
    ``AnalysisService``/``AnthropicProvider`` anymore, so it has no
    reason to hold their credentials at all (``app/main.py``'s
    ``_lifespan`` correspondingly stopped constructing them; see
    ``app/config.py``'s ``check_role_boundaries`` for where the
    ``ANTHROPIC_API_KEY`` requirement moved to instead --
    ``worker_role=ingestion_worker``, not ``worker_role=api``).
    ``active_scanner`` (``NucleiAdapter``) is unaffected and stays --
    it is credential-free (see its own constructor) and
    ``run_scan`` still needs its ``.name`` for the cheap mismatch check.
  - ``get_storage``/``get_analysis_service``/
    ``get_run_scan_workflow_use_case`` are removed rather than left as
    unreachable dead code -- nothing calls them once ``run_scan`` no
    longer constructs ``RunScanWorkflowUseCase`` itself. In their place,
    ``get_scan_dispatcher`` is the new seam ``run_scan`` depends on: a
    thin callable wrapping ``run_scan_workflow_task.delay(...)``, the
    same override-in-tests pattern already established for
    ``get_active_scanner``/(the now-removed) ``get_storage`` --
    ``tests/integration/test_api_scans.py`` overrides it with a spy
    instead of either running a real Celery worker or hitting a real
    Redis broker in an HTTP-layer test whose job is to verify dispatch
    happened with the right arguments, not to re-verify the task body
    (that is ``tests/integration/test_scan_worker_task.py``'s job,
    mirroring the existing split between this module's own tests and
    ``tests/integration/test_scan_pipeline_orchestrator.py``'s).
  - ``get_asset_repository``/``get_finding_repository`` are removed for
    the same reason: their only caller was
    ``get_run_scan_workflow_use_case``, which no longer exists. Findings/
    Assets have no HTTP surface yet (PROJECT_STATE.md's Milestone 5
    design-decision note, unchanged) so nothing else in this module
    needs them; a future milestone that adds one re-adds the provider it
    actually needs, the same way ``get_analysis_service`` itself was
    only ever added in the milestone that first needed it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.infrastructure.db.session import session_scoped_to_org
from app.workers.tasks import run_scan_workflow_task

#: The signature ``run_scan`` (app/api/v1/scans.py) dispatches through --
#: "given an organization and a scan already confirmed to exist, enqueue
#: its pipeline run." Plain ``UUID`` in, nothing out: the caller does not
#: wait on or inspect the dispatched task's result (see
#: ``app/workers/tasks.py``'s module docstring on why no Celery result is
#: ever read back).
ScanDispatcher = Callable[[UUID, UUID], None]


@dataclass(slots=True)
class AppState:
    """Everything the composition root (``app/main.py``'s lifespan)
    builds once at process startup and every request's dependency chain
    reads from below -- a session factory bound to one long-lived
    engine, and the one Milestone 3 adapter (``ActiveScanner``) this
    process still needs directly (for the cheap scanner-name check in
    ``run_scan`` -- see module docstring on why ``storage``/
    ``analysis_service`` no longer live here as of Milestone 7). Grouped
    into one dataclass, stored as a single ``request.app.state``
    attribute, so there is exactly one place (``_state`` below) that
    casts out of Starlette's untyped ``State`` container, instead of one
    cast per field.
    """

    session_factory: async_sessionmaker[AsyncSession]
    active_scanner: ActiveScanner


def _state(request: Request) -> AppState:
    """``request.app.state`` is an untyped ``starlette.datastructures.State``
    -- this is the one place that narrows it back to ``AppState``, rather
    than scattering ``# type: ignore`` at every call site that reads a
    field off it. Every other function in this module goes through this
    one, never ``request.app.state`` directly."""
    return cast(AppState, request.app.state.wired)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The one seam tests override wholesale (see
    ``tests/integration/test_api_scans.py``) to point at a test
    database's engine instead of process-startup wiring -- everything
    built on top of this function (``get_org_session`` and everything
    that depends on it) then runs unmodified against that test engine,
    so a test still exercises the real org-scoping and 404 logic, not a
    bypassed version of it."""
    return _state(request).session_factory


def get_active_scanner(request: Request) -> ActiveScanner:
    return _state(request).active_scanner


def get_scan_dispatcher() -> ScanDispatcher:
    """Returns a callable that enqueues ``run_scan_workflow_task``
    (``app/workers/tasks.py``) for the given scan. Deliberately not a
    method on ``AppState`` and not itself reading from
    ``request.app.state`` -- ``run_scan_workflow_task`` is a module-level
    Celery task object, not a per-process resource this API needs to
    build or hold a reference to (unlike ``active_scanner``, which really
    is constructed once at startup); wrapping it in one small function
    here exists purely as the override seam tests use, the same reason
    ``get_active_scanner``/``get_session_factory`` are functions rather
    than call sites reaching for ``AppState``/Celery directly.
    """

    def _dispatch(organization_id: UUID, scan_id: UUID) -> None:
        run_scan_workflow_task.delay(str(organization_id), str(scan_id))

    return _dispatch


async def get_org_session(
    organization_id: UUID,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """One request = one RLS-scoped transaction, committed on a clean
    return and rolled back on any exception -- exactly what
    ``session_scoped_to_org`` already guarantees for any caller
    (Milestone 2); this dependency does not reimplement that, it opens
    one.

    The organization-existence check is what turns what would otherwise
    be a foreign-key-violation ``IntegrityError`` on the first write (an
    ugly, uninformative 500) into a clean 404 raised before anything is
    written -- using the same ``OrganizationRepositoryPort.get_by_id``
    every other caller in this codebase already uses, not a new lookup
    path invented for the API. No authentication happens here:
    Milestone 5 does not verify *who* is calling, only that the
    organization named in the URL path exists -- see this milestone's
    design-decision note in PROJECT_STATE.md on why building JWT
    verification now, ahead of Identity & Access's own use cases
    (still an empty ``application/identity`` scaffold), is out of scope.
    """
    async with session_scoped_to_org(session_factory, organization_id) as session:
        organization = await SqlAlchemyOrganizationRepository(session).get_by_id(organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail=f"organization {organization_id} not found")
        yield session


def get_scan_repository(
    session: AsyncSession = Depends(get_org_session),
) -> ScanRepositoryPort:
    return SqlAlchemyScanRepository(session)


def get_trigger_scan_use_case(
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> TriggerScanUseCase:
    return TriggerScanUseCase(scan_repository)
