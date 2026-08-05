"""Integration test for ``app.main``'s ``_lifespan`` -- the one piece of
Milestone 5 the API-behavior tests in test_api_scans.py and
test_health_ready.py deliberately never exercise, since they build their
app via ``create_app()`` and override its low-level dependencies instead
of ever running the real lifespan (see ``create_app()``'s own docstring
for why). That is the right call for testing HTTP behavior in isolation
from process-startup wiring, but it leaves the wiring itself -- "does
``_lifespan`` actually populate ``app.state.wired`` with working
objects" -- genuinely untested unless something does so directly. This
module is that something.

Only ``DATABASE_URL`` needs to point at something real
(``TEST_DATABASE_URL``, via the shared ``postgres_available`` fixture):
``MinioStoragePort.__init__`` only constructs a ``Minio`` client object
and stores config -- it does not connect eagerly -- so a MinIO endpoint
that nothing is listening on is fine for confirming that lifespan wires
the *shape* of ``AppState`` correctly, without needing a real MinIO
server (unavailable in this environment -- the same constraint already
documented for Milestone 3's own test suite).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.api.dependencies import AppState
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.storage_port import StoragePort
from app.config import get_settings
from app.infrastructure.storage.minio_storage import MinioStoragePort
from app.main import create_app
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter

pytestmark = pytest.mark.integration


async def test_lifespan_wires_app_state_with_working_objects(
    postgres_available: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not postgres_available:
        pytest.skip("No PostgreSQL instance reachable for this lifespan test")

    monkeypatch.setenv("WORKER_ROLE", "api")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "a-real-minio-password")
    monkeypatch.setenv("MINIO_BUCKET", "scan-raw-output")
    monkeypatch.setenv("JWT_SECRET", "a-real-secret")
    get_settings.cache_clear()

    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            wired = app.state.wired
            assert isinstance(wired, AppState)
            assert isinstance(wired.active_scanner, ActiveScanner)
            assert isinstance(wired.active_scanner, NucleiAdapter)
            assert isinstance(wired.storage, StoragePort)
            assert isinstance(wired.storage, MinioStoragePort)

            # The one part of AppState that *does* need to actually work
            # end-to-end here: a real query against the real test database,
            # proving create_engine()/create_session_factory() wired a
            # genuinely usable session factory, not just an object of the
            # right type.
            async with wired.session_factory() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        # Exiting the `async with` block runs _lifespan's `finally` clause,
        # which disposes the engine -- nothing further to assert on that
        # directly, but a hang or an exception here would signal
        # engine.dispose() itself is broken.
    finally:
        get_settings.cache_clear()
