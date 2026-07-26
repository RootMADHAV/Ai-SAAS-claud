"""Integration tests for the Findings & Analysis repository implementation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.assets.entities import Asset
from app.domain.findings.entities import FindingAnalysis, FindingOccurrence, FindingStatusHistory
from app.domain.identity.entities import Organization
from app.domain.scanning.entities import Scan
from app.domain.shared.enums import FindingStatus, SeverityLevel
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import (
    make_asset,
    make_finding,
    make_organization,
    make_scan,
    set_org_context,
)

pytestmark = pytest.mark.integration


async def _seed_org_asset_scan(session: AsyncSession) -> tuple[Organization, Asset, Scan]:
    org = make_organization()
    await set_org_context(session, org.id)
    await SqlAlchemyOrganizationRepository(session).add(org)
    asset = make_asset(org.id)
    await SqlAlchemyAssetRepository(session).add(asset)
    scan = make_scan(org.id)
    await SqlAlchemyScanRepository(session).add(scan)
    return org, asset, scan


async def test_add_and_get_finding(db_session: AsyncSession) -> None:
    org, asset, _scan = await _seed_org_asset_scan(db_session)
    finding = make_finding(org.id, asset.id, title="Reflected XSS")
    repo = SqlAlchemyFindingRepository(db_session)

    await repo.add(finding)
    fetched = await repo.get_by_id(finding.id)

    assert fetched is not None
    assert fetched.title == "Reflected XSS"
    assert fetched.status is FindingStatus.NEW
    assert fetched.effective_severity is None  # no CVSS or AI estimate yet


async def test_get_by_fingerprint_is_the_deduplication_lookup(db_session: AsyncSession) -> None:
    org, asset, _scan = await _seed_org_asset_scan(db_session)
    finding = make_finding(org.id, asset.id, fingerprint="stable-fingerprint-value")
    repo = SqlAlchemyFindingRepository(db_session)
    await repo.add(finding)

    fetched = await repo.get_by_fingerprint(org.id, "stable-fingerprint-value")
    assert fetched is not None
    assert fetched.id == finding.id

    assert await repo.get_by_fingerprint(org.id, "no-such-fingerprint") is None


async def test_update_bumps_last_seen_at_on_recurrence(db_session: AsyncSession) -> None:
    org, asset, _scan = await _seed_org_asset_scan(db_session)
    finding = make_finding(org.id, asset.id)
    repo = SqlAlchemyFindingRepository(db_session)
    await repo.add(finding)

    later = finding.last_seen_at
    finding.last_seen_at = later
    finding.cvss_score = 9.8
    finding.cvss_vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    await repo.update(finding)

    reloaded = await repo.get_by_id(finding.id)
    assert reloaded is not None
    assert reloaded.cvss is not None
    assert reloaded.effective_severity is not None
    assert reloaded.effective_severity.level is SeverityLevel.CRITICAL


async def test_occurrence_analysis_and_status_history_are_append_only(
    db_session: AsyncSession,
) -> None:
    org, asset, scan = await _seed_org_asset_scan(db_session)
    finding = make_finding(org.id, asset.id)
    repo = SqlAlchemyFindingRepository(db_session)
    await repo.add(finding)

    occurrence = FindingOccurrence(
        id=new_id(),
        organization_id=org.id,
        finding_id=finding.id,
        scan_id=scan.id,
        asset_id=asset.id,
        detected_at=finding.first_seen_at,
        created_at=finding.first_seen_at,
        raw_evidence={"template": "cves/2024/example.yaml"},
    )
    await repo.add_occurrence(occurrence)

    analysis = FindingAnalysis(
        id=new_id(),
        organization_id=org.id,
        finding_id=finding.id,
        prompt_version="v1",
        created_at=finding.first_seen_at,
        ai_summary="Looks exploitable.",
        ai_severity_estimate=SeverityLevel.HIGH,
    )
    await repo.add_analysis(analysis)

    history_entry = FindingStatusHistory(
        id=new_id(),
        organization_id=org.id,
        finding_id=finding.id,
        to_status=FindingStatus.TRIAGED,
        created_at=finding.first_seen_at,
        from_status=FindingStatus.NEW,
    )
    await repo.add_status_history(history_entry)

    assert len(await repo.list_occurrences(finding.id)) == 1
    assert len(await repo.list_analyses(finding.id)) == 1
    (status_entry,) = await repo.list_status_history(finding.id)
    assert status_entry.from_status is FindingStatus.NEW
    assert status_entry.to_status is FindingStatus.TRIAGED
