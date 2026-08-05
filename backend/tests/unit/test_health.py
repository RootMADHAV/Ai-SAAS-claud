"""Unit test for /internal/health/live.

Liveness needs no database and no other process-lifetime wiring at all
(see app/api/internal/health.py's module docstring), so this is the one
health check that can run against ``create_app()`` directly with no
dependency overrides -- ``httpx.ASGITransport`` calls the app in-process,
in the current event loop, without ever running its ``lifespan`` (that
only happens for a real server start or an explicit lifespan manager),
which is exactly why this route must not need anything ``_lifespan``
would have built.

Readiness (``/internal/health/ready``) does need the database and is
tested in tests/integration/test_health_ready.py instead, using the same
dependency-override pattern as tests/integration/test_api_scans.py.
"""

from __future__ import annotations

import httpx

from app.main import create_app


async def test_liveness_returns_ok_with_no_wiring_at_all() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
