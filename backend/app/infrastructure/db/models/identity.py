"""Persistence models for the Identity & Access bounded context.

Owns: organizations, users, memberships, OAuth account links, refresh
tokens, audit logs. Per PROJECT_STATE.md section 1, this context reads
from nothing else and nothing else owns its tables.

Soft delete (``SoftDeleteMixin``) is applied to ``organizations`` and
``users`` only -- exactly the two Identity tables named in the section 3
decision. ``organization_members`` uses its own lifecycle field
(``status``) instead, and ``oauth_accounts`` / ``refresh_tokens`` /
``audit_logs`` are excluded for the reasons given on each class below.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.infrastructure.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The tenant boundary. RLS on every other org-scoped table keys off
    this row's id via ``current_setting('app.current_org_id')`` -- see the
    Alembic migration for the policies themselves.

    The ">=1 Owner always; cannot remove the last one" invariant
    (PROJECT_STATE.md section 5) is an aggregate-level business rule
    enforced by application-layer use cases operating across
    ``OrganizationMember`` rows, not something a single-row CHECK
    constraint can express -- it is intentionally not encoded here.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An account holder. Deliberately not org-scoped (no
    ``organization_id`` column): one user can belong to several
    organizations via ``OrganizationMember``, so a user row is not tenant
    data in the RLS sense and carries no RLS policy.

    ``hashed_password`` is nullable because OAuth2 is designed
    (``OAuthAccount`` below) but its flows are deferred past Milestone 1
    (PROJECT_STATE.md section 3) -- a future OAuth-only account should not
    be blocked by a NOT NULL password column that predates that feature.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OrganizationMember(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """The membership join row. Uses ``status`` (invited/active/removed)
    as its lifecycle field instead of ``deleted_at`` -- explicitly listed
    in PROJECT_STATE.md section 3 as a table with "an existing lifecycle
    field" rather than soft delete, so it does not use
    ``SoftDeleteMixin``. RLS still applies via ``organization_id``.
    """

    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[OrganizationRole] = mapped_column(
        SAEnum(OrganizationRole, native_enum=False, validate_strings=True, length=16),
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        SAEnum(MembershipStatus, native_enum=False, validate_strings=True, length=16),
        default=MembershipStatus.INVITED,
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class OAuthAccount(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """Schema for a future external-identity link. Flows are deferred past
    Milestone 1 (PROJECT_STATE.md section 3); this table exists so that
    when OAuth2 login is built, it lands on an already-reviewed shape
    instead of a migration written under deadline pressure. No soft
    delete -- an unlinked OAuth account is simply deleted, there is no
    "restore a disconnected login method" use case to preserve a tombstone
    for.
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A JWT refresh token record. ``revoked_at`` is its own lifecycle
    field (PROJECT_STATE.md section 3), so -- like
    ``OrganizationMember`` -- this table does not use
    ``SoftDeleteMixin``. Only ``created_at`` is tracked, not
    ``updated_at``: revocation is the only mutation this row ever
    undergoes, and it has its own timestamp column already.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only, per PROJECT_STATE.md section 3's explicit list of
    tables excluded from soft delete. No ``updated_at`` either -- a log
    entry that could be edited after the fact would defeat its own
    purpose.

    ``organization_id`` is nullable to allow platform-level entries with
    no tenant context (e.g. a login attempt before an org is selected);
    the RLS policy on this table (see the migration) only ever exposes
    rows where ``organization_id`` matches the current session's org, so
    a nullable column here does not create a way for one tenant to see
    another's rows -- it only means platform-level rows are invisible
    under every tenant's RLS context, which is correct.
    """

    __tablename__ = "audit_logs"

    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    audit_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
