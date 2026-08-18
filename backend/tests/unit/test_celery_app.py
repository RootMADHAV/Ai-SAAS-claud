"""Unit test for ``app/workers/celery_app.py`` -- the Celery application
instance Milestone 7 introduces (PROJECT_STATE.md section 15;
Technical debt item #10 in docs/implementation_progress.md, now
resolved).

No real Redis broker needs to be reachable for this test:
``Celery(broker=..., backend=...)`` only stores the connection URL at
construction time -- it does not connect eagerly, the same "constructing
an adapter does not make a network call" reasoning already established
for ``MinioStoragePort``/``AnthropicProvider`` in
``tests/integration/test_main_lifespan.py``'s own module docstring, kept
at unit-test tier here since (unlike those two) nothing about this
module needs a real database either.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.workers.celery_app import SCANS_QUEUE_NAME, create_celery_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_required_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    defaults = {
        "WORKER_ROLE": "ingestion_worker",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ROOT_USER": "test-user",
        "MINIO_ROOT_PASSWORD": "a-real-minio-password",
        "MINIO_BUCKET": "scan-raw-output",
        "ANTHROPIC_API_KEY": "a-real-anthropic-key",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


def test_create_celery_app_uses_redis_url_for_broker_and_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch, REDIS_URL="redis://some-host:6380/2")

    app = create_celery_app()

    assert app.conf.broker_url == "redis://some-host:6380/2"
    assert app.conf.result_backend == "redis://some-host:6380/2"


def test_create_celery_app_sets_the_scans_queue_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)

    app = create_celery_app()

    assert app.conf.task_default_queue == SCANS_QUEUE_NAME == "scans"


def test_create_celery_app_works_from_the_api_role_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API process only ever calls .delay() on a task defined
    against this app -- it never runs the Celery worker runtime itself
    -- so constructing this app must succeed for worker_role=api just as
    well as for worker_role=ingestion_worker (see this module's own
    module docstring)."""
    _set_required_env(monkeypatch, WORKER_ROLE="api", JWT_SECRET="a-real-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app = create_celery_app()

    assert app.conf.broker_url == "redis://localhost:6379/0"
