"""Pydantic request/response schemas for the public Scanning API
(``/api/v1/organizations/{organization_id}/scans/...``).

These are pure serialization boundaries between the domain layer and
JSON -- no validation here duplicates a business rule that already lives
in a domain entity or use case (PROJECT_STATE.md section 10: "no
business logic inside FastAPI route handlers"). ``scanner_name`` is
constrained to the literal set of adapters this process actually wires
(today: just ``"nuclei"``, per Milestone 3) so a request naming an
adapter that doesn't exist fails fast at the request-validation boundary
(422) rather than creating a ``Scan`` row that
``RunScanWorkflowUseCase`` could never execute (it would raise
``ScannerMismatchError`` -- see run_scan_workflow.py). This is *not* a
scanner registry (still exactly one adapter, per Milestone 4's decision
log): it is Pydantic reflecting the one adapter this deployment happens
to have wired, in one place, rather than letting an unrunnable ``Scan``
row get created first and fail later.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.scanning.entities import Scan, ScanWorkflowStep
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus


class ScanCreateRequest(BaseModel):
    """Request body for ``POST /organizations/{organization_id}/scans``."""

    target: str = Field(
        min_length=1,
        max_length=253,  # the maximum length of a valid DNS name -- basic
        # input hygiene, not a re-implementation of the real SSRF/DNS
        # validation that already happens inside the pipeline's own
        # validate_target step (app/infrastructure/security/
        # target_validation.py). That check runs regardless of what
        # passes this schema and is the actual authority on whether a
        # target is safe to scan.
    )
    scanner_name: Literal["nuclei"] = "nuclei"


class WorkflowStepResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    step_name: WorkflowStepName
    step_order: int
    status: WorkflowStepStatus
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    @classmethod
    def from_domain(cls, step: ScanWorkflowStep) -> WorkflowStepResponse:
        return cls(
            id=step.id,
            step_name=step.step_name,
            step_order=step.step_order,
            status=step.status,
            retry_count=step.retry_count,
            started_at=step.started_at,
            completed_at=step.completed_at,
            error_message=step.error_message,
        )


class ScanDetailResponse(BaseModel):
    """Returned by every scan endpoint (create, run, get) so a client
    always sees the same shape regardless of which action it just took --
    including the current workflow-step breakdown, which is how a client
    observes this pipeline's resumability (PROJECT_STATE.md section 3):
    a scan that failed partway can be resumed by calling ``run`` again,
    and this response is how a client would notice which step to expect
    progress on next.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    organization_id: UUID
    target: str
    scanner_name: str
    status: ScanStatus
    created_at: datetime
    updated_at: datetime
    triggered_by_user_id: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    workflow_steps: list[WorkflowStepResponse]

    @classmethod
    def from_domain(cls, scan: Scan, steps: Sequence[ScanWorkflowStep]) -> ScanDetailResponse:
        return cls(
            id=scan.id,
            organization_id=scan.organization_id,
            target=scan.target,
            scanner_name=scan.scanner_name,
            status=scan.status,
            created_at=scan.created_at,
            updated_at=scan.updated_at,
            triggered_by_user_id=scan.triggered_by_user_id,
            started_at=scan.started_at,
            completed_at=scan.completed_at,
            workflow_steps=[WorkflowStepResponse.from_domain(s) for s in steps],
        )
