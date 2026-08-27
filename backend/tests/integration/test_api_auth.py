"""Integration tests for the public Identity & Access authentication API
(``/api/v1/auth/...``).

Exercises the real FastAPI app (``app.main.create_app()``) end-to-end
over HTTP via ``httpx.AsyncClient``/``ASGITransport`` -- the same
pattern ``test_api_scans.py`` already established (see that module's
own docstring for why, not Starlette's ``TestClient``).

Only ``get_session_factory`` and ``get_auth_config`` are overridden --
everything built on top of them (``get_identity_session``, the
repository/use-case providers, ``register``/``login``/``refresh``
themselves) runs for real, the same "override exactly the providers with
a real external dependency" principle app/api/dependencies.py's module
docstring already states and ``test_api_scans.py`` already follows for
the Scanning routes.

Tokens are delivered exclusively as httpOnly cookies, not in the JSON
response body (the project's locked auth-transport decision,
PROJECT_STATE.md section 2). Cookies from one response are forwarded
explicitly to the next request via ``cookies=...`` rather than relied on
to round-trip automatically through the client's own cookie jar --
these cookies are set ``Secure``, which a standards-compliant cookie jar
will not attach to a later request over this test transport's plain
``http://`` scheme, so tests forward them explicitly instead of assuming
jar behavior neither this test suite nor a real browser needs "Secure"
weakened to get right.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.dependencies import AuthConfig, get_auth_config, get_session_factory
from app.infrastructure.security.token_service import (
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    decode_access_token,
)
from app.main import create_app

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "auth-integration-test-secret-value"


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


def _unique_email() -> str:
    return f"user-{uuid4()}@example.com"


def _extract_cookie(response: httpx.Response, name: str) -> str:
    value = response.cookies.get(name)
    assert value is not None, f"expected a {name!r} cookie on the response, found none"
    return value


async def test_register_returns_201_and_the_created_user_with_no_cookies(
    wired_app: FastAPI,
) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-horse-battery", "full_name": "Alice"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert body["full_name"] == "Alice"
    assert "id" in body
    assert "password" not in body
    assert "hashed_password" not in body
    # No auth cookies -- registering does not start a session (see
    # app/api/v1/auth.py's module docstring on why).
    assert ACCESS_TOKEN_COOKIE_NAME not in response.cookies
    assert REFRESH_TOKEN_COOKIE_NAME not in response.cookies


async def test_register_rejects_a_duplicate_email_with_409(wired_app: FastAPI) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        first = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "first-password", "full_name": "First"},
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "second-password", "full_name": "Second"},
        )

    assert second.status_code == 409


async def test_register_rejects_a_short_password_with_422(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": _unique_email(), "password": "short", "full_name": "Bob"},
        )

    assert response.status_code == 422


async def test_register_rejects_an_invalid_email_with_422(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "a-fine-password", "full_name": "Bob"},
        )

    assert response.status_code == 422


async def test_login_sets_httponly_cookies_and_returns_the_user(wired_app: FastAPI) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-password", "full_name": "Carol"},
        )

        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "correct-password"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    # Never in the body -- see module docstring.
    assert "access_token" not in body
    assert "refresh_token" not in body

    access_token = _extract_cookie(response, ACCESS_TOKEN_COOKIE_NAME)
    refresh_token = _extract_cookie(response, REFRESH_TOKEN_COOKIE_NAME)
    decode_access_token(access_token, secret=_TEST_JWT_SECRET)  # does not raise
    assert refresh_token

    set_cookie_headers = response.headers.get_list("set-cookie")
    access_cookie_header = next(h for h in set_cookie_headers if h.startswith("access_token="))
    refresh_cookie_header = next(h for h in set_cookie_headers if h.startswith("refresh_token="))
    for header in (access_cookie_header, refresh_cookie_header):
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "samesite=lax" in header.lower()


async def test_login_rejects_wrong_password_with_401(wired_app: FastAPI) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-password", "full_name": "Dana"},
        )

        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )

    assert response.status_code == 401
    assert ACCESS_TOKEN_COOKIE_NAME not in response.cookies


async def test_login_rejects_unknown_email_with_401(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": _unique_email(), "password": "whatever-password"},
        )

    assert response.status_code == 401


async def test_refresh_reads_the_cookie_rotates_it_and_returns_the_user(
    wired_app: FastAPI,
) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-password", "full_name": "Eve"},
        )
        login_response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "correct-password"}
        )
        original_refresh_token = _extract_cookie(login_response, REFRESH_TOKEN_COOKIE_NAME)

        # Cookies are forwarded explicitly (see module docstring) rather
        # than relied on to round-trip through the client's own jar.
        response = await client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_TOKEN_COOKIE_NAME: original_refresh_token},
        )

    assert response.status_code == 200
    assert response.json()["email"] == email
    new_refresh_token = _extract_cookie(response, REFRESH_TOKEN_COOKIE_NAME)
    assert new_refresh_token != original_refresh_token
    assert ACCESS_TOKEN_COOKIE_NAME in response.cookies


async def test_refresh_rejects_a_reused_rotated_token_with_401(wired_app: FastAPI) -> None:
    email = _unique_email()
    async with await _make_client(wired_app) as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-password", "full_name": "Frank"},
        )
        login_response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "correct-password"}
        )
        original_refresh_token = _extract_cookie(login_response, REFRESH_TOKEN_COOKIE_NAME)

        first = await client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_TOKEN_COOKIE_NAME: original_refresh_token},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_TOKEN_COOKIE_NAME: original_refresh_token},
        )

    assert second.status_code == 401


async def test_refresh_rejects_an_unknown_token_with_401(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_TOKEN_COOKIE_NAME: "a-token-that-was-never-issued"},
        )

    assert response.status_code == 401


async def test_refresh_with_no_cookie_at_all_returns_401(wired_app: FastAPI) -> None:
    async with await _make_client(wired_app) as client:
        response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
