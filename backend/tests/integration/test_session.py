"""Integration tests for app/infrastructure/db/session.py.

Covers the pieces the rest of the integration suite doesn't exercise
directly: ``create_engine``'s scanner_worker guard, and
``session_scoped_to_org`` end-to-end (the production helper repositories
are meant to be used through in real request handling -- the rest of
this suite calls ``set_org_context`` directly against a fixture-provided
session instead, since that's a lighter-weight way to test repository
CRUD logic in isolation from this helper).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import Environment, Settings, WorkerRole
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.session import (
    create_engine,
    create_session_factory,
    session_scoped_to_org,
)
from tests.integration.support import make_organization

pytestmark = pytest.mark.integration


def test_create_engine_rejects_scanner_worker_settings() -> None:
    """The code-level half of the scanner network isolation boundary
    (app/config.py's ``check_role_boundaries`` already forbids
    scanner_worker from holding a DATABASE_URL at all) -- this is the
    second guard, in case a caller ever constructs Settings a different
    way that skirts the first one."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        worker_role=WorkerRole.SCANNER_WORKER,
        environment=Environment.DEVELOPMENT,
        database_url=None,
        redis_url="redis://localhost:6379/0",
        minio_endpoint="localhost:9000",
        minio_root_user="test",
        minio_root_password="a-real-password",
    )
    with pytest.raises(ValueError, match="scanner_worker must never hold"):
        create_engine(settings)


async def test_create_engine_builds_a_working_engine_from_settings(
    postgres_available: bool,
) -> None:
    if not postgres_available:
        pytest.skip("No PostgreSQL instance reachable")

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        worker_role=WorkerRole.API,
        environment=Environment.DEVELOPMENT,
        database_url=os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
        ),
        redis_url="redis://localhost:6379/0",
        minio_endpoint="localhost:9000",
        minio_root_user="test",
        minio_root_password="a-real-password",
        jwt_secret="a-real-secret",
    )
    engine = create_engine(settings)
    try:
        assert isinstance(engine, AsyncEngine)
        factory = create_session_factory(engine)
        assert isinstance(factory, async_sessionmaker)
        async with factory() as session, session.begin():
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_session_scoped_to_org_sets_the_guc_for_the_whole_transaction(
    engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    org = make_organization()

    async with session_scoped_to_org(session_factory, org.id) as session:
        result = await session.execute(text("SELECT current_setting('app.current_org_id')"))
        assert result.scalar_one() == str(org.id)

        # The GUC stays set for every statement in this transaction, not
        # just the one that set it -- repositories rely on this to run
        # several calls under one org context.
        await SqlAlchemyOrganizationRepository(session).add(org)
        fetched = await SqlAlchemyOrganizationRepository(session).get_by_id(org.id)
        assert fetched is not None


async def test_session_scoped_to_org_is_transaction_local(engine: AsyncEngine) -> None:
    """``SET LOCAL`` (via ``set_config(..., is_local=True)``) does not
    leak into a session obtained afterward from the same pool -- a fresh
    session with no org context set gets an empty-string GUC, per
    ``current_setting``'s documented behavior with ``missing_ok=true``."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    org = make_organization()

    async with session_scoped_to_org(session_factory, org.id):
        pass

    async with session_factory() as fresh_session:
        result = await fresh_session.execute(
            text("SELECT current_setting('app.current_org_id', true)")
        )
        value = result.scalar_one()
        assert value != str(org.id)
