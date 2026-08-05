"""FastAPI dependency providers -- the composition root's per-request
wiring for the Scanning API (``app/api/v1/scans.py``).

Everything here builds on already-existing, already-verified layers
(Milestone 2's repositories, Milestone 3's ``ScannerPort``/``StoragePort``
adapters, Milestone 4's use cases). Nothing in this module invents a new
port, a new use case, or a new persistence concept -- it only wires
existing pieces together per HTTP request, which is exactly Milestone 5's
scope (PROJECT_STATE.md section 15: "route handlers that call
TriggerScanUseCase/RunScanWorkflowUseCase ... and the Milestone 2
repositories").

One request = one RLS-scoped transaction: ``get_org_session`` opens
exactly one ``session_scoped_to_org`` (app/infrastructure/db/session.py,
Milestone 2) per request, and every repository/use-case dependency below
depends on that same function -- FastAPI caches a dependency's result
per request by default, so three separate ``Depends(get_org_session)``
calls within one request resolve to the same session, not three
transactions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.interfaces.assets_repository import AssetRepositoryPort
from app.application.interfaces.findings_repository import FindingRepositoryPort
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.interfaces.storage_port import StoragePort
from app.application.scanning.run_scan_workflow import RunScanWorkflowUseCase
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.infrastructure.db.session import session_scoped_to_org


@dataclass(slots=True)
class AppState:
    """Everything the composition root (``app/main.py``'s lifespan) builds
    once at process startup and every request's dependency chain reads
    from below -- a session factory bound to one long-lived engine, and
    the two Milestone 3 adapters (``ActiveScanner``, ``StoragePort``)
    ``RunScanWorkflowUseCase`` needs. Grouped into one dataclass, stored
    as a single ``request.app.state`` attribute, so there is exactly one
    place (``_state`` below) that casts out of Starlette's untyped
    ``State`` container, instead of one cast per field."""

    session_factory: async_sessionmaker[AsyncSession]
    active_scanner: ActiveScanner
    storage: StoragePort


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


def get_storage(request: Request) -> StoragePort:
    return _state(request).storage


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


def get_asset_repository(
    session: AsyncSession = Depends(get_org_session),
) -> AssetRepositoryPort:
    return SqlAlchemyAssetRepository(session)


def get_finding_repository(
    session: AsyncSession = Depends(get_org_session),
) -> FindingRepositoryPort:
    return SqlAlchemyFindingRepository(session)


def get_trigger_scan_use_case(
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> TriggerScanUseCase:
    return TriggerScanUseCase(scan_repository)


def get_run_scan_workflow_use_case(
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
    asset_repository: AssetRepositoryPort = Depends(get_asset_repository),
    finding_repository: FindingRepositoryPort = Depends(get_finding_repository),
    active_scanner: ActiveScanner = Depends(get_active_scanner),
    storage: StoragePort = Depends(get_storage),
) -> RunScanWorkflowUseCase:
    return RunScanWorkflowUseCase(
        scan_repository=scan_repository,
        asset_repository=asset_repository,
        finding_repository=finding_repository,
        active_scanner=active_scanner,
        storage=storage,
    )
