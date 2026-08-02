"""Integration test for the Milestone 4 processing pipeline orchestrator,
against real Postgres-backed repositories (Milestone 2) rather than
fakes.

The scanner and object storage are still faked here (no real ``nuclei``
binary or MinIO server is available in this environment -- the same
constraint already documented for Milestone 3's own test suite in
docs/implementation_progress.md's Technical debt list) -- but
``ScanRepositoryPort``, ``AssetRepositoryPort``, and
``FindingRepositoryPort`` are the genuine SQLAlchemy implementations,
running against a real, RLS-enabled PostgreSQL 16 database, exercised
inside ``session_scoped_to_org`` exactly as production code would use
them. This is the thing tests/unit/test_run_scan_workflow.py's
fully-faked repositories cannot prove: that this use case's repository
calls are actually valid against the real schema (correct column types,
foreign keys, unique constraints) and correctly tenant-scoped under RLS.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.application.interfaces.storage_port import StorageObjectNotFoundError, StoragePort
from app.application.scanning.run_scan_workflow import RunScanWorkflowUseCase
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import AssetType, ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.domain.shared.fingerprint import compute_fingerprint
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import make_organization, set_org_context

pytestmark = pytest.mark.integration


class FakeStorage(StoragePort):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self.objects[key] = data

    async def get_object(self, key: str) -> bytes:
        if key not in self.objects:
            raise StorageObjectNotFoundError(key)
        return self.objects[key]

    async def delete_object(self, key: str) -> None:
        del self.objects[key]

    async def object_exists(self, key: str) -> bool:
        return key in self.objects


@dataclass
class FakeActiveScanner(ActiveScanner):
    raw_jsonl_lines: list[dict[str, object]] = field(default_factory=list)
    call_count: int = 0

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        self.call_count += 1
        raw = "\n".join(json.dumps(line) for line in self.raw_jsonl_lines).encode("utf-8")
        now = utcnow()
        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=raw,
            started_at=now,
            completed_at=now,
        )


def _nuclei_line() -> dict[str, object]:
    return {
        "template-id": "CVE-2021-99999",
        "info": {
            "name": "Integration Test Vulnerability",
            "severity": "critical",
            "description": "A vulnerability found during an integration test.",
            "classification": {
                "cve-id": ["CVE-2021-99999"],
                "cvss-score": 9.8,
                "cvss-metrics": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            },
        },
        "host": "example.com",
        "matched-at": "https://example.com/vulnerable-path",
    }


@dataclass
class Fixtures:
    session: AsyncSession
    organization_id: UUID
    trigger_scan: TriggerScanUseCase
    run_scan_workflow: RunScanWorkflowUseCase
    scanner: FakeActiveScanner
    storage: FakeStorage


@pytest_asyncio.fixture
async def fixtures(engine: AsyncEngine) -> AsyncIterator[Fixtures]:
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        organization = make_organization()
        await set_org_context(session, organization.id)
        await SqlAlchemyOrganizationRepository(session).add(organization)
        await session.commit()

        await set_org_context(session, organization.id)
        scan_repository = SqlAlchemyScanRepository(session)
        asset_repository = SqlAlchemyAssetRepository(session)
        finding_repository = SqlAlchemyFindingRepository(session)
        scanner = FakeActiveScanner(raw_jsonl_lines=[_nuclei_line()])
        storage = FakeStorage()

        yield Fixtures(
            session=session,
            organization_id=organization.id,
            trigger_scan=TriggerScanUseCase(scan_repository),
            run_scan_workflow=RunScanWorkflowUseCase(
                scan_repository=scan_repository,
                asset_repository=asset_repository,
                finding_repository=finding_repository,
                active_scanner=scanner,
                storage=storage,
            ),
            scanner=scanner,
            storage=storage,
        )
        await session.commit()

    table_names = (
        "organizations, scans, scan_workflow_steps, assets, asset_observations, "
        "findings, finding_occurrences, finding_status_history"
    )
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


async def test_full_pipeline_persists_a_finding_against_real_postgres(
    fixtures: Fixtures,
) -> None:
    scan = await fixtures.trigger_scan.execute(
        organization_id=fixtures.organization_id,
        target="example.com",
        scanner_name="nuclei",
    )

    result = await fixtures.run_scan_workflow.execute(scan.id)

    assert result.status is ScanStatus.COMPLETED
    assert result.started_at is not None
    assert result.completed_at is not None

    steps = await SqlAlchemyScanRepository(fixtures.session).list_workflow_steps(scan.id)
    assert len(steps) == 8
    assert [s.step_order for s in steps] == sorted(s.step_order for s in steps)
    for step in steps:
        if step.step_name is WorkflowStepName.AI_ANALYZE:
            assert step.status is WorkflowStepStatus.SKIPPED
        else:
            assert step.status is WorkflowStepStatus.COMPLETED

    asset_repository = SqlAlchemyAssetRepository(fixtures.session)
    finding_repository = SqlAlchemyFindingRepository(fixtures.session)

    fingerprint = _expected_fingerprint(fixtures.organization_id)
    finding = await finding_repository.get_by_fingerprint(fixtures.organization_id, fingerprint)
    assert finding is not None
    assert finding.title == "Integration Test Vulnerability"
    assert finding.cvss_score == 9.8
    assert finding.effective_severity is not None
    assert finding.effective_severity.level.value == "critical"

    asset = await asset_repository.get_by_id(finding.asset_id)
    assert asset is not None
    assert asset.value == "example.com"
    assert asset.asset_type is AssetType.DOMAIN

    occurrences = await finding_repository.list_occurrences(finding.id)
    assert len(occurrences) == 1
    assert occurrences[0].scan_id == scan.id

    observations = await asset_repository.list_observations(asset.id)
    assert len(observations) == 1


def _expected_fingerprint(organization_id: UUID) -> str:
    """The fingerprint is deterministic given (org, host, scanner,
    template-id) -- recomputed here rather than hardcoded so this test
    does not silently drift from compute_fingerprint's real behavior."""
    return compute_fingerprint(
        str(organization_id),
        "example.com",
        "nuclei",
        "CVE-2021-99999",
    )


async def test_second_scan_against_the_same_target_updates_the_existing_finding(
    fixtures: Fixtures,
) -> None:
    first_scan = await fixtures.trigger_scan.execute(
        organization_id=fixtures.organization_id,
        target="example.com",
        scanner_name="nuclei",
    )
    await fixtures.run_scan_workflow.execute(first_scan.id)

    second_scan = await fixtures.trigger_scan.execute(
        organization_id=fixtures.organization_id,
        target="example.com",
        scanner_name="nuclei",
    )
    await fixtures.run_scan_workflow.execute(second_scan.id)

    finding_repository = SqlAlchemyFindingRepository(fixtures.session)
    fingerprint = _expected_fingerprint(fixtures.organization_id)
    finding = await finding_repository.get_by_fingerprint(fixtures.organization_id, fingerprint)
    assert finding is not None

    occurrences = await finding_repository.list_occurrences(finding.id)
    assert len(occurrences) == 2
    assert {occ.scan_id for occ in occurrences} == {first_scan.id, second_scan.id}


async def test_invalid_target_marks_the_scan_failed_in_postgres(fixtures: Fixtures) -> None:
    scan = await fixtures.trigger_scan.execute(
        organization_id=fixtures.organization_id,
        target="127.0.0.1",
        scanner_name="nuclei",
    )

    result = await fixtures.run_scan_workflow.execute(scan.id)

    assert result.status is ScanStatus.FAILED
    reloaded = await SqlAlchemyScanRepository(fixtures.session).get_by_id(scan.id)
    assert reloaded is not None
    assert reloaded.status is ScanStatus.FAILED
    assert fixtures.scanner.call_count == 0
