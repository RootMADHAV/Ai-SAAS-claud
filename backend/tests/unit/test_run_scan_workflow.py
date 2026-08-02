"""Unit tests for app/application/scanning/run_scan_workflow.py (Milestone 4).

Every port is faked -- per PROJECT_STATE.md section 11, fakes are for the
consumers of a port. The real SQLAlchemy repositories, the real
``NucleiAdapter``, and the real ``MinioStoragePort`` each already have
their own dedicated test coverage; the concern here is this use case's
own orchestration logic (step sequencing, status derivation, retry,
resumption, idempotent correlate/persist), which is best isolated from
any one port's concrete implementation. A separate integration test
(tests/integration/test_scan_pipeline_orchestrator.py) exercises this
same use case against the real Scan/Asset/Finding repositories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from app.application.interfaces.assets_repository import AssetRepositoryPort
from app.application.interfaces.findings_repository import FindingRepositoryPort
from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.interfaces.storage_port import StorageObjectNotFoundError, StoragePort
from app.application.scanning.normalization import NormalizedFinding
from app.application.scanning.run_scan_workflow import (
    RunScanWorkflowUseCase,
    ScannerMismatchError,
    _PipelineItem,
)
from app.domain.assets.entities import Asset, AssetObservation, AssetRelationship
from app.domain.findings.entities import (
    Finding,
    FindingAnalysis,
    FindingOccurrence,
    FindingStatusHistory,
)
from app.domain.scanning.entities import PIPELINE_STEP_ORDER, Scan, ScanScope, ScanWorkflowStep
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import (
    AssetType,
    ScanStatus,
    WorkflowStepName,
    WorkflowStepStatus,
)
from app.domain.shared.ids import new_id
from app.scanner_engine.base_scanner import ScannerExecutionError


class FakeScanRepository(ScanRepositoryPort):
    def __init__(self) -> None:
        self.scans: dict[UUID, Scan] = {}
        self.steps: dict[UUID, list[ScanWorkflowStep]] = {}
        self.workflow_step_update_count = 0

    async def get_by_id(self, scan_id: UUID) -> Scan | None:
        return self.scans.get(scan_id)

    async def add(self, scan: Scan) -> None:
        self.scans[scan.id] = scan
        self.steps.setdefault(scan.id, [])

    async def update(self, scan: Scan) -> None:
        if scan.id not in self.scans:
            raise LookupError(f"Scan {scan.id} does not exist")
        self.scans[scan.id] = scan

    async def add_workflow_step(self, step: ScanWorkflowStep) -> None:
        self.steps.setdefault(step.scan_id, []).append(step)

    async def update_workflow_step(self, step: ScanWorkflowStep) -> None:
        self.workflow_step_update_count += 1
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


class FakeAssetRepository(AssetRepositoryPort):
    def __init__(self) -> None:
        self.assets: dict[UUID, Asset] = {}
        self.observations: list[AssetObservation] = []
        self.raise_on_add: Exception | None = None

    async def get_by_id(self, asset_id: UUID) -> Asset | None:
        return self.assets.get(asset_id)

    async def get_by_identity(
        self, organization_id: UUID, asset_type: AssetType, value: str
    ) -> Asset | None:
        for asset in self.assets.values():
            if (
                asset.organization_id == organization_id
                and asset.asset_type == asset_type
                and asset.value == value
            ):
                return asset
        return None

    async def add(self, asset: Asset) -> None:
        if self.raise_on_add is not None:
            exc, self.raise_on_add = self.raise_on_add, None
            raise exc
        self.assets[asset.id] = asset

    async def update(self, asset: Asset) -> None:
        if asset.id not in self.assets:
            raise LookupError(f"Asset {asset.id} does not exist")
        self.assets[asset.id] = asset

    async def add_observation(self, observation: AssetObservation) -> None:
        self.observations.append(observation)

    async def list_observations(self, asset_id: UUID) -> list[AssetObservation]:
        return [obs for obs in self.observations if obs.asset_id == asset_id]

    async def add_relationship(self, relationship: AssetRelationship) -> None:
        raise NotImplementedError

    async def list_relationships(self, asset_id: UUID) -> list[AssetRelationship]:
        raise NotImplementedError


class FakeFindingRepository(FindingRepositoryPort):
    def __init__(self) -> None:
        self.findings: dict[UUID, Finding] = {}
        self.occurrences: list[FindingOccurrence] = []
        self.status_history: list[FindingStatusHistory] = []
        self.raise_on_add: Exception | None = None

    async def get_by_id(self, finding_id: UUID) -> Finding | None:
        return self.findings.get(finding_id)

    async def get_by_fingerprint(self, organization_id: UUID, fingerprint: str) -> Finding | None:
        for finding in self.findings.values():
            if finding.organization_id == organization_id and finding.fingerprint == fingerprint:
                return finding
        return None

    async def add(self, finding: Finding) -> None:
        if self.raise_on_add is not None:
            exc, self.raise_on_add = self.raise_on_add, None
            raise exc
        self.findings[finding.id] = finding

    async def update(self, finding: Finding) -> None:
        if finding.id not in self.findings:
            raise LookupError(f"Finding {finding.id} does not exist")
        self.findings[finding.id] = finding

    async def add_occurrence(self, occurrence: FindingOccurrence) -> None:
        self.occurrences.append(occurrence)

    async def list_occurrences(self, finding_id: UUID) -> list[FindingOccurrence]:
        return [occ for occ in self.occurrences if occ.finding_id == finding_id]

    async def add_analysis(self, analysis: FindingAnalysis) -> None:
        raise NotImplementedError

    async def list_analyses(self, finding_id: UUID) -> list[FindingAnalysis]:
        raise NotImplementedError

    async def add_status_history(self, entry: FindingStatusHistory) -> None:
        self.status_history.append(entry)

    async def list_status_history(self, finding_id: UUID) -> list[FindingStatusHistory]:
        return [entry for entry in self.status_history if entry.finding_id == finding_id]


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
        if key not in self.objects:
            raise StorageObjectNotFoundError(key)
        del self.objects[key]

    async def object_exists(self, key: str) -> bool:
        return key in self.objects


@dataclass
class FakeActiveScanner(ActiveScanner):
    """Records every call so tests can assert the scanner is (or is not)
    invoked again on a resumed run -- the one thing
    RunScanWorkflowUseCase must never do twice for the same scan."""

    scanner_name: str = "nuclei"
    raw_jsonl_lines: list[dict[str, object]] = field(default_factory=list)
    raise_on_execute: Exception | None = None
    call_count: int = 0

    @property
    def name(self) -> str:
        return self.scanner_name

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        self.call_count += 1
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        raw = "\n".join(json.dumps(line) for line in self.raw_jsonl_lines).encode("utf-8")
        now = utcnow()
        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=raw,
            started_at=now,
            completed_at=now,
        )


def _nuclei_line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "template-id": "CVE-2021-12345",
        "info": {
            "name": "Example Vulnerability",
            "severity": "high",
            "description": "An example vulnerability.",
            "classification": {
                "cve-id": ["CVE-2021-12345"],
                "cvss-score": 7.5,
                "cvss-metrics": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H",
            },
        },
        "host": "example.com",
        "matched-at": "https://example.com/vuln",
    }
    line.update(overrides)
    return line


@dataclass
class Harness:
    scan_repository: FakeScanRepository
    asset_repository: FakeAssetRepository
    finding_repository: FakeFindingRepository
    scanner: FakeActiveScanner
    storage: FakeStorage
    use_case: RunScanWorkflowUseCase
    organization_id: UUID = field(default_factory=new_id)

    async def trigger(self, *, target: str = "example.com", scanner_name: str = "nuclei") -> Scan:
        now = utcnow()
        scan = Scan(
            id=new_id(),
            organization_id=self.organization_id,
            target=target,
            scanner_name=scanner_name,
            status=ScanStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        await self.scan_repository.add(scan)
        for order, step_name in enumerate(PIPELINE_STEP_ORDER):
            await self.scan_repository.add_workflow_step(
                ScanWorkflowStep(
                    id=new_id(),
                    organization_id=self.organization_id,
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


@pytest.fixture
def harness() -> Harness:
    scan_repository = FakeScanRepository()
    asset_repository = FakeAssetRepository()
    finding_repository = FakeFindingRepository()
    scanner = FakeActiveScanner(raw_jsonl_lines=[_nuclei_line()])
    storage = FakeStorage()
    use_case = RunScanWorkflowUseCase(
        scan_repository=scan_repository,
        asset_repository=asset_repository,
        finding_repository=finding_repository,
        active_scanner=scanner,
        storage=storage,
    )
    return Harness(
        scan_repository, asset_repository, finding_repository, scanner, storage, use_case
    )


async def test_happy_path_completes_and_persists_a_finding(harness: Harness) -> None:
    scan = await harness.trigger()

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.COMPLETED
    assert result.started_at is not None
    assert result.completed_at is not None
    assert len(harness.finding_repository.findings) == 1
    finding = next(iter(harness.finding_repository.findings.values()))
    assert finding.title == "Example Vulnerability"
    assert finding.cvss_score == 7.5
    assert finding.cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"
    assert len(harness.asset_repository.assets) == 1
    asset = next(iter(harness.asset_repository.assets.values()))
    assert asset.value == "example.com"
    assert asset.asset_type is AssetType.DOMAIN
    assert len(harness.asset_repository.observations) == 1
    assert len(harness.finding_repository.occurrences) == 1
    assert len(harness.finding_repository.status_history) == 1


async def test_ai_analyze_is_always_skipped(harness: Harness) -> None:
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.AI_ANALYZE].status is WorkflowStepStatus.SKIPPED
    assert steps[WorkflowStepName.AI_ANALYZE].completed_at is not None


async def test_every_other_step_is_completed(harness: Harness) -> None:
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    for name in PIPELINE_STEP_ORDER:
        if name is WorkflowStepName.AI_ANALYZE:
            continue
        assert steps[name].status is WorkflowStepStatus.COMPLETED, name
        assert steps[name].completed_at is not None


async def test_scanner_is_invoked_exactly_once(harness: Harness) -> None:
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    assert harness.scanner.call_count == 1


async def test_raises_for_unknown_scan(harness: Harness) -> None:
    with pytest.raises(LookupError):
        await harness.use_case.execute(new_id())


async def test_raises_when_workflow_steps_were_never_created(harness: Harness) -> None:
    now = utcnow()
    scan = Scan(
        id=new_id(),
        organization_id=new_id(),
        target="example.com",
        scanner_name="nuclei",
        status=ScanStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    await harness.scan_repository.add(scan)

    with pytest.raises(LookupError):
        await harness.use_case.execute(scan.id)


async def test_scanner_name_mismatch_raises(harness: Harness) -> None:
    scan = await harness.trigger(scanner_name="some-other-scanner")

    with pytest.raises(ScannerMismatchError):
        await harness.use_case.execute(scan.id)


async def test_already_completed_scan_is_a_no_op(harness: Harness) -> None:
    scan = await harness.trigger()
    await harness.use_case.execute(scan.id)
    assert harness.scanner.call_count == 1

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.COMPLETED
    assert harness.scanner.call_count == 1  # not invoked again
    assert len(harness.finding_repository.findings) == 1  # not duplicated


async def test_invalid_target_fails_validate_target_step_and_scan(harness: Harness) -> None:
    scan = await harness.trigger(target="127.0.0.1")

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.FAILED
    assert harness.scanner.call_count == 0  # never reached execute_scanner
    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.VALIDATE_TARGET].status is WorkflowStepStatus.FAILED
    assert steps[WorkflowStepName.VALIDATE_TARGET].error_message is not None
    assert steps[WorkflowStepName.EXECUTE_SCANNER].status is WorkflowStepStatus.PENDING
    assert len(harness.finding_repository.findings) == 0


async def test_scanner_execution_failure_fails_the_scan(harness: Harness) -> None:
    harness.scanner.raise_on_execute = ScannerExecutionError("nuclei exploded")
    scan = await harness.trigger()

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.FAILED
    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.EXECUTE_SCANNER].status is WorkflowStepStatus.FAILED
    assert "nuclei exploded" in (steps[WorkflowStepName.EXECUTE_SCANNER].error_message or "")
    assert steps[WorkflowStepName.NORMALIZE].status is WorkflowStepStatus.PENDING


async def test_retry_after_transient_failure_completes_and_increments_retry_count(
    harness: Harness,
) -> None:
    harness.scanner.raise_on_execute = ScannerExecutionError("transient network error")
    scan = await harness.trigger()

    first = await harness.use_case.execute(scan.id)
    assert first.status is ScanStatus.FAILED

    # "the transient condition clears" -- the same scenario a caller
    # retrying a genuinely temporary failure would hit.
    harness.scanner.raise_on_execute = None
    second = await harness.use_case.execute(scan.id)

    assert second.status is ScanStatus.COMPLETED
    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.EXECUTE_SCANNER].retry_count == 1
    assert steps[WorkflowStepName.EXECUTE_SCANNER].status is WorkflowStepStatus.COMPLETED
    assert steps[WorkflowStepName.EXECUTE_SCANNER].error_message is None
    # Steps that never failed are not counted as retried.
    assert steps[WorkflowStepName.VALIDATE_TARGET].retry_count == 0
    assert harness.scanner.call_count == 2  # once for the failed attempt, once for the retry


async def test_second_scan_of_the_same_target_is_a_recurrence(harness: Harness) -> None:
    """Two different Scan rows detecting the identical vulnerability
    (same fingerprint) must update the one Finding's last_seen_at and add
    a second occurrence, per PROJECT_STATE.md section 3's findings-
    deduplication redesign -- not create a second Finding row."""
    scan_one = await harness.trigger()
    await harness.use_case.execute(scan_one.id)
    assert len(harness.finding_repository.findings) == 1
    first_seen_at = next(iter(harness.finding_repository.findings.values())).first_seen_at

    scan_two = await harness.trigger()
    await harness.use_case.execute(scan_two.id)

    assert len(harness.finding_repository.findings) == 1  # still just the one
    finding = next(iter(harness.finding_repository.findings.values()))
    assert finding.first_seen_at == first_seen_at  # unchanged
    assert finding.last_seen_at > first_seen_at
    assert len(harness.finding_repository.occurrences) == 2  # one per scan
    assert {occ.scan_id for occ in harness.finding_repository.occurrences} == {
        scan_one.id,
        scan_two.id,
    }
    # Only the first scan's persist created a status-history row.
    assert len(harness.finding_repository.status_history) == 1


async def test_second_scan_of_the_same_target_reuses_the_asset(harness: Harness) -> None:
    scan_one = await harness.trigger()
    await harness.use_case.execute(scan_one.id)
    scan_two = await harness.trigger()

    await harness.use_case.execute(scan_two.id)

    assert len(harness.asset_repository.assets) == 1
    assert len(harness.asset_repository.observations) == 2


async def test_multiple_findings_against_the_same_host_share_one_asset(harness: Harness) -> None:
    harness.scanner.raw_jsonl_lines = [
        _nuclei_line(**{"template-id": "template-a"}),
        _nuclei_line(**{"template-id": "template-b"}),
    ]
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    assert len(harness.asset_repository.assets) == 1
    assert len(harness.finding_repository.findings) == 2
    assert len(harness.asset_repository.observations) == 1  # one observation per (scan, asset)


async def test_ip_host_is_correlated_as_an_ip_asset(harness: Harness) -> None:
    harness.scanner.raw_jsonl_lines = [
        _nuclei_line(host="203.0.113.7", **{"matched-at": "203.0.113.7"})
    ]
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    asset = next(iter(harness.asset_repository.assets.values()))
    assert asset.asset_type is AssetType.IP
    assert asset.value == "203.0.113.7"


async def test_finding_without_cvss_still_persists_with_raw_evidence_preserved(
    harness: Harness,
) -> None:
    """No CVSS in nuclei's output -> no cvss_score/vector on the Finding,
    and definitely not smuggled into ai_severity_level (see _enrich's
    docstring) -- but nuclei's own raw severity claim is not lost, it
    travels in the occurrence's raw_evidence."""
    harness.scanner.raw_jsonl_lines = [
        {
            "template-id": "info-template",
            "info": {"name": "Informational finding", "severity": "info"},
            "host": "example.com",
        }
    ]
    scan = await harness.trigger()

    await harness.use_case.execute(scan.id)

    finding = next(iter(harness.finding_repository.findings.values()))
    assert finding.cvss_score is None
    assert finding.cvss_vector is None
    assert finding.ai_severity_level is None
    assert finding.effective_severity is None
    occurrence = harness.finding_repository.occurrences[0]
    assert occurrence.raw_evidence is not None
    assert occurrence.raw_evidence["info"]["severity"] == "info"  # type: ignore[index]


async def test_invalid_cvss_vector_is_dropped_without_failing_the_scan(harness: Harness) -> None:
    harness.scanner.raw_jsonl_lines = [
        _nuclei_line(
            info={
                "name": "Bad CVSS",
                "severity": "high",
                "classification": {
                    "cvss-score": 7.5,
                    "cvss-metrics": "not-a-real-vector",
                },
            }
        )
    ]
    scan = await harness.trigger()

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.COMPLETED
    finding = next(iter(harness.finding_repository.findings.values()))
    assert finding.cvss_score is None
    assert finding.cvss_vector is None


async def test_no_findings_still_completes_the_scan(harness: Harness) -> None:
    harness.scanner.raw_jsonl_lines = []
    scan = await harness.trigger()

    result = await harness.use_case.execute(scan.id)

    assert result.status is ScanStatus.COMPLETED
    assert harness.finding_repository.findings == {}
    assert harness.asset_repository.assets == {}


async def test_resuming_after_a_correlate_failure_does_not_rerun_the_scanner(
    harness: Harness,
) -> None:
    """EXECUTE_SCANNER must not be invoked a second time once it has
    already succeeded -- even though a later step (CORRELATE here) fails
    and the scan as a whole must be retried. This is the one skip this
    module's docstring calls out as load-bearing (re-running the real
    scanner is expensive and not idempotent), distinct from
    ``test_already_completed_scan_is_a_no_op``, where the whole *scan* --
    not just this one step -- was already done."""
    harness.asset_repository.raise_on_add = RuntimeError("transient db blip")
    scan = await harness.trigger()

    first = await harness.use_case.execute(scan.id)
    assert first.status is ScanStatus.FAILED
    assert harness.scanner.call_count == 1

    second = await harness.use_case.execute(scan.id)

    assert second.status is ScanStatus.COMPLETED
    assert harness.scanner.call_count == 1  # EXECUTE_SCANNER was skipped, not re-run
    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.EXECUTE_SCANNER].status is WorkflowStepStatus.COMPLETED
    assert steps[WorkflowStepName.CORRELATE].status is WorkflowStepStatus.COMPLETED
    assert steps[WorkflowStepName.CORRELATE].retry_count == 1


async def test_resuming_after_a_persist_failure_does_not_rerun_ai_analyze(
    harness: Harness,
) -> None:
    """AI_ANALYZE runs (and is marked SKIPPED) before PERSIST in pipeline
    order. If PERSIST then fails and the scan is retried, AI_ANALYZE --
    already SKIPPED -- must not be re-entered either."""
    harness.finding_repository.raise_on_add = RuntimeError("transient db blip")
    scan = await harness.trigger()

    first = await harness.use_case.execute(scan.id)
    assert first.status is ScanStatus.FAILED
    first_steps = {
        s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)
    }
    assert first_steps[WorkflowStepName.AI_ANALYZE].status is WorkflowStepStatus.SKIPPED
    assert first_steps[WorkflowStepName.PERSIST].status is WorkflowStepStatus.FAILED

    second = await harness.use_case.execute(scan.id)

    assert second.status is ScanStatus.COMPLETED
    steps = {s.step_name: s for s in await harness.scan_repository.list_workflow_steps(scan.id)}
    assert steps[WorkflowStepName.AI_ANALYZE].status is WorkflowStepStatus.SKIPPED
    assert steps[WorkflowStepName.PERSIST].retry_count == 1


async def test_persist_defends_against_a_missing_asset_id(harness: Harness) -> None:
    """A defensive invariant check, not a reachable path through the
    public ``execute()`` entry point -- CORRELATE always sets
    ``item.asset_id`` before PERSIST runs in the real pipeline. Exercised
    directly against the private method since there is no way to reach
    it with a violated invariant any other way."""
    scan = await harness.trigger()
    item = _PipelineItem(
        normalized=NormalizedFinding(
            scanner_name="nuclei",
            template_id="t",
            title="t",
            host="h",
            matched_at="h",
            raw_severity="info",
        ),
        fingerprint="irrelevant",
    )

    with pytest.raises(RuntimeError, match="correlate must run before persist"):
        await harness.use_case._persist(scan, [item])  # noqa: SLF001
