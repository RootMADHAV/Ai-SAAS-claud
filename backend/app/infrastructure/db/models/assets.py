"""Persistence models for the Asset Intelligence bounded context.

Owns: assets, asset observations, asset relationships. Per
PROJECT_STATE.md section 3: ``Asset`` is a current-state cache (identity =
``(org_id, asset_type, normalized value)``); ``AssetObservation`` is the
append-only history; ``metadata``/``confidence`` on ``Asset`` are a
materialized view of the latest observation -- small write-time
duplication for cheap reads on the common case, kept in sync by
application-layer code when an observation is recorded, not by a database
trigger (no trigger-based logic exists anywhere else in this schema, and
introducing the first one here for a single field would be inconsistent
with how every other derived value in this codebase is computed: in
application code, not in the database).

No graph database: ``AssetRelationship`` (many-to-many, typed) and
``Asset.parent_asset_id`` (simple hierarchy) cover relationship modeling
in plain Postgres, per the section 3 decision.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.shared.enums import AssetStatus, AssetType
from app.infrastructure.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Current-state cache. Identity is ``(organization_id, asset_type,
    value)`` -- the unique constraint below, not the surrogate ``id``, is
    what a re-observation of the same asset actually matches against.
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("organization_id", "asset_type", "value"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    asset_type: Mapped[AssetType] = mapped_column(
        SAEnum(AssetType, native_enum=False, validate_strings=True, length=16), nullable=False
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AssetStatus] = mapped_column(
        SAEnum(AssetStatus, native_enum=False, validate_strings=True, length=16),
        default=AssetStatus.ACTIVE,
        nullable=False,
    )
    asset_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    parent_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    observations: Mapped[list[AssetObservation]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        foreign_keys="AssetObservation.asset_id",
    )


class AssetObservation(UUIDPrimaryKeyMixin, Base):
    """Append-only per-scan sighting history -- no soft delete, no
    ``updated_at``, per the explicit exclusion list in PROJECT_STATE.md
    section 3. ``organization_id`` is denormalized from the parent asset
    so RLS can filter this table directly without a join.

    No direct ``finding -> asset_observation`` foreign key anywhere in
    this schema: PROJECT_STATE.md section 3 states it is derivable via
    the shared ``scan_id`` + ``asset_id`` pair instead, which is why
    ``FindingOccurrence`` (findings.py) carries its own ``scan_id`` and
    ``asset_id`` rather than referencing this table.
    """

    __tablename__ = "asset_observations"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    observation_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="observations", foreign_keys=[asset_id])


class AssetRelationship(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A typed edge between two assets (e.g. "resolves_to", "hosts",
    "part_of") -- the many-to-many half of relationship modeling; see
    ``Asset.parent_asset_id`` for the simple-hierarchy half. Not soft
    deleted: PROJECT_STATE.md section 3's soft-delete list names
    ``assets`` itself, not this derived edge table, and a stale edge
    between two still-soft-deleted assets carries no independent meaning
    worth preserving as a tombstone.
    """

    __tablename__ = "asset_relationships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "source_asset_id", "target_asset_id", "relationship_type"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    source_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    target_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
