"""Declarative base and reusable column mixins for the persistence layer.

This module -- and everything under ``app/infrastructure/db/`` -- is the
one place in the codebase allowed to import SQLAlchemy. The domain layer
stays framework-free (see coding_standards in PROJECT_STATE.md section
10); ORM models are an infrastructure concern that maps to/from domain
entities at the repository boundary, not a domain concept itself.

Mixins here encode two locked decisions verbatim (PROJECT_STATE.md
section 3):
  - ULIDs generated in app code (``new_id``), stored in native
    Postgres ``UUID`` columns -- not ``ulid`` strings, not native
    ``ENUM`` types for status/type columns (those stay ``StrEnum`` +
    ``VARCHAR`` via ``native_enum=False``, applied per-column in the
    model modules, not here).
  - Soft delete (``deleted_at``) only where explicitly decided in
    PROJECT_STATE.md section 3. This module supplies the mixin but does
    NOT decide which tables use it -- that is a per-model choice made in
    ``models/*.py``, matching the explicit soft-delete list rather than
    applying it everywhere by default.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id


class Base(DeclarativeBase):
    """Shared declarative base. A single ``Base`` subclass is required so
    that ``Base.metadata`` reflects every mapped table for Alembic
    autogeneration -- every model module must import this same class,
    never define its own."""


class UUIDPrimaryKeyMixin:
    """A ULID-derived UUID primary key, generated client-side.

    Client-side (Python) generation, not a server default, is deliberate:
    it lets repository code know an entity's id immediately after
    construction, before any flush/commit, which several call sites need
    (e.g. emitting a domain event that references the new id in the same
    unit of work).
    """

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)


class TimestampMixin:
    """``created_at`` / ``updated_at``, both timezone-aware and set via the
    shared ``utcnow()`` clock (app/domain/shared/clock.py) rather than
    ``func.now()``, so application code and the database agree on what
    "now" means and a future deterministic-clock test can patch one
    function for both.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    """``deleted_at`` for current-state tables named explicitly in
    PROJECT_STATE.md section 3 (orgs, users, assets, findings, scans,
    reports) -- append-only tables and tables with their own lifecycle
    field (organization_members.status, refresh_tokens.revoked_at) do
    not use this mixin, by design, not by omission.

    Visibility filtering on this column happens in repository read
    methods (``WHERE deleted_at IS NULL``), not in a Row-Level Security
    policy: PostgreSQL checks a table's SELECT-relevant ``USING`` clause
    against the *new* row on every UPDATE, which makes combining
    ``deleted_at IS NULL`` into an RLS predicate unable to permit the
    very UPDATE that performs the soft delete. See the DESIGN NOTE in
    the initial-schema Alembic migration for the full account of why
    this differs from PROJECT_STATE.md's original phrasing. Tenant
    isolation itself (``organization_id``) is unaffected and still fully
    enforced by RLS.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
