"""``trigger_scan`` use case: records the intent to scan a target.

Deliberately does not itself call ``validate_target`` -- that is the
pipeline's own first step (``RunScanWorkflowUseCase``), and duplicating
it here would blur which of the two use cases is responsible for it.
This use case's only job is to create the ``Scan`` aggregate and its
eight ``ScanWorkflowStep`` rows, all ``PENDING``, in the locked pipeline
order (PROJECT_STATE.md section 3) -- nothing here executes anything.
"""

from __future__ import annotations

from uuid import UUID

from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.domain.scanning.entities import PIPELINE_STEP_ORDER, Scan, ScanWorkflowStep
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import ScanStatus, WorkflowStepStatus
from app.domain.shared.ids import new_id


class TriggerScanUseCase:
    def __init__(self, scan_repository: ScanRepositoryPort) -> None:
        self._scan_repository = scan_repository

    async def execute(
        self,
        *,
        organization_id: UUID,
        target: str,
        scanner_name: str,
        triggered_by_user_id: UUID | None = None,
    ) -> Scan:
        now = utcnow()
        scan = Scan(
            id=new_id(),
            organization_id=organization_id,
            target=target,
            scanner_name=scanner_name,
            status=ScanStatus.QUEUED,
            created_at=now,
            updated_at=now,
            triggered_by_user_id=triggered_by_user_id,
        )
        await self._scan_repository.add(scan)

        for order, step_name in enumerate(PIPELINE_STEP_ORDER):
            await self._scan_repository.add_workflow_step(
                ScanWorkflowStep(
                    id=new_id(),
                    organization_id=organization_id,
                    scan_id=scan.id,
                    step_name=step_name,
                    step_order=order,
                    status=WorkflowStepStatus.PENDING,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )

        return scan
