"""Integration test for /internal/health/ready.

Needs a real database connection (see app/api/internal/health.py's
module docstring for why liveness does not, but readiness does), so this
lives in the integration suite and shares the ``engine``/
``postgres_available`` fixtures from tests/conftest.py with the rest of
that suite -- skipping cleanly rather than failing when no PostgreSQL
instance is reachable, same as every other integration test in this
project.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.dependencies import get_session_factory
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_readiness_returns_ok_when_the_database_is_reachable(
    engine: AsyncEngine,
) -> None:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_returns_503_when_the_database_is_unreachable() -> None:
    app = create_app()
    # A session factory bound to a port nothing is listening on --
    # exercises the except branch in readiness() without needing to
    # actually stop a real database mid-test.
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=_unreachable_engine()
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def _unreachable_engine() -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine("postgresql+asyncpg://nobody:nothing@localhost:1/nonexistent")
