"""Persistence models for the Findings & Analysis bounded context.

Owns: findings, finding occurrences, finding analyses, finding status
history. Implements the findings-deduplication redesign in
PROJECT_STATE.md section 3 verbatim: ``findings`` is keyed by
``(organization_id, fingerprint)`` -- a recurrence updates the existing
row's ``last_seen_at`` instead of inserting a new one -- and
``FindingOccurrence`` (mirroring ``AssetObservation``) keeps the
append-only per-scan history underneath.

CVSS/Severity columns here store validated data; the actual validation
rules (score range, vector format) live in the ``CVSS``/``Severity``
value objects (app/domain/findings/value_objects.py) and are enforced at
the domain/application boundary before a repository ever receives a
value to persist -- this module does not re-implement that validation as
a CHECK constraint, since the value objects already make an invalid
instance unconstructable in the layer that creates one.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.shared.enums import ConfidenceLevel, FindingStatus, SeverityLevel
from app.infrastructure.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Finding(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Keyed by ``(organization_id, fingerprint)`` -- see the module
    docstring. ``ai_severity_level`` and ``cvss_score``/``cvss_vector`` are
    stored side by side deliberately: PROJECT_STATE.md section 5 states
    "effective_severity prefers CVSS over an AI estimate," which is a
    computed property on the domain entity, not a persisted column --
    persisting a pre-computed "effective" value here would let it drift
    from its inputs on the next CVSS update.
    """

    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("organization_id", "fingerprint"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[FindingStatus] = mapped_column(
        SAEnum(FindingStatus, native_enum=False, validate_strings=True, length=16),
        default=FindingStatus.NEW,
        nullable=False,
    )
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        SAEnum(ConfidenceLevel, native_enum=False, validate_strings=True, length=16),
        default=ConfidenceLevel.UNCONFIRMED,
        nullable=False,
    )
    ai_severity_level: Mapped[SeverityLevel | None] = mapped_column(
        SAEnum(SeverityLevel, native_enum=False, validate_strings=True, length=16), nullable=True
    )
    cvss_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvss_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    occurrences: Mapped[list[FindingOccurrence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )
    analyses: Mapped[list[FindingAnalysis]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )
    status_history: Mapped[list[FindingStatusHistory]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )


class FindingOccurrence(UUIDPrimaryKeyMixin, Base):
    """Append-only per-scan history, mirroring ``AssetObservation``. No
    direct FK to ``asset_observations`` -- PROJECT_STATE.md section 3
    states this is derivable via the shared ``scan_id``/``asset_id`` pair,
    which is why both are carried here directly rather than joined
    through the finding's own asset.
    """

    __tablename__ = "finding_occurrences"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    raw_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="occurrences")


class FindingAnalysis(UUIDPrimaryKeyMixin, Base):
    """Append-only, never overwritten (PROJECT_STATE.md section 3), so
    re-analysis history and "what changed" stay queryable.
    ``kb_version`` is nullable -- no RAG until Phase 5.
    """

    __tablename__ = "finding_analyses"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    kb_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_severity_estimate: Mapped[SeverityLevel | None] = mapped_column(
        SAEnum(SeverityLevel, native_enum=False, validate_strings=True, length=16), nullable=True
    )
    remediation_advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="analyses")


class FindingStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only triage-state audit trail, per PROJECT_STATE.md
    section 3's explicit exclusion of this table from soft delete.
    ``from_status`` is nullable to represent the finding's initial
    creation, which has no prior status to record.
    """

    __tablename__ = "finding_status_history"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[FindingStatus | None] = mapped_column(
        SAEnum(FindingStatus, native_enum=False, validate_strings=True, length=16), nullable=True
    )
    to_status: Mapped[FindingStatus] = mapped_column(
        SAEnum(FindingStatus, native_enum=False, validate_strings=True, length=16), nullable=False
    )
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="status_history")
