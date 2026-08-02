"""Unit tests for ``derive_scan_status`` (Milestone 4) --
app/domain/scanning/entities.py."""

from __future__ import annotations

from app.domain.scanning.entities import ScanWorkflowStep, derive_scan_status
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.domain.shared.ids import new_id


def _step(status: WorkflowStepStatus, *, order: int = 0) -> ScanWorkflowStep:
    now = utcnow()
    return ScanWorkflowStep(
        id=new_id(),
        organization_id=new_id(),
        scan_id=new_id(),
        step_name=list(WorkflowStepName)[order % len(WorkflowStepName)],
        step_order=order,
        status=status,
        retry_count=0,
        created_at=now,
        updated_at=now,
    )


def test_no_steps_is_queued() -> None:
    assert derive_scan_status([]) is ScanStatus.QUEUED


def test_all_pending_is_running() -> None:
    steps = [_step(WorkflowStepStatus.PENDING, order=i) for i in range(3)]
    assert derive_scan_status(steps) is ScanStatus.RUNNING


def test_mixed_completed_and_pending_is_running() -> None:
    steps = [
        _step(WorkflowStepStatus.COMPLETED, order=0),
        _step(WorkflowStepStatus.RUNNING, order=1),
        _step(WorkflowStepStatus.PENDING, order=2),
    ]
    assert derive_scan_status(steps) is ScanStatus.RUNNING


def test_any_failed_is_failed_even_with_later_pending_steps() -> None:
    steps = [
        _step(WorkflowStepStatus.COMPLETED, order=0),
        _step(WorkflowStepStatus.FAILED, order=1),
        _step(WorkflowStepStatus.PENDING, order=2),
    ]
    assert derive_scan_status(steps) is ScanStatus.FAILED


def test_all_completed_is_completed() -> None:
    steps = [_step(WorkflowStepStatus.COMPLETED, order=i) for i in range(3)]
    assert derive_scan_status(steps) is ScanStatus.COMPLETED


def test_completed_and_skipped_is_completed() -> None:
    """A SKIPPED step (Milestone 4: AI_ANALYZE, pending Milestone 6's
    AnalysisService) does not prevent the scan from being COMPLETED."""
    steps = [
        _step(WorkflowStepStatus.COMPLETED, order=0),
        _step(WorkflowStepStatus.SKIPPED, order=1),
        _step(WorkflowStepStatus.COMPLETED, order=2),
    ]
    assert derive_scan_status(steps) is ScanStatus.COMPLETED


def test_all_skipped_is_completed() -> None:
    steps = [_step(WorkflowStepStatus.SKIPPED, order=i) for i in range(2)]
    assert derive_scan_status(steps) is ScanStatus.COMPLETED
