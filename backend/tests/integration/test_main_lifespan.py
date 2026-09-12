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

Milestone 7 update: ``_lifespan`` now only constructs a session factory
and scanner adapters (see ``app/main.py``'s module docstring for why
``MinioStoragePort``/``AnthropicProvider``/``AnalysisService`` moved to
``app/workers/tasks.py``'s own composition root instead) -- so this test
only needs ``DATABASE_URL``/``REDIS_URL`` to exercise the real lifespan;
it no longer needs a syntactically-real-looking MinIO endpoint or
Anthropic API key to do so, since this process does not construct either
adapter anymore. The equivalent test for the *worker's* composition
root, ``_run_scan_workflow_from_settings`` (which does still need those,
plus a real Anthropic-shaped key, for exactly the reasons this module's
old version did), now lives in
``tests/integration/test_scan_worker_task.py``.

Phase 4 update: ``_lifespan`` now constructs ``NmapAdapter`` alongside
``NucleiAdapter`` -- both credential-free, same as before -- and
``AppState.active_scanner`` (singular) became ``active_scanners``
(plural, a tuple of both). Asserted below the same way the single
adapter already was.

Update: ``_lifespan`` now also builds an ``AuthConfig``
(``app/api/dependencies.py``) from ``Settings`` and adds it to
``AppState`` -- asserted below the same way ``active_scanner`` already
is, since ``JWT_SECRET`` has been required for ``worker_role=api`` since
Milestone 1 but was genuinely unused until this work.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.api.dependencies import AppState
from app.application.interfaces.scanner_port import ActiveScanner
from app.config import get_settings
from app.main import create_app
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
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
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "20")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "45")
    # No ANTHROPIC_API_KEY set -- and none is needed: as of Milestone 7,
    # worker_role=api no longer requires it (see
    # app/config.py's check_role_boundaries), since this process never
    # constructs AnalysisService/AnthropicProvider anymore.
    get_settings.cache_clear()

    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            wired = app.state.wired
            assert isinstance(wired, AppState)
            assert isinstance(wired.active_scanners, tuple)
            assert all(isinstance(s, ActiveScanner) for s in wired.active_scanners)
            # Phase 4: both wired adapters, not just nuclei -- proves
            # _lifespan actually constructs NmapAdapter now too, not
            # just that the tuple type-checks.
            assert {s.name for s in wired.active_scanners} == {"nuclei", "nmap"}
            assert any(isinstance(s, NucleiAdapter) for s in wired.active_scanners)
            assert any(isinstance(s, NmapAdapter) for s in wired.active_scanners)
            assert wired.auth_config.jwt_secret == "a-real-secret"
            assert wired.auth_config.access_token_expire_minutes == 20
            assert wired.auth_config.refresh_token_expire_days == 45

            # The one part of AppState that does need to actually work
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
