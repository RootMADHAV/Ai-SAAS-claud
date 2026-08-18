"""Integration tests for the public Scanning API
(``/api/v1/organizations/{organization_id}/scans``).

Exercises the real FastAPI app (``app.main.create_app()``) end-to-end
over HTTP via ``httpx.AsyncClient``/``ASGITransport`` -- no
``TestClient``, whose thread-based lifespan/event-loop handling would
bind an asyncpg connection pool to a different event loop than this
test's own (see tests/conftest.py's ``engine`` fixture docstring for why
that specific failure mode matters enough to design around here too).

Only the low-level providers with a real external dependency
(``get_session_factory``, ``get_active_scanner``) are overridden -- see
app/api/dependencies.py's module docstring. Everything built on top of
them (``get_org_session``'s organization-existence check, the repository
providers, the use-case providers) runs for real, so these tests
exercise the actual org-scoping and 404/409 wiring, not a bypassed
version of it.

Milestone 7 update: ``get_scan_dispatcher`` is now also overridden, with
a spy that records its calls instead of a fake that fabricates pipeline
completion -- this API layer no longer runs the pipeline itself (see
``app/api/v1/scans.py``'s module docstring), so what this test module
verifies about ``run_scan`` changed to match: that it validates
synchronously (still real 404/409 checks, unchanged in substance) and
dispatches with the right arguments, not that a scan completes. Whether
the *dispatched* work is correct is
``tests/integration/test_scan_worker_task.py``'s job now, mirroring the
existing split between this module and
``tests/integration/test_scan_pipeline_orchestrator.py`` for the use
case underneath. ``get_storage``/``get_analysis_service`` no longer
exist to override -- this API process does not construct either
anymore.

A pre-existing, unrelated correctness gap noted and fixed while
rewriting this file for the above, per the verification-honesty
convention: ``_create_organization()`` did not call ``set_org_context()``
before inserting its test organization. ``organizations``' own RLS
policy (``id = current_setting('app.current_org_id')::uuid``, see the
initial-schema Alembic migration) requires that GUC set to the row's own
id before an ``INSERT`` can satisfy its ``WITH CHECK`` clause -- no
database-level provisioning can satisfy this instead, since the check is
against the specific new row's id, not a fixed default value.
``tests/integration/test_scan_pipeline_orchestrator.py``'s own fixture
already calls ``set_org_context()`` first for exactly this reason; this
module now matches that already-correct, already-established pattern.
Not a Milestone 7 scope change in its own right -- bundled into this
milestone's already-required rewrite of this file rather than left
broken, and called out explicitly rather than silently folded in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import (
    ScanDispatcher,
    get_active_scanner,
    get_scan_dispatcher,
    get_session_factory,
)
from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.domain.shared.enums import ScanStatus
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.main import create_app
from tests.integration.support import make_organization, make_scan, set_org_context

pytestmark = pytest.mark.integration


class _FakeActiveScanner(ActiveScanner):
    """Only ``.name`` is ever read by this layer as of Milestone 7 (the
    scanner-mismatch check in ``run_scan``) -- ``.execute()`` is never
    called here, since this API process no longer runs the pipeline;
    still implemented (raising if it somehow were called) so this fake
    remains a faithful, complete ``ActiveScanner`` rather than a partial
    stub that would silently misrepresent what it substitutes for."""

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        raise AssertionError(
            "ActiveScanner.execute() should never be called from the API process "
            "as of Milestone 7 -- the pipeline runs inside the ingestion_worker "
            "Celery task, not here. If this fired, run_scan regressed to calling "
            "the pipeline directly again."
        )


class _DispatchSpy:
    """Records every dispatch call instead of actually enqueueing a
    Celery task or touching a real Redis broker -- this test module's
    job is to verify ``run_scan`` validates and dispatches with the
    right arguments, not to re-verify the dispatched task's own body
    (see ``tests/integration/test_scan_worker_task.py`` for that,
    mirroring the split ``tests/integration/
    test_scan_pipeline_orchestrator.py`` already has relative to this
    module for the use case underneath)."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def __call__(self, organization_id: UUID, scan_id: UUID) -> None:
        self.calls.append((organization_id, scan_id))


@pytest.fixture
def dispatch_spy() -> _DispatchSpy:
    return _DispatchSpy()


@pytest.fixture
def wired_app(engine: AsyncEngine, dispatch_spy: _DispatchSpy) -> FastAPI:
    """A fresh ``FastAPI`` app per test, wired to this test's own
    Postgres engine plus a fresh scanner fake and dispatch spy -- never
    the module-level ``app`` in ``app.main``, so no test can leak state
    into another (see ``create_app()``'s own docstring on why it is a
    factory)."""
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    app.dependency_overrides[get_active_scanner] = lambda: _FakeActiveScanner()
    app.dependency_overrides[get_scan_dispatcher] = lambda: dispatch_spy
    return app


async def _make_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_organization(db_session: AsyncSession) -> UUID:
    organization = make_organization()
    await set_org_context(db_session, organization.id)
    await SqlAlchemyOrganizationRepository(db_session).add(organization)
    await db_session.commit()
    return organization.id


async def test_create_scan_returns_201_with_all_eight_steps_pending(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com", "scanner_name": "nuclei"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["organization_id"] == str(org_id)
    assert body["target"] == "example.com"
    assert body["status"] == "queued"
    assert len(body["workflow_steps"]) == 8
    assert all(step["status"] == "pending" for step in body["workflow_steps"])
    assert response.headers["location"] == f"/api/v1/organizations/{org_id}/scans/{body['id']}"


async def test_create_scan_defaults_scanner_name_to_nuclei(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )

    assert response.status_code == 201
    assert response.json()["scanner_name"] == "nuclei"


async def test_create_scan_rejects_an_unknown_scanner_name_with_422(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com", "scanner_name": "nmap"},
        )

    assert response.status_code == 422


async def test_create_scan_for_a_nonexistent_organization_returns_404(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{uuid4()}/scans",
            json={"target": "example.com"},
        )

    assert response.status_code == 404


async def test_run_scan_returns_202_and_dispatches_the_task(
    wired_app: FastAPI, db_session: AsyncSession, dispatch_spy: _DispatchSpy
) -> None:
    """The Milestone 7 API-contract change: run no longer executes the
    pipeline or returns a finished scan -- it validates synchronously and
    dispatches, returning 202 with the scan's pre-execution state."""
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        create_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )
        scan_id = create_response.json()["id"]

        run_response = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan_id}/run")

    assert run_response.status_code == 202
    body = run_response.json()
    # Still QUEUED: no worker actually ran the pipeline in this test --
    # RunScanWorkflowUseCase's own status transitions are
    # tests/integration/test_scan_worker_task.py's and
    # tests/integration/test_scan_pipeline_orchestrator.py's job to
    # verify, not this module's.
    assert body["status"] == ScanStatus.QUEUED.value
    assert dispatch_spy.calls == [(org_id, UUID(scan_id))]


async def test_run_scan_for_a_nonexistent_scan_returns_404(
    wired_app: FastAPI, db_session: AsyncSession, dispatch_spy: _DispatchSpy
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{org_id}/scans/{uuid4()}/run")

    assert response.status_code == 404
    assert dispatch_spy.calls == []


async def test_run_scan_for_a_nonexistent_organization_returns_404(
    wired_app: FastAPI, dispatch_spy: _DispatchSpy
) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{uuid4()}/scans/{uuid4()}/run")

    assert response.status_code == 404
    assert dispatch_spy.calls == []


async def test_run_scan_with_a_mismatched_scanner_name_returns_409(
    wired_app: FastAPI, db_session: AsyncSession, dispatch_spy: _DispatchSpy
) -> None:
    """A Scan row created some other way than this API's own create
    endpoint (whose request schema only ever accepts "nuclei") with a
    scanner_name this deployment has no adapter for -- exercises the
    ScannerMismatchError -> 409 global exception handler wiring in
    app/main.py, now raised directly from the route (see
    app/api/v1/scans.py's module docstring) rather than from inside a
    use case call -- same observable HTTP behavior either way."""
    org_id = await _create_organization(db_session)
    await set_org_context(db_session, org_id)
    scan = make_scan(org_id, scanner_name="not-a-real-scanner")
    await SqlAlchemyScanRepository(db_session).add(scan)
    await db_session.commit()

    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan.id}/run")

    assert response.status_code == 409
    assert dispatch_spy.calls == []


async def test_run_scan_does_not_redispatch_an_already_running_scan(
    wired_app: FastAPI, db_session: AsyncSession, dispatch_spy: _DispatchSpy
) -> None:
    """New as of Milestone 7: since dispatch returns almost instantly
    (unlike the old synchronous call, which blocked behind the pipeline
    itself), a client can trivially call run twice in rapid succession.
    A scan already RUNNING is not re-dispatched -- still 202 (the
    caller's request, "make sure this is running," is genuinely
    satisfied), just without enqueueing a duplicate concurrent worker
    run of the same scan."""
    org_id = await _create_organization(db_session)
    await set_org_context(db_session, org_id)
    scan = make_scan(org_id, status=ScanStatus.RUNNING)
    await SqlAlchemyScanRepository(db_session).add(scan)
    await db_session.commit()

    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan.id}/run")

    assert response.status_code == 202
    assert response.json()["status"] == ScanStatus.RUNNING.value
    assert dispatch_spy.calls == []


async def test_get_scan_returns_its_current_detail(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        create_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )
        scan_id = create_response.json()["id"]

        get_response = await client.get(f"/api/v1/organizations/{org_id}/scans/{scan_id}")

    assert get_response.status_code == 200
    assert get_response.json()["id"] == scan_id
    assert len(get_response.json()["workflow_steps"]) == 8


async def test_get_scan_for_a_nonexistent_scan_returns_404(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.get(f"/api/v1/organizations/{org_id}/scans/{uuid4()}")

    assert response.status_code == 404


async def test_get_scan_for_a_nonexistent_organization_returns_404(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.get(f"/api/v1/organizations/{uuid4()}/scans/{uuid4()}")

    assert response.status_code == 404


async def test_scan_lifecycle_across_three_requests_reflects_async_dispatch(
    wired_app: FastAPI, db_session: AsyncSession, dispatch_spy: _DispatchSpy
) -> None:
    """The vertical slice this milestone's API actually exposes: create,
    run (validated and dispatched, not executed), then read back the
    same scan across three separate HTTP requests, the way a real client
    would use it. Completion is not observed here -- there is no worker
    consuming the dispatched task in this test process -- that is
    exactly what moved off this module's plate to
    tests/integration/test_scan_worker_task.py."""
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        created = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )
        scan_id = created.json()["id"]
        assert created.json()["status"] == ScanStatus.QUEUED.value

        ran = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan_id}/run")
        assert ran.status_code == 202
        assert dispatch_spy.calls == [(org_id, UUID(scan_id))]

        fetched = await client.get(f"/api/v1/organizations/{org_id}/scans/{scan_id}")
        assert fetched.json()["status"] == ScanStatus.QUEUED.value
        assert fetched.json()["completed_at"] is None
