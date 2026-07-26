"""Async SQLAlchemy engine, session factory, and RLS tenant scoping.

Locked decisions this module implements (PROJECT_STATE.md sections 2-3):
  - SQLAlchemy async, against PostgreSQL.
  - Row-Level Security for tenant isolation from day one: every
    org-scoped table's RLS policy reads
    ``current_setting('app.current_org_id')`` (see the Alembic migration
    for the policies themselves). ``session_scoped_to_org`` below is the
    one function in the codebase that sets that session variable --
    repositories call it, nothing further upstream needs to know the GUC
    name exists.

Using ``SET LOCAL`` (not ``SET``) matters: it scopes the setting to the
current transaction and is automatically cleared on commit/rollback, so a
pooled connection can never leak one request's org context into the next
request that reuses it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine for a process that holds a database
    connection. Callers must have already confirmed ``settings.database_url``
    is not ``None`` -- ``Settings.check_role_boundaries`` (app/config.py)
    is what guarantees that for every role except scanner_worker, which
    must never call this function at all."""
    if settings.database_url is None:
        raise ValueError(
            "create_engine() requires settings.database_url; scanner_worker "
            "must never hold a database connection -- see docs/security_model.md"
        )
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to one engine. ``expire_on_commit=False``
    because repository methods routinely return mapped objects (or values
    read off them) after commit; the default would force an unwanted
    reload on next attribute access."""
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@asynccontextmanager
async def session_scoped_to_org(
    session_factory: async_sessionmaker[AsyncSession], organization_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Open a session whose entire unit of work runs with
    ``app.current_org_id`` set to ``organization_id`` for the duration of
    one transaction, so every RLS policy on every org-scoped table applies
    automatically -- callers never write ``WHERE organization_id = ...``
    themselves; the database enforces it.

    ``set_config(..., is_local=True)`` is the SQL-level equivalent of
    ``SET LOCAL`` usable as an ordinary function call (needed because
    parameterized ``SET LOCAL app.current_org_id = :value`` is not valid
    SQL -- ``SET`` does not accept bind parameters).
    """
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(organization_id)},
        )
        yield session
