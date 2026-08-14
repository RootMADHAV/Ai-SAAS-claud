"""Domain entities for the Scanning bounded context.

See app/domain/identity/entities.py's module docstring for the scope
rationale shared by every entities.py added in this milestone.

``Scan.status`` "derived from its workflow steps" (PROJECT_STATE.md
section 5) is a real behavior, but the derivation rule itself was
deliberately deferred past Milestone 2 to whichever milestone actually
produces ``ScanWorkflowStep`` rows to derive a status from -- see
``derive_scan_status`` below, added in Milestone 4 (the processing
pipeline orchestrator), which is that milestone. ``Scan`` itself still
carries a plain ``status`` field, set by the orchestrator that calls
``derive_scan_status``, not computed as a property on this dataclass --
``Scan`` does not hold a live reference to its own steps, so a pure
function taking them as an explicit argument is the correct shape here,
not a method coupling this entity to a collection it doesn't own.
"""

from __future__ import annotations

from collections.abc import Sequence
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


# The locked processing pipeline (PROJECT_STATE.md section 3), as the
# concrete sequence of ``WorkflowStepName`` values a scan's
# ``ScanWorkflowStep`` rows are created in. A single source of truth for
# this order, rather than repeating the literal list at every call site
# that needs it (Milestone 4's ``TriggerScanUseCase`` to create the rows,
# ``RunScanWorkflowUseCase`` to walk them) -- both live in
# ``app/application/scanning/``, an outer layer relative to this module,
# so the order itself belongs here, at the domain layer, not duplicated
# in either of them.
PIPELINE_STEP_ORDER: tuple[WorkflowStepName, ...] = (
    WorkflowStepName.VALIDATE_TARGET,
    WorkflowStepName.EXECUTE_SCANNER,
    WorkflowStepName.NORMALIZE,
    WorkflowStepName.DEDUPLICATE,
    WorkflowStepName.CORRELATE,
    WorkflowStepName.ENRICH,
    WorkflowStepName.AI_ANALYZE,
    WorkflowStepName.PERSIST,
)


def derive_scan_status(steps: Sequence[ScanWorkflowStep]) -> ScanStatus:
    """The derivation rule named in PROJECT_STATE.md section 5: a scan's
    status is computed from its workflow steps, not tracked as
    independent state that could drift from them.

    Rules, in priority order:
      - No steps recorded yet -> ``QUEUED`` (defensive; in practice
        ``TriggerScanUseCase`` always creates all eight rows up front, so
        this should not occur once a scan exists).
      - Any step ``FAILED`` -> ``FAILED``, regardless of how many steps
        after it are still ``PENDING`` -- the pipeline stops at the first
        failure (PROJECT_STATE.md section 3's resumability rationale:
        retry from the failed step, not from scratch), so a later step
        never having run yet does not change the scan's overall outcome.
      - Every step ``COMPLETED`` or ``SKIPPED`` -> ``COMPLETED`` -- a
        ``SKIPPED`` step is a legitimate non-execution, not a failure,
        and does not prevent the scan as a whole from being considered
        done. As of Milestone 6, ``AnalysisService`` gives ``AI_ANALYZE``
        real work to do (see ``RunScanWorkflowUseCase._ai_analyze``), so
        no step in the pipeline's current code path produces ``SKIPPED``
        going forward -- but the value, and this rule's handling of it,
        remain correct for any historical scan rows created before this
        milestone, and for the enum value itself, which is not removed
        just because nothing currently produces it (see
        PROJECT_STATE.md section 3's Milestone 6 entry).
      - Otherwise (some ``PENDING``/``RUNNING``, none ``FAILED``) ->
        ``RUNNING``.
    """
    if not steps:
        return ScanStatus.QUEUED
    if any(step.status is WorkflowStepStatus.FAILED for step in steps):
        return ScanStatus.FAILED
    if all(
        step.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.SKIPPED)
        for step in steps
    ):
        return ScanStatus.COMPLETED
    return ScanStatus.RUNNING
