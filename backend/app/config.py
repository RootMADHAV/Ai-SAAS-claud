"""Centralized, typed, validated configuration.

Loaded once at startup, not read from os.getenv() scattered through the
code. Fails fast: a missing or placeholder value is a startup error, not
something discovered on the first request that needs it.

WORKER_ROLE drives which settings are required vs. forbidden. In
particular, a process with WORKER_ROLE=scanner_worker must never even
construct database settings -- see docs/security_model.md on scanner
network isolation. That isolation is enforced two ways: the network
segmentation in docker-compose.yml (the scanner-worker container has no
route to postgres), and this check (the scanner-worker process is never
handed the credentials in the first place). A secret a process never loads
can't leak if that process is compromised. Belt and suspenders, not a
substitute for the network isolation.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TEST = "test"


class WorkerRole(StrEnum):
    API = "api"
    INGESTION_WORKER = "ingestion_worker"
    SCANNER_WORKER = "scanner_worker"


_DEV_ONLY_SECRETS = {"", "change_me_dev_only", "devpassword"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    worker_role: WorkerRole

    # Flat fields, matching .env.example exactly -- pydantic-settings maps
    # DATABASE_URL -> database_url automatically when case_sensitive=False.
    # Optional at the type level because not every process needs every
    # group; check_role_boundaries below enforces which are actually
    # required for a given role.
    database_url: str | None = None
    redis_url: str | None = None
    minio_endpoint: str | None = None
    minio_root_user: str | None = None
    minio_root_password: str | None = None
    minio_bucket: str | None = None
    qdrant_url: str | None = None

    jwt_secret: str | None = None
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    ai_default_provider: str = "anthropic"
    # Which model the configured provider should use -- generically named
    # (not "anthropic_model") since its meaning ("model identifier for
    # whichever provider ai_default_provider selects") stays valid once a
    # second provider adapter exists, the same forward-compatible naming
    # already applied to ai_default_provider itself. Defaults to a
    # rolling alias, not a dated snapshot, so this value does not go
    # stale the moment Anthropic ships a new snapshot under the same
    # model family.
    ai_model: str = "claude-sonnet-4-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    ollama_base_url: str | None = None

    sentry_dsn: str | None = None
    otel_exporter_endpoint: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def check_role_boundaries(self) -> Settings:
        # Every role needs the queue, and every role needs object storage --
        # even scanner_worker, which writes raw scan output to MinIO for the
        # ingestion worker to pick up. "Not configured" and "configured with
        # a dev-default value" are different failure modes and get checked
        # separately below, rather than treating a missing value as if it
        # were a placeholder value.
        if self.redis_url is None:
            raise ValueError("REDIS_URL is required for every worker_role")
        if not all(
            (
                self.minio_endpoint,
                self.minio_root_user,
                self.minio_root_password,
                self.minio_bucket,
            )
        ):
            raise ValueError(
                "MinIO settings (MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, "
                "MINIO_BUCKET) are required for every worker_role"
            )

        # Only the scanner-worker is forbidden from holding database
        # credentials -- see docs/security_model.md.
        if self.worker_role is WorkerRole.SCANNER_WORKER:
            if self.database_url is not None:
                raise ValueError(
                    "scanner_worker must not have DATABASE_URL configured -- "
                    "see docs/security_model.md on scanner network isolation"
                )
        elif self.database_url is None:
            raise ValueError(f"DATABASE_URL is required for worker_role={self.worker_role}")

        # Only the API issues and verifies tokens; workers never need one.
        if self.worker_role is WorkerRole.API and self.jwt_secret is None:
            raise ValueError("JWT_SECRET is required for worker_role=api")

        # Milestone 7 update: RunScanWorkflowUseCase (and therefore
        # AnalysisService/AnthropicProvider) now runs inside the
        # ingestion_worker Celery task (app/workers/tasks.py), not
        # inside the API's HTTP request handler -- the API process only
        # ever checks a Scan's existence/scanner_name and dispatches a
        # task (app/api/v1/scans.py's run_scan route); it never
        # constructs AnalysisService itself anymore (see app/main.py's
        # _lifespan, which correspondingly stopped constructing
        # AnthropicProvider/AnalysisService/MinioStoragePort this
        # milestone). So the AI-provider-credential requirement follows
        # the process that actually needs it: ingestion_worker, not api.
        # Still scoped to "anthropic" specifically, not every possible
        # provider key at once -- AnthropicProvider remains the only
        # adapter this codebase builds (PROJECT_STATE.md section 3's
        # "don't build a registry for one real implementation"
        # reasoning, applied here to config validation too) -- requiring,
        # say, OPENAI_API_KEY before an OpenAI adapter exists would
        # demand a credential nothing in this codebase can use yet.
        # Pointing ai_default_provider at any other provider name
        # currently fails at composition-root wiring time
        # (app/workers/tasks.py's _run_scan_workflow_from_settings, the
        # ingestion_worker's own composition root, mirroring
        # app/main.py's _lifespan exactly), not here, since that failure
        # is about what adapter exists to construct, not about a missing
        # credential.
        if (
            self.worker_role is WorkerRole.INGESTION_WORKER
            and self.ai_default_provider == "anthropic"
            and self.anthropic_api_key is None
        ):
            raise ValueError(
                "ANTHROPIC_API_KEY is required for worker_role=ingestion_worker when "
                "AI_DEFAULT_PROVIDER=anthropic (the default)"
            )

        if self.environment is Environment.PRODUCTION:
            if self.jwt_secret is not None and self.jwt_secret in _DEV_ONLY_SECRETS:
                raise ValueError(
                    "JWT_SECRET must be a real secret in production, not a dev default"
                )
            if self.minio_root_password in _DEV_ONLY_SECRETS:
                raise ValueError("MINIO_ROOT_PASSWORD must not be a dev default in production")
            if self.anthropic_api_key is not None and self.anthropic_api_key in _DEV_ONLY_SECRETS:
                raise ValueError(
                    "ANTHROPIC_API_KEY must be a real secret in production, not a dev default"
                )

        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so Settings() -- which reads the environment -- only runs
    once per process, not once per request."""
    return Settings()  # type: ignore[call-arg]


def get_cors_allowed_origins() -> list[str]:
    """Comma-separated ``CORS_ALLOWED_ORIGINS``, read directly from the
    environment rather than through ``Settings`` above -- deliberately,
    not an oversight or a lapse in "centralized, typed configuration."

    ``Settings()`` requires ``WORKER_ROLE`` (no default) plus every other
    field ``check_role_boundaries`` demands for that role (``DATABASE_URL``,
    ``REDIS_URL``, the MinIO settings, ...) before it will construct at
    all -- the fail-fast design this module's own docstring describes.
    ``app/main.py``'s ``create_app()`` is called argument-free,
    unconditionally, by every existing test in this codebase
    (``from app.main import create_app`` runs the module body, including
    the bottom-of-file ``app = create_app()`` line, on import) -- routing
    CORS origins through ``Settings`` would make importing ``app.main``
    itself require a complete, production-shaped environment, which is
    not true today (see ``create_app()``'s own docstring on why it must
    not require a database connection to succeed) and must not become
    true as a side effect of adding CORS support.

    Also a genuinely different kind of setting than everything else in
    this file: CORS matters only to ``worker_role=api`` (the one process
    that ever serves browser traffic at all) and is not a secret --
    neither property this module's shared ``check_role_boundaries``
    validation (built for credentials and per-role required
    infrastructure) is designed around. A narrow, explicitly-justified
    exception to "not read from os.getenv() scattered through the code,"
    not a precedent for reading other settings this way.

    Unset or empty -> ``[]`` (CORS disabled entirely -- the same
    zero-cross-origin-browser-access behavior this API had before this
    setting existed), not a wildcard fallback.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
