"""Shared pytest fixtures.

Replaces an earlier version of this file discovered during Milestone 2
that imported ``app.core.db`` and ``app.main`` -- neither exists
anywhere in this project (PROJECT_STATE.md section 4 has the actual
folder structure) -- and used sync SQLAlchemy against SQLite plus
FastAPI's ``TestClient``, contradicting the locked stack (PROJECT_STATE.md
section 2: SQLAlchemy async, PostgreSQL). That file was not part of any
documented Milestone 1 deliverable and would have failed at collection
time (``ModuleNotFoundError``) had pytest been run against this exact
repository. It is replaced here rather than left in place; see
``docs/implementation_progress.md`` for the one-time note explaining the
correction, per PROJECT_STATE.md section 12's verification-honesty rule.

Repository tests are an integration suite against a real PostgreSQL
database, not SQLite -- RLS, native UUID columns, and JSONB are
Postgres-specific behavior a SQLite substitute would not actually
exercise, and PROJECT_STATE.md section 11 explicitly reserves real port
implementations for "a smaller integration suite" rather than testing
them with fakes (fakes are for the *consumers* of a port). If no
Postgres instance is reachable at ``TEST_DATABASE_URL``, these tests
skip rather than fail, so the full suite still runs cleanly on a machine
with no local database configured.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.infrastructure.db.models  # noqa: F401  -- populates Base.metadata
from app.infrastructure.db.base import Base

_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
)


async def _postgres_is_reachable(url: str) -> bool:
    probe_engine = create_async_engine(url)
    try:
        async with probe_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        # Originally caught OSError only (connection refused / host not found).
        # Broadened to Exception after discovering that asyncpg raises
        # ``InvalidPasswordError`` (a subclass of asyncpg.PostgresError, NOT
        # OSError) when the TCP connection succeeds but the database user does
        # not exist or has the wrong password.  In that situation the
        # session-scoped ``postgres_available`` fixture returned True, causing
        # every test in the integration suite to fail with an auth error rather
        # than skip cleanly.  Any exception during the probe means "not usable
        # for integration tests" -- treat them all the same way.
        return False
    finally:
        await probe_engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def postgres_available() -> bool:
    return await _postgres_is_reachable(_TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def engine(postgres_available: bool) -> AsyncIterator[AsyncEngine]:
    """A fresh engine per test, against ``TEST_DATABASE_URL``. Skips every
    dependent test (rather than erroring) when nothing is listening --
    the schema itself is assumed already migrated onto that database via
    ``alembic upgrade head`` (see docs/session_state.md for the exact
    setup this project's integration suite expects).

    Deliberately function-scoped, not session-scoped: pytest-asyncio
    gives each async test its own event loop by default, but an asyncpg
    connection pool is bound to the loop it was created in. A
    session-scoped engine's pool would be created inside test 1's loop
    and then handed to test 2's loop, producing exactly the
    ``InterfaceError: another operation is in progress`` failure this
    fixture exists to avoid -- discovered while building this test suite,
    not a hypothetical. The cost is one new connection pool per test,
    which is negligible at this suite's size.
    """
    if not postgres_available:
        pytest.skip(f"No PostgreSQL instance reachable at {_TEST_DATABASE_URL!r}")
    eng = create_async_engine(_TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One session per test, followed by a full truncate of every mapped
    table. Truncating rather than relying on rollback covers repository
    methods that call ``session.flush()`` without an outer commit *and*
    helpers like ``session_scoped_to_org`` that commit their own
    transaction -- either way, the next test starts from an empty
    database.

    ``TRUNCATE`` specifically (not ``DELETE``) because Postgres exempts
    ``TRUNCATE`` from Row-Level Security entirely -- it is not a per-row
    operation. ``app_user`` has no ``BYPASSRLS`` attribute (matching the
    single-role, FORCE-RLS-on-everything design in PROJECT_STATE.md
    section 3), so a ``DELETE`` here would hit the same unset-GUC error
    the repositories under test are meant to guard against, for no
    reason connected to what this fixture is trying to do.
    """
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    table_names = ", ".join(Base.metadata.tables.keys())
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
