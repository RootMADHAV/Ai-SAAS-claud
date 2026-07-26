"""Tests for the ``LookupError`` raised by every repository's ``update()``
method when the target row does not exist -- each repository's write path
fails loudly rather than silently no-op'ing on a stale or fabricated id.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.entities import OrganizationMember
from app.domain.reporting.entities import Report
from app.domain.scanning.entities import ScanWorkflowStep
from app.domain.shared.enums import (
    MembershipStatus,
    OrganizationRole,
    ReportFormat,
    WorkflowStepName,
    WorkflowStepStatus,
)
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.reporting_repository import SqlAlchemyReportRepository
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import (
    make_asset,
    make_finding,
    make_organization,
    make_scan,
    make_user,
    set_org_context,
)

pytestmark = pytest.mark.integration


async def test_organization_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    with pytest.raises(LookupError, match=str(org.id)):
        await SqlAlchemyOrganizationRepository(db_session).update(org)


async def test_organization_soft_delete_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    with pytest.raises(LookupError, match=str(org.id)):
        await SqlAlchemyOrganizationRepository(db_session).soft_delete(org.id)


async def test_organization_member_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    await org_repo.add(org)
    user = make_user()
    await SqlAlchemyUserRepository(db_session).add(user)

    phantom_member = OrganizationMember(
        id=new_id(),
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.MEMBER,
        status=MembershipStatus.ACTIVE,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )
    with pytest.raises(LookupError):
        await org_repo.update_member(phantom_member)


async def test_user_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    user = make_user()
    with pytest.raises(LookupError, match=str(user.id)):
        await SqlAlchemyUserRepository(db_session).update(user)


async def test_user_soft_delete_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    user = make_user()
    with pytest.raises(LookupError, match=str(user.id)):
        await SqlAlchemyUserRepository(db_session).soft_delete(user.id)


async def test_asset_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org)
    asset = make_asset(org.id)
    with pytest.raises(LookupError, match=str(asset.id)):
        await SqlAlchemyAssetRepository(db_session).update(asset)


async def test_scan_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org)
    scan = make_scan(org.id)
    with pytest.raises(LookupError, match=str(scan.id)):
        await SqlAlchemyScanRepository(db_session).update(scan)


async def test_workflow_step_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org)
    scan = make_scan(org.id)
    scan_repo = SqlAlchemyScanRepository(db_session)
    await scan_repo.add(scan)

    phantom_step = ScanWorkflowStep(
        id=new_id(),
        organization_id=org.id,
        scan_id=scan.id,
        step_name=WorkflowStepName.NORMALIZE,
        step_order=0,
        status=WorkflowStepStatus.PENDING,
        retry_count=0,
        created_at=scan.created_at,
        updated_at=scan.updated_at,
    )
    with pytest.raises(LookupError):
        await scan_repo.update_workflow_step(phantom_step)


async def test_finding_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org)
    asset = make_asset(org.id)
    await SqlAlchemyAssetRepository(db_session).add(asset)
    finding = make_finding(org.id, asset.id)
    with pytest.raises(LookupError, match=str(finding.id)):
        await SqlAlchemyFindingRepository(db_session).update(finding)


async def test_report_update_raises_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org)
    report = Report(
        id=new_id(),
        organization_id=org.id,
        format=ReportFormat.PDF,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )
    with pytest.raises(LookupError, match=str(report.id)):
        await SqlAlchemyReportRepository(db_session).update(report)
