"""Persistence models for the Scanning bounded context.

Owns: scans, scan scopes, workflow steps. Per PROJECT_STATE.md section 3,
the processing pipeline (``validate_target -> execute_scanner ->
normalize -> deduplicate -> correlate -> enrich -> ai_analyze ->
persist``) is implemented as explicit ``ScanWorkflowStep`` rows rather
than one monolithic task, giving resumability (retry from the failed
step) and per-step timing. ``Scan.status`` is itself derived from these
steps at the application layer (PROJECT_STATE.md section 5) -- this
module only provides the column to hold that derived value, not the
derivation logic, which belongs to the Scanning domain entities a later
milestone builds.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.infrastructure.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Scan(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One invocation of the scanning pipeline against a target."""

    __tablename__ = "scans"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    target: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ScanStatus] = mapped_column(
        SAEnum(ScanStatus, native_enum=False, validate_strings=True, length=16),
        default=ScanStatus.QUEUED,
        nullable=False,
    )
    triggered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow_steps: Mapped[list[ScanWorkflowStep]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )


class ScanScope(UUIDPrimaryKeyMixin, Base):
    """A registered target an organization is permitted to scan.
    PROJECT_STATE.md section 3: "the concept costs one table today;
    retrofitting authorization gating into every scan-trigger path later
    would cost more" -- the table exists now, enforcement against it is
    deferred code, not a schema gap.

    ``target_type`` is a plain string, not a ``StrEnum`` column: no enum
    for scan-scope target types has been approved yet (only ``AssetType``
    exists, and a CIDR range is not an asset type), and inventing one here
    would be a schema-level design decision this milestone is not
    authorized to make silently. Validate the value set in code once
    enforcement is built -- see PROJECT_STATE.md section 3 on target
    ownership.

    No ``updated_at``/``deleted_at``: PROJECT_STATE.md section 3 names the
    soft-delete list explicitly (orgs, users, assets, findings, scans,
    reports) and this table is not on it; a hard delete removes a scope
    grant outright, which matches "revoke permission to scan this," an
    action with no restore use case.
    """

    __tablename__ = "scan_scopes"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScanWorkflowStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One step of the processing pipeline for one scan -- independently
    retryable, per PROJECT_STATE.md section 5's domain-model summary for
    ``WorkflowStep``. ``organization_id`` is denormalized from the parent
    scan so RLS can filter this table directly.
    """

    __tablename__ = "scan_workflow_steps"
    __table_args__ = (UniqueConstraint("scan_id", "step_order"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[WorkflowStepName] = mapped_column(
        SAEnum(WorkflowStepName, native_enum=False, validate_strings=True, length=32),
        nullable=False,
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WorkflowStepStatus] = mapped_column(
        SAEnum(WorkflowStepStatus, native_enum=False, validate_strings=True, length=16),
        default=WorkflowStepStatus.PENDING,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="workflow_steps")
