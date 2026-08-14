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
documented for Milestone 3's own test suite). As of Milestone 6, the
same is true of ``AnthropicProvider.__init__``: constructing
``anthropic.AsyncAnthropic(api_key=...)`` does not make a network call,
so a syntactically-real-looking-but-fake API key is fine for confirming
``_lifespan`` wires an ``AnalysisService`` of the right shape, without
needing a real Anthropic credential or a live call to the provider (the
same verification tier already documented for this adapter's own unit
tests, ``test_anthropic_provider.py``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.ai_agents.analysis_service import AnalysisService
from app.api.dependencies import AppState
from app.application.interfaces.ai_provider_port import AIProviderPort
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.storage_port import StoragePort
from app.config import get_settings
from app.infrastructure.ai_providers.anthropic_provider import AnthropicProvider
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-real-anthropic-key")
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
            assert isinstance(wired.analysis_service, AnalysisService)
            assert wired.analysis_service.provider_name == "anthropic"
            # AnalysisService intentionally exposes no way to reach its
            # provider other than provider_name (see that property's own
            # docstring) -- confirming the concrete adapter type wired in
            # requires the one reach-into-a-private-attribute this test
            # allows itself, mirroring test_run_scan_workflow.py's own
            # precedent for exercising a use case's private method
            # directly where no public seam exists.
            assert isinstance(wired.analysis_service._provider, AIProviderPort)  # noqa: SLF001
            assert isinstance(wired.analysis_service._provider, AnthropicProvider)  # noqa: SLF001

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


async def test_lifespan_rejects_an_unimplemented_ai_provider(
    postgres_available: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AnthropicProvider is the only AIProviderPort implementation this
    milestone builds -- see run_scan_workflow.py's module docstring and
    app/main.py's _lifespan. Pointing AI_DEFAULT_PROVIDER at anything else
    must fail loudly at startup, not silently construct nothing or fall
    back to a default a deployer never asked for."""
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
    monkeypatch.setenv("AI_DEFAULT_PROVIDER", "openai")
    get_settings.cache_clear()

    app = create_app()
    try:
        with pytest.raises(ValueError, match="has no adapter wired"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        get_settings.cache_clear()
