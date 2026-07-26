"""Domain entity for the Reporting bounded context.

See app/domain/identity/entities.py's module docstring for the shared
scope rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.shared.enums import ReportFormat


@dataclass(slots=True)
class Report:
    """A generated report artifact. ``storage_key`` references the
    StoragePort (MinIO) object holding the rendered file -- this entity
    tracks metadata about that artifact, not its bytes."""

    id: UUID
    organization_id: UUID
    format: ReportFormat
    created_at: datetime
    updated_at: datetime
    scan_id: UUID | None = None
    storage_key: str | None = None
    generated_at: datetime | None = None
    created_by_user_id: UUID | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
