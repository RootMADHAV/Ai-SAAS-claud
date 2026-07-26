"""Integration tests for the Reporting repository implementation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.entities import Organization
from app.domain.reporting.entities import Report
from app.domain.scanning.entities import Scan
from app.domain.shared.enums import ReportFormat
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.reporting_repository import SqlAlchemyReportRepository
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import make_organization, make_scan, set_org_context

pytestmark = pytest.mark.integration


async def _seed_org_and_scan(session: AsyncSession) -> tuple[Organization, Scan]:
    org = make_organization()
    await set_org_context(session, org.id)
    await SqlAlchemyOrganizationRepository(session).add(org)
    scan = make_scan(org.id)
    await SqlAlchemyScanRepository(session).add(scan)
    return org, scan


async def test_add_get_and_update_report(db_session: AsyncSession) -> None:
    org, scan = await _seed_org_and_scan(db_session)
    report = Report(
        id=new_id(),
        organization_id=org.id,
        format=ReportFormat.PDF,
        created_at=org.created_at,
        updated_at=org.updated_at,
        scan_id=scan.id,
    )
    repo = SqlAlchemyReportRepository(db_session)

    await repo.add(report)
    fetched = await repo.get_by_id(report.id)
    assert fetched is not None
    assert fetched.generated_at is None
    assert fetched.storage_key is None

    fetched.storage_key = "reports/example.pdf"
    fetched.generated_at = org.created_at
    await repo.update(fetched)

    reloaded = await repo.get_by_id(report.id)
    assert reloaded is not None
    assert reloaded.storage_key == "reports/example.pdf"
    assert reloaded.generated_at is not None


async def test_list_by_scan(db_session: AsyncSession) -> None:
    org, scan = await _seed_org_and_scan(db_session)
    repo = SqlAlchemyReportRepository(db_session)
    report = Report(
        id=new_id(),
        organization_id=org.id,
        format=ReportFormat.HTML,
        created_at=org.created_at,
        updated_at=org.updated_at,
        scan_id=scan.id,
    )
    await repo.add(report)

    reports = await repo.list_by_scan(scan.id)
    assert [r.id for r in reports] == [report.id]
