"""Integration tests for the Organization bootstrap API
(``POST /api/v1/organizations``) -- Phase 3 backend preparation, not a
Phase 2 milestone or part of the Auth work's own scope (see
PROJECT_STATE.md's Phase 3 backend-preparation note).

Mirrors test_api_auth.py's/test_api_scans.py's own
httpx.AsyncClient/ASGITransport pattern and dependency-override
conventions -- see those modules' own docstrings for why. Only
``get_session_factory``/``get_auth_config`` are overridden;
``get_current_user``, ``get_new_organization_id``,
``get_org_bootstrap_session``, and ``CreateOrganizationUseCase`` all run
for real here, so these tests exercise the actual RLS-scoped
organization creation, not a bypassed version of it.
"""

from __future__ import annotations

from uuid import UUID

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
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.security.token_service import ACCESS_TOKEN_COOKIE_NAME, create_access_token
from app.main import create_app
from tests.integration.support import make_user, set_org_context

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "organizations-integration-test-secret-value"


class _FakeActiveScanner(ActiveScanner):
    """Only needed by the full-flow test at the bottom of this module,
    which exercises scan creation/run after organization bootstrap --
    mirrors test_api_scans.py's identical fake exactly (see that
    module's own docstring for why ``.execute()`` raises rather than
    running anything)."""

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        raise AssertionError("not exercised by this module's tests")


def _noop_dispatcher(organization_id: UUID, scan_id: UUID) -> None:
    """This module never asserts anything about dispatch itself (that is
    test_api_scans.py's job) -- just needs a real Celery broker call
    replaced with something harmless for the one test here that reaches
    ``run_scan`` at all."""


@pytest.fixture
def wired_app(engine: AsyncEngine) -> FastAPI:
    """A fresh ``FastAPI`` app per test, wired to this test's own
    Postgres engine plus a known JWT secret -- never the module-level
    ``app`` in ``app.main`` (see ``create_app()``'s own docstring)."""
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(
        jwt_secret=_TEST_JWT_SECRET,
        access_token_expire_minutes=15,
        refresh_token_expire_days=30,
    )
    return app


async def _make_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_authenticated_user(db_session: AsyncSession) -> tuple[UUID, dict[str, str]]:
    """A real ``User`` row with no organization membership at all -- the
    exact starting state this endpoint exists to move a caller out of
    (register -> login -> no org yet -> this endpoint)."""
    user = make_user()
    await SqlAlchemyUserRepository(db_session).add(user)
    await db_session.commit()
    token = create_access_token(user_id=user.id, secret=_TEST_JWT_SECRET, expire_minutes=15)
    return user.id, {ACCESS_TOKEN_COOKIE_NAME: token}


async def test_create_organization_returns_201_with_the_new_organization(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    _, cookies = await _create_authenticated_user(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/organizations",
            json={"name": "Acme Security", "slug": "acme-security"},
            cookies=cookies,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme Security"
    assert body["slug"] == "acme-security"
    assert response.headers["location"] == f"/api/v1/organizations/{body['id']}"


async def test_create_organization_makes_the_caller_an_active_owner(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    user_id, cookies = await _create_authenticated_user(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/organizations",
            json={"name": "Acme Security", "slug": "acme-security-2"},
            cookies=cookies,
        )
    org_id = UUID(response.json()["id"])

    # organization_members carries an RLS policy of its own
    # (organization_id = current_setting(...)) -- db_session has no org
    # context set yet (the HTTP request above ran in its own, separate
    # session), so it must be set explicitly before this direct
    # repository read, the same way test_identity_repository.py already
    # does for every org-scoped query.
    await set_org_context(db_session, org_id)
    member = await SqlAlchemyOrganizationRepository(db_session).get_member(org_id, user_id)
    assert member is not None
    assert member.role is OrganizationRole.OWNER
    assert member.status is MembershipStatus.ACTIVE


async def test_create_organization_rejects_a_duplicate_slug_with_409(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    _, first_cookies = await _create_authenticated_user(db_session)
    _, second_cookies = await _create_authenticated_user(db_session)

    async with await _make_client(wired_app) as client:
        first = await client.post(
            "/api/v1/organizations",
            json={"name": "First Org", "slug": "dupe-slug"},
            cookies=first_cookies,
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/organizations",
            json={"name": "Second Org", "slug": "dupe-slug"},
            cookies=second_cookies,
        )

    assert second.status_code == 409


async def test_create_organization_rejects_an_invalid_slug_with_422(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    _, cookies = await _create_authenticated_user(db_session)

    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/organizations",
            json={"name": "Acme Security", "slug": "Not A Valid Slug!"},
            cookies=cookies,
        )

    assert response.status_code == 422


async def test_create_organization_with_no_access_token_cookie_returns_401(
    wired_app: FastAPI,
) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/organizations", json={"name": "Acme Security", "slug": "acme-security-3"}
        )

    assert response.status_code == 401


async def test_full_bootstrap_flow_register_login_create_org_create_and_run_scan(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    """The exact MVP vertical slice Phase 3 needs: register, login,
    create an organization (obtaining membership), then create and run a
    scan inside it -- all over real HTTP, no dependency shortcuts beyond
    the session factory/JWT secret every test in this module already
    overrides.

    Cookies are forwarded explicitly between requests rather than relied
    on to round-trip through the client's own cookie jar -- see
    test_api_auth.py's module docstring for why (Secure cookies over
    this transport's plain http:// scheme)."""
    wired_app.dependency_overrides[get_active_scanner] = lambda: _FakeActiveScanner()
    wired_app.dependency_overrides[get_scan_dispatcher] = lambda: _noop_dispatcher

    async with await _make_client(wired_app) as client:
        register = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "founder@example.com",
                "password": "correct-horse-battery",
                "full_name": "Founder",
            },
        )
        assert register.status_code == 201

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "founder@example.com", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200
        cookies = {ACCESS_TOKEN_COOKIE_NAME: login.cookies[ACCESS_TOKEN_COOKIE_NAME]}

        created_org = await client.post(
            "/api/v1/organizations",
            json={"name": "Founder Org", "slug": "founder-org"},
            cookies=cookies,
        )
        assert created_org.status_code == 201
        org_id = created_org.json()["id"]

        created_scan = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=cookies,
        )
        assert created_scan.status_code == 201
        scan_id = created_scan.json()["id"]

        ran = await client.post(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}/run", cookies=cookies
        )
        assert ran.status_code == 202

        fetched = await client.get(
            f"/api/v1/organizations/{org_id}/scans/{scan_id}", cookies=cookies
        )
        assert fetched.status_code == 200


async def test_second_user_cannot_use_the_first_users_organization_without_membership(
    wired_app: FastAPI, db_session: AsyncSession
) -> None:
    """Confirms this endpoint does not accidentally grant access beyond
    the creator -- a second, real, authenticated user, with no
    membership of their own, still gets 403 from a Scanning route inside
    the first user's organization (require_organization_member, exactly
    as test_api_scans.py's own TestAuthenticationAndAuthorization class
    already verifies for a directly-inserted org; this is the same check
    against an org created via this new endpoint instead)."""
    _, first_cookies = await _create_authenticated_user(db_session)
    _, second_cookies = await _create_authenticated_user(db_session)

    async with await _make_client(wired_app) as client:
        created_org = await client.post(
            "/api/v1/organizations",
            json={"name": "First User Org", "slug": "first-user-org"},
            cookies=first_cookies,
        )
        org_id = created_org.json()["id"]

        response = await client.post(
            f"/api/v1/organizations/{org_id}/scans",
            json={"target": "example.com"},
            cookies=second_cookies,
        )

    assert response.status_code == 403
