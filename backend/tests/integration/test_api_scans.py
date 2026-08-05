"""Integration tests for the public Scanning API
(``/api/v1/organizations/{organization_id}/scans``).

Exercises the real FastAPI app (``app.main.create_app()``) end-to-end
over HTTP via ``httpx.AsyncClient``/``ASGITransport`` -- no
``TestClient``, whose thread-based lifespan/event-loop handling would
bind an asyncpg connection pool to a different event loop than this
test's own (see tests/conftest.py's ``engine`` fixture docstring for why
that specific failure mode matters enough to design around here too).

Only the three low-level providers with a real external dependency
(``get_session_factory``, ``get_active_scanner``, ``get_storage``) are
overridden -- see app/api/dependencies.py's module docstring. Everything
built on top of them (``get_org_session``'s organization-existence
check, the repository providers, the use-case providers) runs for real,
so these tests exercise the actual org-scoping and 404/409 wiring, not a
bypassed version of it -- the same "repositories are real, only the
scanner/storage adapters are faked" split already established in
tests/integration/test_scan_pipeline_orchestrator.py for the use cases
these routes call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import get_active_scanner, get_session_factory, get_storage
from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.application.interfaces.storage_port import StorageObjectNotFoundError, StoragePort
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.main import create_app
from tests.integration.support import make_organization, make_scan, set_org_context

pytestmark = pytest.mark.integration

_NUCLEI_MATCH_LINE = (
    b'{"template-id":"t1","info":{"name":"Test Finding","severity":"high"},'
    b'"host":"example.com","matched-at":"example.com"}'
)


class _FakeActiveScanner(ActiveScanner):
    """A scanner that always reports one match against ``example.com``,
    without touching a subprocess or the network -- these tests are
    about the API layer's wiring, not nuclei itself (which
    tests/unit/test_nuclei_adapter.py already covers)."""

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        now = datetime.now(UTC)
        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=_NUCLEI_MATCH_LINE,
            started_at=now,
            completed_at=now,
        )


class _FakeStorage(StoragePort):
    """An in-memory StoragePort -- no real MinIO server is reachable in
    this environment (the same constraint already documented for
    Milestone 3's own test suite)."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self._objects[key] = data

    async def get_object(self, key: str) -> bytes:
        if key not in self._objects:
            raise StorageObjectNotFoundError(key)
        return self._objects[key]

    async def delete_object(self, key: str) -> None:
        if key not in self._objects:
            raise StorageObjectNotFoundError(key)
        del self._objects[key]

    async def object_exists(self, key: str) -> bool:
        return key in self._objects


@pytest.fixture
def wired_app(engine: AsyncEngine) -> FastAPI:
    """A fresh ``FastAPI`` app per test, wired to this test's own
    Postgres engine plus fresh scanner/storage fakes -- never the module-
    level ``app`` in ``app.main``, so no test can leak state into
    another (see ``create_app()``'s own docstring on why it is a
    factory)."""
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    app.dependency_overrides[get_active_scanner] = lambda: _FakeActiveScanner()
    app.dependency_overrides[get_storage] = lambda: _FakeStorage()
    return app


async def _make_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_organization(db_session: AsyncSession) -> UUID:
    organization = make_organization()
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


async def test_run_scan_completes_and_marks_ai_analyze_skipped(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        create_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )
        scan_id = create_response.json()["id"]

        run_response = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan_id}/run")

    assert run_response.status_code == 200
    body = run_response.json()
    assert body["status"] == ScanStatus.COMPLETED.value
    steps_by_name = {step["step_name"]: step for step in body["workflow_steps"]}
    assert steps_by_name[WorkflowStepName.AI_ANALYZE.value]["status"] == (
        WorkflowStepStatus.SKIPPED.value
    )
    assert steps_by_name[WorkflowStepName.PERSIST.value]["status"] == (
        WorkflowStepStatus.COMPLETED.value
    )


async def test_run_scan_for_a_nonexistent_scan_returns_404(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{org_id}/scans/{uuid4()}/run")

    assert response.status_code == 404


async def test_run_scan_for_a_nonexistent_organization_returns_404(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{uuid4()}/scans/{uuid4()}/run")

    assert response.status_code == 404


async def test_run_scan_with_a_mismatched_scanner_name_returns_409(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    """A Scan row created some other way than this API's own create
    endpoint (whose request schema only ever accepts "nuclei") with a
    scanner_name this process has no adapter for -- exercises the
    ScannerMismatchError -> 409 global exception handler wiring in
    app/main.py."""
    org_id = await _create_organization(db_session)
    await set_org_context(db_session, org_id)
    scan = make_scan(org_id, scanner_name="not-a-real-scanner")
    await SqlAlchemyScanRepository(db_session).add(scan)
    await db_session.commit()

    async with await _make_client(wired_app) as client:
        response = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan.id}/run")

    assert response.status_code == 409


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


async def test_scan_lifecycle_end_to_end_across_three_requests(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    """The vertical slice this milestone's API actually exposes: create,
    run, then read back the same scan and see the completed state --
    across three separate HTTP requests, the way a real client would
    use it, rather than asserting against internal state directly."""
    org_id = await _create_organization(db_session)

    async with await _make_client(wired_app) as client:
        created = await client.post(
            f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
        )
        scan_id = created.json()["id"]
        assert created.json()["status"] == ScanStatus.QUEUED.value

        ran = await client.post(f"/api/v1/organizations/{org_id}/scans/{scan_id}/run")
        assert ran.json()["status"] == ScanStatus.COMPLETED.value

        fetched = await client.get(f"/api/v1/organizations/{org_id}/scans/{scan_id}")
        assert fetched.json()["status"] == ScanStatus.COMPLETED.value
        assert fetched.json()["completed_at"] is not None
