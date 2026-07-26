"""Persistence models for cross-cutting platform configuration.

``SystemSetting`` and ``FeatureFlag`` (PROJECT_STATE.md section 3) do not
belong to any of the six bounded contexts in section 1 -- they are
platform configuration, not tenant data or a domain concept any context
owns, which is why they live in their own module rather than being
folded into ``identity.py`` for lack of a better home.

Both are described as "Redis-cached since read-heavy and write-rare" in
the decision log; the cache itself is an infrastructure/event_bus-adjacent
concern for a later milestone -- these are just the tables it would sit
in front of.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.shared.clock import utcnow
from app.infrastructure.db.base import Base, UUIDPrimaryKeyMixin


class SystemSetting(Base):
    """A system-global key/value setting. Not org-scoped -- no RLS policy
    applies to this table; access is gated at the application/API layer
    (admin-only), not the database layer, since there is no tenant
    dimension here to key an RLS predicate on.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class FeatureFlag(UUIDPrimaryKeyMixin, Base):
    """A feature flag with an optional per-organization override.
    ``organization_id IS NULL`` is the global default row for a given
    key; a non-null row overrides it for that org.

    Postgres treats NULL as distinct in a plain UNIQUE constraint, so
    ``UniqueConstraint("key", "organization_id")`` alone would allow more
    than one global (``organization_id IS NULL``) row per key. The
    partial unique index below closes that gap for the global case; the
    plain constraint continues to cover the per-org case, where
    ``organization_id`` is never NULL.
    """

    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("key", "organization_id", name="uq_feature_flags_key_org"),
        Index(
            "uq_feature_flags_key_global",
            "key",
            unique=True,
            postgresql_where="organization_id IS NULL",
        ),
    )

    key: Mapped[str] = mapped_column(Text, nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
