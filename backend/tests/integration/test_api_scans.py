"""Integration tests for the public Scanning API
(``/api/v1/organizations/{organization_id}/scans``).

Exercises the real FastAPI app (``app.main.create_app()``) end-to-end
over HTTP via ``httpx.AsyncClient``/``ASGITransport`` -- no
``TestClient``, whose thread-based lifespan/event-loop handling would
bind an asyncpg connection pool to a different event loop than this
test's own (see tests/conftest.py's ``engine`` fixture docstring for why
that specific failure mode matters enough to design around here too).

Only the low-level providers with a real external dependency
(``get_session_factory``, ``get_active_scanner``, and
``get_auth_config``) are overridden -- see app/api/dependencies.py's
module docstring. Everything built on top of them (``get_org_session``'s
organization-existence check, ``require_organization_member``'s
membership check, the repository providers, the use-case providers)
runs for real, so these tests exercise the actual org-scoping, auth, and
404/409/401/403 wiring, not a bypassed version of it.

``get_scan_dispatcher`` is also overridden, with a spy that records its
calls instead of a fake that fabricates pipeline completion -- this API
layer does not run the pipeline itself (see ``app/api/v1/scans.py``'s
module docstring), so what this test module verifies about ``run_scan``
is that it validates synchronously (real 404/409 checks) and dispatches
with the right arguments, not that a scan completes. Whether the
*dispatched* work is correct is
``tests/integration/test_scan_worker_task.py``'s job, mirroring the
existing split between this module and
``tests/integration/test_scan_pipeline_orchestrator.py`` for the use
case underneath.

Every route now requires a valid httpOnly ``access_token`` cookie (the
project's locked auth-transport decision, PROJECT_STATE.md section 2)
naming an ACTIVE member of the target organization (see
``require_organization_member``, app/api/dependencies.py). Every
pre-existing 404/409/202 assertion below is unchanged in substance --
each test authenticates via the new ``_create_authenticated_member``
helper and sends the resulting access-token cookie on every request.
``TestAuthenticationAndAuthorization`` covers the auth requirement
itself (missing/malformed/wrongly-signed tokens, non-members, and
non-ACTIVE memberships).

``_create_organization()`` calls ``set_org_context()`` before inserting
its test organization -- ``organizations``' own RLS policy
(``id = current_setting('app.current_org_id')::uuid``) requires that GUC
set to the row's own id before an ``INSERT`` can satisfy its
``WITH CHECK`` clause.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import (
    AuthConfig,
    get_active_scanner,
    get_auth_config,
    get_scan_dispatcher,
    get_session_factory,
)
from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.domain.shared.enums import MembershipStatus, OrganizationRole, ScanStatus
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.infrastructure.security.token_service import ACCESS_TOKEN_COOKIE_NAME, create_access_token
from app.main import create_app
from tests.integration.support import (
    make_member,
    make_organization,
    make_scan,
    make_user,
    set_org_context,
)

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "scans-integration-test-secret-value"


class _FakeActiveScanner(ActiveScanner):
    """Only ``.name`` is ever read by this layer (the scanner-mismatch
    check in ``run_scan``) -- ``.execute()`` is never called here, since
    this API process does not run the pipeline; still implemented
    (raising if it somehow were called) so this fake remains a faithful,
    complete ``ActiveScanner`` rather than a partial stub that would
    silently misrepresent what it substitutes for."""

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        raise AssertionError(
            "ActiveScanner.execute() should never be called from the API process -- "
            "the pipeline runs inside the ingestion_worker Celery task, not here. "
            "If this fired, run_scan regressed to calling the pipeline directly again."
        )


class _RecordingDispatcher:
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
def dispatcher() -> _RecordingDispatcher:
    return _RecordingDispatcher()


@pytest.fixture
def wired_app(engine: AsyncEngine, dispatcher: _RecordingDispatcher) -> FastAPI:
    """A fresh ``FastAPI`` app per test, wired to this test's own
    Postgres engine plus a fresh scanner fake, dispatch spy, and a known
    JWT secret -- never the module-level ``app`` in ``app.main``, so no
    test can leak state into another (see ``create_app()``'s own
    docstring on why it is a factory)."""
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    app.dependency_overrides[get_active_scanner] = lambda: _FakeActiveScanner()
    app.dependency_overrides[get_scan_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(
        jwt_secret=_TEST_JWT_SECRET,
        access_token_expire_minutes=15,
        refresh_token_expire_days=30,
    )
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


async def _create_authenticated_member(
    db_session: AsyncSession,
    organization_id: UUID,
    *,
    role: OrganizationRole = OrganizationRole.OWNER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> dict[str, str]:
    """Creates a ``User`` plus an ``OrganizationMember`` row linking them
    to ``organization_id``, then returns an ``{"access_token": <jwt>}``
    dict suitable for httpx's per-request ``cookies=`` parameter -- the
    shared setup every authenticated test in this module needs."""
    user = make_user()
    await SqlAlchemyUserRepository(db_session).add(user)

    await set_org_context(db_session, organization_id)
    member = make_member(organization_id, user.id, role=role, status=status)
    await SqlAlchemyOrganizationRepository(db_session).add_member(member)
    await db_session.commit()

    token = create_access_token(user_id=user.id, secret=_TEST_JWT_SECRET, expire_minutes=15)
    return {ACCESS_TOKEN_COOKIE_NAME: token}


async def test_create_scan_returns_201_with_all_eight_steps_pending(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com", "scanner_name": "nuclei"},
            cookies=cookies,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["organization_id"] == str(org_id)
    assert body["target"] == "example.com"
    assert body["status"] == "queued"
    assert len(body["workflow_steps"]) == 8
    assert all(step["status"] == "pending" for step in body["workflow_steps"])
    assert response.headers["location"] == f"/api/v1/organizations/{org_id}/scans/{body['id']}"


async def test_create_scan_records_the_triggering_user(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com", "scanner_name": "nuclei"},
            cookies=cookies,
        )

    assert response.json()["triggered_by_user_id"] is not None


async def test_create_scan_defaults_scanner_name_to_nuclei(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )

    assert response.status_code == 201
    assert response.json()["scanner_name"] == "nuclei"


async def test_create_scan_rejects_an_unknown_scanner_name_with_422(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com", "scanner_name": "nmap"},
            cookies=cookies,
        )

    assert response.status_code == 422


async def test_create_scan_for_a_nonexistent_organization_returns_404(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    # A real, authenticated user -- just not a member of the org named
    # in the URL, which does not even exist. The 404 for "organization
    # does not exist" is still asserted -- see
    # TestAuthenticationAndAuthorization below for the separate 403
    # case (org exists, caller is not a member of it).
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{uuid4()}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )

    assert response.status_code == 404


async def test_run_scan_returns_202_and_dispatches_the_task(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    """``run`` no longer executes the pipeline or returns a finished
    scan -- it validates synchronously and dispatches, returning 202 with
    the scan's pre-execution state."""
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        create_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )
        scan_id = create_response.json()["id"]

        run_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}/run", cookies=cookies
        )

    assert run_response.status_code == 202
    body = run_response.json()
    # Still QUEUED: no worker actually ran the pipeline in this test --
    # RunScanWorkflowUseCase's own status transitions are
    # tests/integration/test_scan_worker_task.py's and
    # tests/integration/test_scan_pipeline_orchestrator.py's job to
    # verify, not this module's.
    assert body["status"] == ScanStatus.QUEUED.value
    assert dispatcher.calls == [(org_id, UUID(scan_id))]


async def test_run_scan_for_a_nonexistent_scan_returns_404(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{uuid4()}/run", cookies=cookies
        )

    assert response.status_code == 404
    assert dispatcher.calls == []


async def test_run_scan_for_a_nonexistent_organization_returns_404(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{uuid4()}/scans/{uuid4()}/run", cookies=cookies
        )

    assert response.status_code == 404
    assert dispatcher.calls == []


async def test_run_scan_with_a_mismatched_scanner_name_returns_409(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    """A Scan row created some other way than this API's own create
    endpoint (whose request schema only ever accepts "nuclei") with a
    scanner_name this deployment has no adapter for -- exercises the
    ScannerMismatchError -> 409 global exception handler wiring in
    app/main.py."""
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)
    await set_org_context(db_session, org_id)
    scan = make_scan(org_id, scanner_name="not-a-real-scanner")
    await SqlAlchemyScanRepository(db_session).add(scan)
    await db_session.commit()

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{scan.id}/run", cookies=cookies
        )

    assert response.status_code == 409
    assert dispatcher.calls == []


async def test_run_scan_does_not_redispatch_an_already_running_scan(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    """Since dispatch returns almost instantly, a client can trivially
    call run twice in rapid succession. A scan already RUNNING is not
    re-dispatched -- still 202 (the caller's request, "make sure this is
    running," is genuinely satisfied), just without enqueueing a
    duplicate concurrent worker run of the same scan."""
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)
    await set_org_context(db_session, org_id)
    scan = make_scan(org_id, status=ScanStatus.RUNNING)
    await SqlAlchemyScanRepository(db_session).add(scan)
    await db_session.commit()

    async with await _make_client(wired_app) as client:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{scan.id}/run", cookies=cookies
        )

    assert response.status_code == 202
    assert response.json()["status"] == ScanStatus.RUNNING.value
    assert dispatcher.calls == []


async def test_get_scan_returns_its_current_detail(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        create_response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )
        scan_id = create_response.json()["id"]

        get_response = await client.get(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}", cookies=cookies
        )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == scan_id
    assert len(get_response.json()["workflow_steps"]) == 8


async def test_get_scan_for_a_nonexistent_scan_returns_404(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.get(
            f"/api/v1/organizations/{org_id}/scans/{uuid4()}", cookies=cookies
        )

    assert response.status_code == 404


async def test_get_scan_for_a_nonexistent_organization_returns_404(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        response = await client.get(
            f"/api/v1/organizations/{uuid4()}/scans/{uuid4()}", cookies=cookies
        )

    assert response.status_code == 404


async def test_scan_lifecycle_across_three_requests_reflects_async_dispatch(
    wired_app: FastAPI, db_session: AsyncSession, dispatcher: _RecordingDispatcher
) -> None:
    """The vertical slice this API actually exposes: create, run
    (validated and dispatched, not executed), then read back the same
    scan across three separate HTTP requests, the way a real client
    would use it. Completion is not observed here -- there is no worker
    consuming the dispatched task in this test process -- that is
    exactly what moved off this module's plate to
    tests/integration/test_scan_worker_task.py."""
    org_id = await _create_organization(db_session)
    cookies = await _create_authenticated_member(db_session, org_id)

    async with await _make_client(wired_app) as client:
        created = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )
        scan_id = created.json()["id"]
        assert created.json()["status"] == ScanStatus.QUEUED.value

        ran = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}/run", cookies=cookies
        )
        assert ran.status_code == 202
        assert dispatcher.calls == [(org_id, UUID(scan_id))]

        fetched = await client.get(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}", cookies=cookies
        )
        assert fetched.json()["status"] == ScanStatus.QUEUED.value
        assert fetched.json()["completed_at"] is None


class TestAuthenticationAndAuthorization:
    """The auth requirement itself, resolving Technical Debt #9."""

    async def test_no_access_token_cookie_returns_401(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)

        async with await _make_client(wired_app) as client:
            response = await client.post(
                f"/api/v1/organizations/{org_id}/scans", json={"target": "example.com"}
            )

        assert response.status_code == 401

    async def test_malformed_access_token_cookie_returns_401(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)

        async with await _make_client(wired_app) as client:
            response = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies={ACCESS_TOKEN_COOKIE_NAME: "not-a-real-token"},
            )

        assert response.status_code == 401

    async def test_token_signed_with_a_different_secret_returns_401(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)
        bad_token = create_access_token(
            user_id=uuid4(), secret="a-completely-different-secret", expire_minutes=15
        )

        async with await _make_client(wired_app) as client:
            response = await client.get(
                f"/api/v1/organizations/{org_id}/scans/{uuid4()}",
                cookies={ACCESS_TOKEN_COOKIE_NAME: bad_token},
            )

        assert response.status_code == 401

    async def test_authenticated_non_member_returns_403(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)

        # A second, real, authenticated user -- but never added as a
        # member of org_id.
        outsider = make_user()
        await SqlAlchemyUserRepository(db_session).add(outsider)
        await db_session.commit()
        outsider_token = create_access_token(
            user_id=outsider.id, secret=_TEST_JWT_SECRET, expire_minutes=15
        )

        async with await _make_client(wired_app) as client:
            response = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies={ACCESS_TOKEN_COOKIE_NAME: outsider_token},
            )

        assert response.status_code == 403

    async def test_removed_member_returns_403(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)
        cookies = await _create_authenticated_member(
            db_session, org_id, status=MembershipStatus.REMOVED
        )

        async with await _make_client(wired_app) as client:
            response = await client.get(
                f"/api/v1/organizations/{org_id}/scans/{uuid4()}", cookies=cookies
            )

        assert response.status_code == 403

    async def test_invited_but_not_yet_active_member_returns_403(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _create_organization(db_session)
        cookies = await _create_authenticated_member(
            db_session, org_id, status=MembershipStatus.INVITED
        )

        async with await _make_client(wired_app) as client:
            response = await client.get(
                f"/api/v1/organizations/{org_id}/scans/{uuid4()}", cookies=cookies
            )

        assert response.status_code == 403

    async def test_viewer_role_member_can_still_read_and_create(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        # Membership-only gate, not RBAC (out of this work's own scope --
        # Phase 6 on PROJECT_STATE.md's own roadmap): any ACTIVE role,
        # including the least-privileged VIEWER, passes
        # require_organization_member.
        org_id = await _create_organization(db_session)
        cookies = await _create_authenticated_member(
            db_session, org_id, role=OrganizationRole.VIEWER
        )

        async with await _make_client(wired_app) as client:
            response = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies=cookies,
            )

        assert response.status_code == 201
