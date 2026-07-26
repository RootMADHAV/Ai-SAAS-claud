"""SQLAlchemy implementation of the Findings & Analysis repository port."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.findings_repository import FindingRepositoryPort
from app.domain.findings.entities import (
    Finding,
    FindingAnalysis,
    FindingOccurrence,
    FindingStatusHistory,
)
from app.domain.shared.enums import ConfidenceLevel, FindingStatus, SeverityLevel
from app.infrastructure.db.models.findings import (
    Finding as FindingRow,
)
from app.infrastructure.db.models.findings import (
    FindingAnalysis as FindingAnalysisRow,
)
from app.infrastructure.db.models.findings import (
    FindingOccurrence as FindingOccurrenceRow,
)
from app.infrastructure.db.models.findings import (
    FindingStatusHistory as FindingStatusHistoryRow,
)


def _finding_to_domain(row: FindingRow) -> Finding:
    return Finding(
        id=row.id,
        organization_id=row.organization_id,
        asset_id=row.asset_id,
        fingerprint=row.fingerprint,
        title=row.title,
        status=FindingStatus(row.status),
        confidence=ConfidenceLevel(row.confidence),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        ai_severity_level=SeverityLevel(row.ai_severity_level)
        if row.ai_severity_level is not None
        else None,
        cvss_score=row.cvss_score,
        cvss_vector=row.cvss_vector,
        deleted_at=row.deleted_at,
    )


def _occurrence_to_domain(row: FindingOccurrenceRow) -> FindingOccurrence:
    return FindingOccurrence(
        id=row.id,
        organization_id=row.organization_id,
        finding_id=row.finding_id,
        scan_id=row.scan_id,
        asset_id=row.asset_id,
        detected_at=row.detected_at,
        created_at=row.created_at,
        raw_evidence=row.raw_evidence,
    )


def _analysis_to_domain(row: FindingAnalysisRow) -> FindingAnalysis:
    return FindingAnalysis(
        id=row.id,
        organization_id=row.organization_id,
        finding_id=row.finding_id,
        prompt_version=row.prompt_version,
        created_at=row.created_at,
        kb_version=row.kb_version,
        model_metadata=row.model_metadata,
        ai_summary=row.ai_summary,
        ai_severity_estimate=SeverityLevel(row.ai_severity_estimate)
        if row.ai_severity_estimate is not None
        else None,
        remediation_advice=row.remediation_advice,
    )


def _status_history_to_domain(row: FindingStatusHistoryRow) -> FindingStatusHistory:
    return FindingStatusHistory(
        id=row.id,
        organization_id=row.organization_id,
        finding_id=row.finding_id,
        to_status=FindingStatus(row.to_status),
        created_at=row.created_at,
        from_status=FindingStatus(row.from_status) if row.from_status is not None else None,
        changed_by_user_id=row.changed_by_user_id,
        reason=row.reason,
    )


class SqlAlchemyFindingRepository(FindingRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, finding_id: UUID) -> Finding | None:
        # Explicit filter, not session.get(): RLS enforces tenant
        # isolation on this table but deliberately does not filter
        # deleted_at (see the DESIGN NOTE in the initial-schema migration
        # for why that combination is unimplementable in Postgres RLS).
        result = await self._session.execute(
            select(FindingRow).where(FindingRow.id == finding_id, FindingRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _finding_to_domain(row) if row is not None else None

    async def get_by_fingerprint(self, organization_id: UUID, fingerprint: str) -> Finding | None:
        result = await self._session.execute(
            select(FindingRow).where(
                FindingRow.organization_id == organization_id,
                FindingRow.fingerprint == fingerprint,
                FindingRow.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return _finding_to_domain(row) if row is not None else None

    async def add(self, finding: Finding) -> None:
        self._session.add(
            FindingRow(
                id=finding.id,
                organization_id=finding.organization_id,
                asset_id=finding.asset_id,
                fingerprint=finding.fingerprint,
                title=finding.title,
                description=finding.description,
                status=finding.status,
                confidence=finding.confidence,
                ai_severity_level=finding.ai_severity_level,
                cvss_score=finding.cvss_score,
                cvss_vector=finding.cvss_vector,
                first_seen_at=finding.first_seen_at,
                last_seen_at=finding.last_seen_at,
                created_at=finding.created_at,
                updated_at=finding.updated_at,
                deleted_at=finding.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, finding: Finding) -> None:
        row = await self._session.get(FindingRow, finding.id)
        if row is None:
            raise LookupError(f"Finding {finding.id} does not exist")
        row.title = finding.title
        row.description = finding.description
        row.status = finding.status
        row.confidence = finding.confidence
        row.ai_severity_level = finding.ai_severity_level
        row.cvss_score = finding.cvss_score
        row.cvss_vector = finding.cvss_vector
        row.last_seen_at = finding.last_seen_at
        row.deleted_at = finding.deleted_at
        await self._session.flush()

    async def add_occurrence(self, occurrence: FindingOccurrence) -> None:
        self._session.add(
            FindingOccurrenceRow(
                id=occurrence.id,
                organization_id=occurrence.organization_id,
                finding_id=occurrence.finding_id,
                scan_id=occurrence.scan_id,
                asset_id=occurrence.asset_id,
                raw_evidence=occurrence.raw_evidence,
                detected_at=occurrence.detected_at,
                created_at=occurrence.created_at,
            )
        )
        await self._session.flush()

    async def list_occurrences(self, finding_id: UUID) -> list[FindingOccurrence]:
        result = await self._session.execute(
            select(FindingOccurrenceRow)
            .where(FindingOccurrenceRow.finding_id == finding_id)
            .order_by(FindingOccurrenceRow.detected_at)
        )
        return [_occurrence_to_domain(row) for row in result.scalars().all()]

    async def add_analysis(self, analysis: FindingAnalysis) -> None:
        self._session.add(
            FindingAnalysisRow(
                id=analysis.id,
                organization_id=analysis.organization_id,
                finding_id=analysis.finding_id,
                prompt_version=analysis.prompt_version,
                kb_version=analysis.kb_version,
                model_metadata=analysis.model_metadata,
                ai_summary=analysis.ai_summary,
                ai_severity_estimate=analysis.ai_severity_estimate,
                remediation_advice=analysis.remediation_advice,
                created_at=analysis.created_at,
            )
        )
        await self._session.flush()

    async def list_analyses(self, finding_id: UUID) -> list[FindingAnalysis]:
        result = await self._session.execute(
            select(FindingAnalysisRow)
            .where(FindingAnalysisRow.finding_id == finding_id)
            .order_by(FindingAnalysisRow.created_at)
        )
        return [_analysis_to_domain(row) for row in result.scalars().all()]

    async def add_status_history(self, entry: FindingStatusHistory) -> None:
        self._session.add(
            FindingStatusHistoryRow(
                id=entry.id,
                organization_id=entry.organization_id,
                finding_id=entry.finding_id,
                from_status=entry.from_status,
                to_status=entry.to_status,
                changed_by_user_id=entry.changed_by_user_id,
                reason=entry.reason,
                created_at=entry.created_at,
            )
        )
        await self._session.flush()

    async def list_status_history(self, finding_id: UUID) -> list[FindingStatusHistory]:
        result = await self._session.execute(
            select(FindingStatusHistoryRow)
            .where(FindingStatusHistoryRow.finding_id == finding_id)
            .order_by(FindingStatusHistoryRow.created_at)
        )
        return [_status_history_to_domain(row) for row in result.scalars().all()]
