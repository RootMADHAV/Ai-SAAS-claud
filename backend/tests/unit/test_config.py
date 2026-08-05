"""Unit tests for Settings.

Every test passes explicit kwargs and _env_file=None so a real .env file or
real environment variables on whatever machine runs pytest can never leak
into an assertion.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Environment, Settings, WorkerRole, get_settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "worker_role": WorkerRole.API,
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "redis_url": "redis://localhost:6379/0",
        "minio_endpoint": "localhost:9000",
        "minio_root_user": "test-user",
        "minio_root_password": "a-real-minio-password",
        "minio_bucket": "scan-raw-output",
        "jwt_secret": "a-real-secret",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg,arg-type]


def test_api_role_requires_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        _settings(database_url=None)


def test_every_role_requires_redis_url() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL is required"):
        _settings(redis_url=None)


def test_every_role_requires_minio_settings() -> None:
    """Including scanner_worker -- it writes raw scan output to MinIO for
    the ingestion worker to pick up, per the network segmentation design."""
    with pytest.raises(ValidationError, match="MinIO settings"):
        _settings(minio_root_password=None)


def test_every_role_requires_minio_bucket() -> None:
    """Milestone 5 addition: the composition root (app/main.py) needs a
    bucket name to construct ``MinioStoragePort`` -- see PROJECT_STATE.md
    section 3's Milestone 5 entry for why this joined the other MinIO
    settings as a required field rather than a hardcoded string."""
    with pytest.raises(ValidationError, match="MinIO settings"):
        _settings(minio_bucket=None)


def test_api_role_requires_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET is required for worker_role=api"):
        _settings(jwt_secret=None)


def test_non_api_roles_do_not_require_jwt_secret() -> None:
    settings = _settings(worker_role=WorkerRole.INGESTION_WORKER, jwt_secret=None)
    assert settings.jwt_secret is None


def test_scanner_worker_rejects_database_url() -> None:
    """The code-level half of the scanner network isolation boundary: this
    process must never even be handed database credentials."""
    with pytest.raises(ValidationError, match="must not have DATABASE_URL"):
        _settings(worker_role=WorkerRole.SCANNER_WORKER, database_url="postgresql://x/y")


def test_scanner_worker_with_no_database_url_is_valid() -> None:
    settings = _settings(worker_role=WorkerRole.SCANNER_WORKER, database_url=None)
    assert settings.worker_role is WorkerRole.SCANNER_WORKER
    assert settings.database_url is None


def test_production_rejects_dev_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET must be a real secret"):
        _settings(environment=Environment.PRODUCTION, jwt_secret="change_me_dev_only")


def test_production_rejects_missing_jwt_secret() -> None:
    """Caught by the general API-role requirement, not a production-only
    rule -- a missing secret is invalid in every environment, not just
    production."""
    with pytest.raises(ValidationError, match="JWT_SECRET is required for worker_role=api"):
        _settings(environment=Environment.PRODUCTION, jwt_secret=None)


def test_production_accepts_a_real_jwt_secret() -> None:
    settings = _settings(environment=Environment.PRODUCTION, jwt_secret="a-long-real-secret-value")
    assert settings.environment is Environment.PRODUCTION


def test_production_rejects_dev_default_minio_password() -> None:
    with pytest.raises(ValidationError, match="MINIO_ROOT_PASSWORD must not be a dev default"):
        _settings(
            environment=Environment.PRODUCTION,
            jwt_secret="a-long-real-secret-value",
            minio_root_password="devpassword",
        )


def test_development_allows_dev_default_jwt_secret() -> None:
    settings = _settings(environment=Environment.DEVELOPMENT, jwt_secret="change_me_dev_only")
    assert settings.jwt_secret == "change_me_dev_only"


def test_get_settings_reads_from_real_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every other test bypasses environment loading entirely via explicit
    kwargs -- this is the one test that proves get_settings() actually
    reads os.environ, not just that Settings() validates correctly when
    handed values directly."""
    get_settings.cache_clear()
    monkeypatch.setenv("WORKER_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "a-real-minio-password")
    monkeypatch.setenv("MINIO_BUCKET", "scan-raw-output")
    monkeypatch.setenv("JWT_SECRET", "a-real-secret")

    settings = get_settings()

    assert settings.worker_role is WorkerRole.API
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
    get_settings.cache_clear()
