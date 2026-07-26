"""Integration tests for the Scanning repository implementation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.entities import Organization
from app.domain.scanning.entities import ScanScope, ScanWorkflowStep
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import make_organization, make_scan, set_org_context

pytestmark = pytest.mark.integration


async def _seed_org(session: AsyncSession) -> Organization:
    org = make_organization()
    await set_org_context(session, org.id)
    await SqlAlchemyOrganizationRepository(session).add(org)
    return org


async def test_add_get_and_update_scan(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    scan = make_scan(org.id)
    repo = SqlAlchemyScanRepository(db_session)

    await repo.add(scan)
    fetched = await repo.get_by_id(scan.id)
    assert fetched is not None
    assert fetched.status is ScanStatus.QUEUED

    fetched.status = ScanStatus.RUNNING
    fetched.started_at = fetched.created_at
    await repo.update(fetched)

    reloaded = await repo.get_by_id(scan.id)
    assert reloaded is not None
    assert reloaded.status is ScanStatus.RUNNING
    assert reloaded.started_at is not None


async def test_workflow_steps_are_listed_in_step_order(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    scan = make_scan(org.id)
    scan_repo = SqlAlchemyScanRepository(db_session)
    await scan_repo.add(scan)

    step_names = [
        WorkflowStepName.VALIDATE_TARGET,
        WorkflowStepName.EXECUTE_SCANNER,
        WorkflowStepName.NORMALIZE,
    ]
    for order, name in enumerate(step_names):
        step = ScanWorkflowStep(
            id=new_id(),
            organization_id=org.id,
            scan_id=scan.id,
            step_name=name,
            step_order=order,
            status=WorkflowStepStatus.PENDING,
            retry_count=0,
            created_at=scan.created_at,
            updated_at=scan.updated_at,
        )
        await scan_repo.add_workflow_step(step)

    steps = await scan_repo.list_workflow_steps(scan.id)
    assert [s.step_name for s in steps] == step_names
    assert [s.step_order for s in steps] == [0, 1, 2]


async def test_workflow_step_retry_is_persisted(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    scan = make_scan(org.id)
    scan_repo = SqlAlchemyScanRepository(db_session)
    await scan_repo.add(scan)

    step = ScanWorkflowStep(
        id=new_id(),
        organization_id=org.id,
        scan_id=scan.id,
        step_name=WorkflowStepName.EXECUTE_SCANNER,
        step_order=0,
        status=WorkflowStepStatus.FAILED,
        retry_count=0,
        created_at=scan.created_at,
        updated_at=scan.updated_at,
        error_message="connection timed out",
    )
    await scan_repo.add_workflow_step(step)

    step.status = WorkflowStepStatus.RUNNING
    step.retry_count = 1
    step.error_message = None
    await scan_repo.update_workflow_step(step)

    (reloaded,) = await scan_repo.list_workflow_steps(scan.id)
    assert reloaded.status is WorkflowStepStatus.RUNNING
    assert reloaded.retry_count == 1
    assert reloaded.error_message is None


async def test_scan_scopes_add_and_list(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    repo = SqlAlchemyScanRepository(db_session)
    scope = ScanScope(
        id=new_id(),
        organization_id=org.id,
        target_type="domain",
        target_value="*.example.com",
        created_at=org.created_at,
    )
    await repo.add_scope(scope)

    scopes = await repo.list_scopes(org.id)
    assert [s.target_value for s in scopes] == ["*.example.com"]
