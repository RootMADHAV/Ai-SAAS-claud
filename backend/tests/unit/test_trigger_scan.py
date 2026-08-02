"""Unit tests for app/application/scanning/trigger_scan.py (Milestone 4).

Uses a fake ``ScanRepositoryPort`` -- per PROJECT_STATE.md section 11,
fakes are for the consumers of a port; the real SQLAlchemy-backed
implementation already has its own integration coverage from Milestone 2
(and, for this use case, a fresh Milestone 4 integration test -- see
tests/integration/test_scan_pipeline_orchestrator.py).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.domain.scanning.entities import PIPELINE_STEP_ORDER, Scan, ScanScope, ScanWorkflowStep
from app.domain.shared.enums import ScanStatus, WorkflowStepStatus
from app.domain.shared.ids import new_id


class FakeScanRepository(ScanRepositoryPort):
    def __init__(self) -> None:
        self.scans: dict[UUID, Scan] = {}
        self.steps: dict[UUID, list[ScanWorkflowStep]] = {}

    async def get_by_id(self, scan_id: UUID) -> Scan | None:
        return self.scans.get(scan_id)

    async def add(self, scan: Scan) -> None:
        self.scans[scan.id] = scan
        self.steps[scan.id] = []

    async def update(self, scan: Scan) -> None:
        if scan.id not in self.scans:
            raise LookupError(f"Scan {scan.id} does not exist")
        self.scans[scan.id] = scan

    async def add_workflow_step(self, step: ScanWorkflowStep) -> None:
        self.steps.setdefault(step.scan_id, []).append(step)

    async def update_workflow_step(self, step: ScanWorkflowStep) -> None:
        steps = self.steps.get(step.scan_id, [])
        for i, existing in enumerate(steps):
            if existing.id == step.id:
                steps[i] = step
                return
        raise LookupError(f"ScanWorkflowStep {step.id} does not exist")

    async def list_workflow_steps(self, scan_id: UUID) -> list[ScanWorkflowStep]:
        return sorted(self.steps.get(scan_id, []), key=lambda s: s.step_order)

    async def add_scope(self, scope: ScanScope) -> None:
        raise NotImplementedError

    async def list_scopes(self, organization_id: UUID) -> list[ScanScope]:
        raise NotImplementedError


@pytest.fixture
def repository() -> FakeScanRepository:
    return FakeScanRepository()


async def test_creates_a_queued_scan(repository: FakeScanRepository) -> None:
    use_case = TriggerScanUseCase(repository)
    org_id = new_id()

    scan = await use_case.execute(
        organization_id=org_id, target="example.com", scanner_name="nuclei"
    )

    assert scan.organization_id == org_id
    assert scan.target == "example.com"
    assert scan.scanner_name == "nuclei"
    assert scan.status is ScanStatus.QUEUED
    assert scan.started_at is None
    assert scan.completed_at is None
    assert repository.scans[scan.id] == scan


async def test_creates_all_eight_steps_pending_in_locked_order(
    repository: FakeScanRepository,
) -> None:
    use_case = TriggerScanUseCase(repository)

    scan = await use_case.execute(
        organization_id=new_id(), target="example.com", scanner_name="nuclei"
    )

    steps = await repository.list_workflow_steps(scan.id)
    assert [step.step_name for step in steps] == list(PIPELINE_STEP_ORDER)
    assert [step.step_order for step in steps] == list(range(len(PIPELINE_STEP_ORDER)))
    assert all(step.status is WorkflowStepStatus.PENDING for step in steps)
    assert all(step.retry_count == 0 for step in steps)
    assert all(step.scan_id == scan.id for step in steps)
    assert all(step.organization_id == scan.organization_id for step in steps)


async def test_records_triggered_by_user(repository: FakeScanRepository) -> None:
    use_case = TriggerScanUseCase(repository)
    user_id = new_id()

    scan = await use_case.execute(
        organization_id=new_id(),
        target="example.com",
        scanner_name="nuclei",
        triggered_by_user_id=user_id,
    )

    assert scan.triggered_by_user_id == user_id


async def test_does_not_validate_the_target() -> None:
    """trigger_scan records intent only -- validation is the pipeline's
    own first step (RunScanWorkflowUseCase), not duplicated here."""
    repository = FakeScanRepository()
    use_case = TriggerScanUseCase(repository)

    # An obviously-unsafe target (loopback) must not raise here -- only
    # RunScanWorkflowUseCase's VALIDATE_TARGET step is responsible for
    # rejecting it.
    scan = await use_case.execute(
        organization_id=new_id(), target="127.0.0.1", scanner_name="nuclei"
    )

    assert scan.status is ScanStatus.QUEUED
