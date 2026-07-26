"""SQLAlchemy implementation of the Scanning repository port."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.domain.scanning.entities import Scan, ScanScope, ScanWorkflowStep
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.infrastructure.db.models.scanning import (
    Scan as ScanRow,
)
from app.infrastructure.db.models.scanning import (
    ScanScope as ScanScopeRow,
)
from app.infrastructure.db.models.scanning import (
    ScanWorkflowStep as ScanWorkflowStepRow,
)


def _scan_to_domain(row: ScanRow) -> Scan:
    return Scan(
        id=row.id,
        organization_id=row.organization_id,
        target=row.target,
        scanner_name=row.scanner_name,
        status=ScanStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        triggered_by_user_id=row.triggered_by_user_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        deleted_at=row.deleted_at,
    )


def _workflow_step_to_domain(row: ScanWorkflowStepRow) -> ScanWorkflowStep:
    return ScanWorkflowStep(
        id=row.id,
        organization_id=row.organization_id,
        scan_id=row.scan_id,
        step_name=WorkflowStepName(row.step_name),
        step_order=row.step_order,
        status=WorkflowStepStatus(row.status),
        retry_count=row.retry_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_message=row.error_message,
    )


def _scope_to_domain(row: ScanScopeRow) -> ScanScope:
    return ScanScope(
        id=row.id,
        organization_id=row.organization_id,
        target_type=row.target_type,
        target_value=row.target_value,
        created_at=row.created_at,
    )


class SqlAlchemyScanRepository(ScanRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, scan_id: UUID) -> Scan | None:
        # Explicit filter, not session.get(): RLS enforces tenant
        # isolation on this table but deliberately does not filter
        # deleted_at (see the DESIGN NOTE in the initial-schema migration
        # for why that combination is unimplementable in Postgres RLS).
        result = await self._session.execute(
            select(ScanRow).where(ScanRow.id == scan_id, ScanRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _scan_to_domain(row) if row is not None else None

    async def add(self, scan: Scan) -> None:
        self._session.add(
            ScanRow(
                id=scan.id,
                organization_id=scan.organization_id,
                target=scan.target,
                scanner_name=scan.scanner_name,
                status=scan.status,
                triggered_by_user_id=scan.triggered_by_user_id,
                started_at=scan.started_at,
                completed_at=scan.completed_at,
                created_at=scan.created_at,
                updated_at=scan.updated_at,
                deleted_at=scan.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, scan: Scan) -> None:
        row = await self._session.get(ScanRow, scan.id)
        if row is None:
            raise LookupError(f"Scan {scan.id} does not exist")
        row.status = scan.status
        row.started_at = scan.started_at
        row.completed_at = scan.completed_at
        row.deleted_at = scan.deleted_at
        await self._session.flush()

    async def add_workflow_step(self, step: ScanWorkflowStep) -> None:
        self._session.add(
            ScanWorkflowStepRow(
                id=step.id,
                organization_id=step.organization_id,
                scan_id=step.scan_id,
                step_name=step.step_name,
                step_order=step.step_order,
                status=step.status,
                retry_count=step.retry_count,
                started_at=step.started_at,
                completed_at=step.completed_at,
                error_message=step.error_message,
                created_at=step.created_at,
                updated_at=step.updated_at,
            )
        )
        await self._session.flush()

    async def update_workflow_step(self, step: ScanWorkflowStep) -> None:
        row = await self._session.get(ScanWorkflowStepRow, step.id)
        if row is None:
            raise LookupError(f"ScanWorkflowStep {step.id} does not exist")
        row.status = step.status
        row.retry_count = step.retry_count
        row.started_at = step.started_at
        row.completed_at = step.completed_at
        row.error_message = step.error_message
        await self._session.flush()

    async def list_workflow_steps(self, scan_id: UUID) -> list[ScanWorkflowStep]:
        result = await self._session.execute(
            select(ScanWorkflowStepRow)
            .where(ScanWorkflowStepRow.scan_id == scan_id)
            .order_by(ScanWorkflowStepRow.step_order)
        )
        return [_workflow_step_to_domain(row) for row in result.scalars().all()]

    async def add_scope(self, scope: ScanScope) -> None:
        self._session.add(
            ScanScopeRow(
                id=scope.id,
                organization_id=scope.organization_id,
                target_type=scope.target_type,
                target_value=scope.target_value,
                created_at=scope.created_at,
            )
        )
        await self._session.flush()

    async def list_scopes(self, organization_id: UUID) -> list[ScanScope]:
        result = await self._session.execute(
            select(ScanScopeRow).where(ScanScopeRow.organization_id == organization_id)
        )
        return [_scope_to_domain(row) for row in result.scalars().all()]
