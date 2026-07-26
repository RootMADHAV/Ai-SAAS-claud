"""Domain entities for the Scanning bounded context.

See app/domain/identity/entities.py's module docstring for the scope
rationale shared by every entities.py added in this milestone.

``Scan.status`` "derived from its workflow steps" (PROJECT_STATE.md
section 5) is a real behavior, but the derivation rule itself belongs to
the Scanning bounded context's own milestone (Milestone 3: "Scanner
engine + StoragePort + target validation"), where the ``ScannerPort``
this derivation depends on is actually built. Encoding a derivation
method against workflow steps here, before that context exists, would be
guessing at a rule this milestone has no way to verify against real
adapter behavior -- so ``Scan`` carries a plain ``status`` field for now,
set by whichever later milestone computes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus


@dataclass(slots=True)
class Scan:
    """One invocation of the scanning pipeline against a target."""

    id: UUID
    organization_id: UUID
    target: str
    scanner_name: str
    status: ScanStatus
    created_at: datetime
    updated_at: datetime
    triggered_by_user_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class ScanWorkflowStep:
    """One independently retryable step of the processing pipeline for
    one scan, per PROJECT_STATE.md section 5."""

    id: UUID
    organization_id: UUID
    scan_id: UUID
    step_name: WorkflowStepName
    step_order: int
    status: WorkflowStepStatus
    retry_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


@dataclass(slots=True)
class ScanScope:
    """A registered target an organization is permitted to scan.
    Enforcement against this table is deferred (PROJECT_STATE.md
    section 3); this entity is a plain data carrier for the registry,
    not an authorization check."""

    id: UUID
    organization_id: UUID
    target_type: str
    target_value: str
    created_at: datetime
