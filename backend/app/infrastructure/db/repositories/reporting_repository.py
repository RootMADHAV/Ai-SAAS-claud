"""SQLAlchemy implementation of the Reporting repository port."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.reporting_repository import ReportRepositoryPort
from app.domain.reporting.entities import Report
from app.domain.shared.enums import ReportFormat
from app.infrastructure.db.models.reporting import Report as ReportRow


def _report_to_domain(row: ReportRow) -> Report:
    return Report(
        id=row.id,
        organization_id=row.organization_id,
        format=ReportFormat(row.format),
        created_at=row.created_at,
        updated_at=row.updated_at,
        scan_id=row.scan_id,
        storage_key=row.storage_key,
        generated_at=row.generated_at,
        created_by_user_id=row.created_by_user_id,
        deleted_at=row.deleted_at,
    )


class SqlAlchemyReportRepository(ReportRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, report_id: UUID) -> Report | None:
        # Explicit filter, not session.get(): RLS enforces tenant
        # isolation on this table but deliberately does not filter
        # deleted_at (see the DESIGN NOTE in the initial-schema migration
        # for why that combination is unimplementable in Postgres RLS).
        result = await self._session.execute(
            select(ReportRow).where(ReportRow.id == report_id, ReportRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _report_to_domain(row) if row is not None else None

    async def add(self, report: Report) -> None:
        self._session.add(
            ReportRow(
                id=report.id,
                organization_id=report.organization_id,
                scan_id=report.scan_id,
                format=report.format,
                storage_key=report.storage_key,
                generated_at=report.generated_at,
                created_by_user_id=report.created_by_user_id,
                created_at=report.created_at,
                updated_at=report.updated_at,
                deleted_at=report.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, report: Report) -> None:
        row = await self._session.get(ReportRow, report.id)
        if row is None:
            raise LookupError(f"Report {report.id} does not exist")
        row.storage_key = report.storage_key
        row.generated_at = report.generated_at
        row.deleted_at = report.deleted_at
        await self._session.flush()

    async def list_by_scan(self, scan_id: UUID) -> list[Report]:
        result = await self._session.execute(
            select(ReportRow).where(ReportRow.scan_id == scan_id, ReportRow.deleted_at.is_(None))
        )
        return [_report_to_domain(row) for row in result.scalars().all()]
