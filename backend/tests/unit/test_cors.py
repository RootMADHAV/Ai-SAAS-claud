"""Unit tests for create_app()'s CORS wiring.

Uses /internal/health/live -- the one route that needs no database or
other process-lifetime wiring at all (see test_health.py's own
docstring) -- so these tests can exercise the real CORSMiddleware
without a Postgres instance, the same "no wiring needed" property that
lets test_health.py run as a unit test rather than an integration one.

get_cors_allowed_origins()'s own parsing behavior is covered in
test_config.py; these tests only cover create_app()'s decision to add
(or not add) CORSMiddleware and what it actually returns to a browser.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.main import create_app


async def _get_with_origin(app: FastAPI, origin: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/internal/health/live", headers={"Origin": origin})


async def test_no_cors_header_when_no_origins_are_configured() -> None:
    """Explicit ``cors_allowed_origins=[]`` -- not the ``None`` default --
    so this test's result cannot depend on whether CORS_ALLOWED_ORIGINS
    happens to be set in whatever environment runs the suite."""
    app = create_app(cors_allowed_origins=[])

    response = await _get_with_origin(app, "http://localhost:3000")

    assert "access-control-allow-origin" not in response.headers


async def test_configured_origin_gets_cors_headers_with_credentials_allowed() -> None:
    app = create_app(cors_allowed_origins=["http://localhost:3000"])

    response = await _get_with_origin(app, "http://localhost:3000")

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_an_unconfigured_origin_gets_no_cors_header() -> None:
    app = create_app(cors_allowed_origins=["http://localhost:3000"])

    response = await _get_with_origin(app, "http://evil.example.com")

    assert "access-control-allow-origin" not in response.headers
