"""Persistence model for the Reporting bounded context.

Owns: reports. Reads from Findings and Scanning; per PROJECT_STATE.md
section 1, does not own findings themselves -- a report references a
scan and is rendered from that scan's findings by application-layer code,
not by a foreign key to individual findings (a report is a rendered
snapshot/export, not a relational join target).

``scan_id`` is nullable to leave room for a future multi-scan report
without a schema change -- Phase 2's MVP vertical slice
("Nuclei -> AI analysis -> report") is per-scan, but the column should
not force every future report to be.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.shared.enums import ReportFormat
from app.infrastructure.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Report(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A generated report artifact. ``storage_key`` is the StoragePort
    (MinIO) object key for the rendered file -- this table tracks
    metadata about the artifact, not its bytes, matching the existing
    StoragePort abstraction rather than inventing a second way to
    reference stored files.
    """

    __tablename__ = "reports"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    format: Mapped[ReportFormat] = mapped_column(
        SAEnum(ReportFormat, native_enum=False, validate_strings=True, length=16), nullable=False
    )
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
