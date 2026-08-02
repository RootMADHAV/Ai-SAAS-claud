"""``run_scan_workflow`` use case: the processing pipeline orchestrator.

Executes the locked eight-stage pipeline (PROJECT_STATE.md section 3)
against an existing ``Scan`` (created by ``TriggerScanUseCase``):
``validate_target -> execute_scanner -> normalize -> deduplicate ->
correlate -> enrich -> ai_analyze -> persist``. Each stage updates its
own ``ScanWorkflowStep`` row, and ``Scan.status`` is derived from those
rows afterward via ``derive_scan_status`` -- see that function's
docstring in ``app/domain/scanning/entities.py`` for the derivation
rule itself.

Resumability (PROJECT_STATE.md section 3's stated rationale for storing
steps as explicit rows -- "retry from the failed step, not from
scratch") is implemented as follows: every stage except
``EXECUTE_SCANNER`` is a pure, side-effect-free-on-repeat computation
(normalize/deduplicate/enrich never write anything; correlate/persist
are guarded against duplicate writes on a retried run -- see
``_correlate``/``_persist``), so it is always safe to run one again with
the same inputs. This use case takes advantage of that by simply
recomputing every one of those stages on every invocation, using
whichever inputs are cheapest to obtain (in particular, ``normalize``
always re-reads the scanner's raw output from durable storage rather
than depending on anything held only in memory from a previous, possibly
crashed, invocation). ``EXECUTE_SCANNER`` is the one stage that is
neither cheap nor safe to blindly repeat -- re-running it means
re-scanning a live target -- so it is the only stage this use case
actually skips (not just "does not re-track") once its
``ScanWorkflowStep`` row is already ``COMPLETED``; see
``_run_execute_scanner_step``.

Deliberately out of scope for Milestone 4, and not built here:
  - ``AI_ANALYZE`` has no real handler -- ``AIProviderPort`` and an
    ``AnalysisService`` do not exist yet (Milestone 6, per the roadmap in
    docs/implementation_progress.md). This use case unconditionally marks
    that step ``SKIPPED`` rather than fabricating a result -- see
    ``_run_ai_analyze_step`` and ``WorkflowStepStatus.SKIPPED``'s own
    role in the state machine (PROJECT_STATE.md section 5). A skipped
    step still counts as "done" for ``derive_scan_status``, so a scan
    completes today without AI commentary, which Milestone 6 will later
    add without needing to touch this file's control flow.
  - No ``FindingCreated``/event-bus publication after persist, even
    though PROJECT_STATE.md section 3 names this as planned ("still
    publishes after persist, for future consumers") -- ``EventBusPort``
    itself does not exist yet (see PROJECT_STATE.md section 4's folder
    structure: "AIProviderPort/EventBusPort pending"), and this use case
    does not invent one just to satisfy that forward reference.
  - Dispatching across multiple ``ActiveScanner`` implementations (a
    scanner "registry") -- only ``NucleiAdapter`` exists (Milestone 3).
    This use case is constructed with exactly one ``ActiveScanner`` and
    raises ``ScannerMismatchError`` if a ``Scan``'s recorded
    ``scanner_name`` does not match it, rather than guessing which of
    several adapters to use.
  - Splitting ``EXECUTE_SCANNER`` into its own worker process, isolated
    from database access, per the scanner-worker/ingestion-worker network
    segmentation locked in PROJECT_STATE.md section 3. This use case is
    process-agnostic -- it calls its injected ``ActiveScanner`` and
    ``StoragePort`` directly in the same call stack as the repository
    calls -- and is written so that whichever process ends up
    constructing it (a single process today; a split scanner-worker /
    ingestion-worker pair once Milestone 7 wires Docker Compose
    end-to-end) can do so without changing this file. Which process that
    is, and how work crosses that boundary, is deployment wiring left to
    Milestone 7.
"""

from __future__ import annotations

import contextlib
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from app.application.interfaces.assets_repository import AssetRepositoryPort
from app.application.interfaces.findings_repository import FindingRepositoryPort
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.interfaces.storage_port import StoragePort
from app.application.scanning.normalization import NormalizedFinding, normalize_scan_output
from app.domain.assets.entities import Asset, AssetObservation
from app.domain.findings.entities import Finding, FindingOccurrence, FindingStatusHistory
from app.domain.findings.value_objects import CVSS
from app.domain.scanning.entities import (
    PIPELINE_STEP_ORDER,
    Scan,
    ScanWorkflowStep,
    derive_scan_status,
)
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import (
    AssetStatus,
    AssetType,
    ConfidenceLevel,
    FindingStatus,
    ScanStatus,
    WorkflowStepName,
    WorkflowStepStatus,
)
from app.domain.shared.fingerprint import compute_fingerprint
from app.domain.shared.ids import new_id

# target_validation.py has zero framework/DB imports and is already
# imported directly (not behind a port) by app/scanner_engine/adapters/
# nuclei/adapter.py -- there is exactly one way to validate a target, so
# this module is treated as a shared, framework-free utility rather than
# a swappable adapter needing its own port, the same call already made
# for that adapter.
from app.infrastructure.security.target_validation import validate_target

DEFAULT_SCAN_TIMEOUT_SECONDS = 600.0

# A scanner directly connecting to and fingerprinting a host is a strong,
# first-party signal -- PROJECT_STATE.md section 3's "confidence rule
# deliberately simple: most recent observation's confidence, not a
# weighted blend" already commits to not inventing scoring logic here.
_DIRECT_DETECTION_CONFIDENCE = 1.0

T = TypeVar("T")


class ScannerMismatchError(ValueError):
    """Raised when a ``Scan``'s recorded ``scanner_name`` does not match
    the single ``ActiveScanner`` this use case was constructed with (see
    module docstring on why no multi-adapter registry exists yet)."""


@dataclass(slots=True)
class _PipelineItem:
    """Internal, per-normalized-finding working state threaded through
    deduplicate -> correlate -> enrich -> persist. Not a domain entity
    and not exposed outside this module -- purely an orchestration
    detail of how one invocation's batch is carried from stage to
    stage."""

    normalized: NormalizedFinding
    fingerprint: str
    existing_finding: Finding | None = None
    asset_id: UUID | None = None
    cvss: CVSS | None = None


def _effective_host(normalized: NormalizedFinding, scan: Scan) -> str:
    """Nuclei's own ``host`` field is occasionally absent; falling back
    to the scan's original target keeps fingerprinting and asset
    correlation from ever operating on an empty string."""
    return normalized.host or scan.target


def _infer_asset_type(value: str) -> AssetType:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return AssetType.DOMAIN
    return AssetType.IP


class RunScanWorkflowUseCase:
    def __init__(
        self,
        *,
        scan_repository: ScanRepositoryPort,
        asset_repository: AssetRepositoryPort,
        finding_repository: FindingRepositoryPort,
        active_scanner: ActiveScanner,
        storage: StoragePort,
        scan_timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
    ) -> None:
        self._scan_repository = scan_repository
        self._asset_repository = asset_repository
        self._finding_repository = finding_repository
        self._active_scanner = active_scanner
        self._storage = storage
        self._scan_timeout_seconds = scan_timeout_seconds

    async def execute(self, scan_id: UUID) -> Scan:
        scan = await self._scan_repository.get_by_id(scan_id)
        if scan is None:
            raise LookupError(f"Scan {scan_id} does not exist")
        if scan.scanner_name != self._active_scanner.name:
            raise ScannerMismatchError(
                f"scan {scan_id} is registered for scanner_name={scan.scanner_name!r}, "
                f"but this use case is wired to the {self._active_scanner.name!r} adapter"
            )
        if scan.status in (ScanStatus.COMPLETED, ScanStatus.CANCELLED):
            return scan

        steps_by_name = await self._load_steps(scan_id)

        if scan.status is ScanStatus.QUEUED:
            scan.status = ScanStatus.RUNNING
            scan.started_at = scan.started_at or utcnow()
            await self._scan_repository.update(scan)

        with contextlib.suppress(Exception):
            # A raised exception here means one step's `action()` failed
            # (see `_run_step`) -- already fully recorded on that step's
            # row (status=FAILED, error_message) before it propagated out
            # of `_run_step`. That is a normal, expected business outcome
            # of running a scan (an unreachable target, a scanner crash),
            # not a caller-facing error -- the `finally` block below
            # reflects it in `scan.status` via `derive_scan_status`, and
            # the caller reads that field rather than needing a
            # try/except around every call to learn a scan failed.
            await self._run_step(
                steps_by_name[WorkflowStepName.VALIDATE_TARGET], lambda: self._validate(scan)
            )
            await self._run_execute_scanner_step(
                scan, steps_by_name[WorkflowStepName.EXECUTE_SCANNER]
            )
            normalized = await self._run_step(
                steps_by_name[WorkflowStepName.NORMALIZE], lambda: self._normalize(scan)
            )
            items = await self._run_step(
                steps_by_name[WorkflowStepName.DEDUPLICATE],
                lambda: self._deduplicate(scan, normalized),
            )
            await self._run_step(
                steps_by_name[WorkflowStepName.CORRELATE], lambda: self._correlate(scan, items)
            )
            await self._run_step(
                steps_by_name[WorkflowStepName.ENRICH], lambda: self._enrich(items)
            )
            await self._run_ai_analyze_step(steps_by_name[WorkflowStepName.AI_ANALYZE])
            await self._run_step(
                steps_by_name[WorkflowStepName.PERSIST], lambda: self._persist(scan, items)
            )

        final_steps = await self._scan_repository.list_workflow_steps(scan_id)
        scan.status = derive_scan_status(final_steps)
        if scan.status in (ScanStatus.COMPLETED, ScanStatus.FAILED):
            scan.completed_at = utcnow()
        await self._scan_repository.update(scan)

        return scan

    async def _load_steps(self, scan_id: UUID) -> dict[WorkflowStepName, ScanWorkflowStep]:
        steps = await self._scan_repository.list_workflow_steps(scan_id)
        steps_by_name = {step.step_name: step for step in steps}
        missing = [name for name in PIPELINE_STEP_ORDER if name not in steps_by_name]
        if missing:
            raise LookupError(
                f"scan {scan_id} is missing workflow steps {missing!r} -- "
                "call TriggerScanUseCase before RunScanWorkflowUseCase"
            )
        return steps_by_name

    @staticmethod
    def _raw_output_key(scan_id: UUID) -> str:
        return f"{scan_id}/raw-output"

    async def _run_step(self, step: ScanWorkflowStep, action: Callable[[], Awaitable[T]]) -> T:
        """Runs one pipeline stage, updating its ``ScanWorkflowStep`` row
        (``RUNNING`` -> ``COMPLETED``/``FAILED``) around it. ``retry_count``
        is incremented only when the step's *current* status is already
        ``FAILED`` -- i.e. only on a genuine retry of a previous failure,
        never on the routine re-execution described in this module's
        docstring for an otherwise-healthy resumed run."""
        if step.status is WorkflowStepStatus.FAILED:
            step.retry_count += 1
        step.status = WorkflowStepStatus.RUNNING
        step.started_at = step.started_at or utcnow()
        step.error_message = None
        step.completed_at = None
        await self._scan_repository.update_workflow_step(step)
        try:
            result = await action()
        except Exception as exc:
            step.status = WorkflowStepStatus.FAILED
            step.error_message = str(exc)
            await self._scan_repository.update_workflow_step(step)
            raise
        step.status = WorkflowStepStatus.COMPLETED
        step.completed_at = utcnow()
        await self._scan_repository.update_workflow_step(step)
        return result

    async def _run_execute_scanner_step(self, scan: Scan, step: ScanWorkflowStep) -> None:
        """The one stage exempt from this module's "always safe to
        recompute" rule -- see module docstring. If this step already
        succeeded, its raw output is durably in storage and the real
        scanner is never invoked a second time for the same scan."""
        if step.status is WorkflowStepStatus.COMPLETED:
            return

        async def _do() -> None:
            scan_output = await self._active_scanner.execute(
                scan.target, timeout_seconds=self._scan_timeout_seconds
            )
            await self._storage.put_object(
                self._raw_output_key(scan.id),
                scan_output.raw_bytes,
                content_type="application/x-ndjson",
            )

        await self._run_step(step, _do)

    async def _run_ai_analyze_step(self, step: ScanWorkflowStep) -> None:
        """Unconditionally skipped -- see module docstring. Cannot fail,
        so no try/except is needed around it."""
        if step.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.SKIPPED):
            return
        step.status = WorkflowStepStatus.RUNNING
        step.started_at = step.started_at or utcnow()
        step.error_message = None
        await self._scan_repository.update_workflow_step(step)
        step.status = WorkflowStepStatus.SKIPPED
        step.completed_at = utcnow()
        await self._scan_repository.update_workflow_step(step)

    async def _validate(self, scan: Scan) -> None:
        validate_target(scan.target)

    async def _normalize(self, scan: Scan) -> list[NormalizedFinding]:
        raw_bytes = await self._storage.get_object(self._raw_output_key(scan.id))
        return normalize_scan_output(
            output_format=self._active_scanner.output_format,
            scanner_name=self._active_scanner.name,
            raw_bytes=raw_bytes,
        )

    async def _deduplicate(
        self, scan: Scan, normalized: list[NormalizedFinding]
    ) -> list[_PipelineItem]:
        items: list[_PipelineItem] = []
        for n in normalized:
            host = _effective_host(n, scan)
            fingerprint = compute_fingerprint(
                str(scan.organization_id), host, scan.scanner_name, n.template_id
            )
            existing = await self._finding_repository.get_by_fingerprint(
                scan.organization_id, fingerprint
            )
            items.append(
                _PipelineItem(normalized=n, fingerprint=fingerprint, existing_finding=existing)
            )
        return items

    async def _correlate(self, scan: Scan, items: list[_PipelineItem]) -> None:
        pipeline_run_at = utcnow()
        asset_cache: dict[tuple[AssetType, str], Asset] = {}
        already_observed_cache: dict[UUID, bool] = {}

        for item in items:
            value = _effective_host(item.normalized, scan)
            asset_type = _infer_asset_type(value)
            cache_key = (asset_type, value)
            asset = asset_cache.get(cache_key)
            if asset is None:
                asset = await self._asset_repository.get_by_identity(
                    scan.organization_id, asset_type, value
                )
                if asset is None:
                    asset = Asset(
                        id=new_id(),
                        organization_id=scan.organization_id,
                        asset_type=asset_type,
                        value=value,
                        status=AssetStatus.ACTIVE,
                        first_seen_at=pipeline_run_at,
                        last_seen_at=pipeline_run_at,
                        created_at=pipeline_run_at,
                        updated_at=pipeline_run_at,
                        confidence=_DIRECT_DETECTION_CONFIDENCE,
                    )
                    await self._asset_repository.add(asset)
                else:
                    asset.last_seen_at = pipeline_run_at
                    asset.confidence = _DIRECT_DETECTION_CONFIDENCE
                    await self._asset_repository.update(asset)
                asset_cache[cache_key] = asset

            item.asset_id = asset.id

            already_observed = already_observed_cache.get(asset.id)
            if already_observed is None:
                observations = await self._asset_repository.list_observations(asset.id)
                already_observed = any(obs.scan_id == scan.id for obs in observations)
            if not already_observed:
                await self._asset_repository.add_observation(
                    AssetObservation(
                        id=new_id(),
                        organization_id=scan.organization_id,
                        asset_id=asset.id,
                        scan_id=scan.id,
                        confidence=_DIRECT_DETECTION_CONFIDENCE,
                        observed_at=pipeline_run_at,
                        created_at=pipeline_run_at,
                    )
                )
            already_observed_cache[asset.id] = True

    async def _enrich(self, items: list[_PipelineItem]) -> None:
        """Validates each item's scanner-reported CVSS candidate into a
        real ``CVSS`` value object. A candidate that fails validation
        (out-of-range score, malformed vector -- both of which real
        nuclei templates occasionally have) is treated as absent, not as
        a pipeline failure -- one bad CVSS string should not fail an
        entire scan's worth of otherwise-good findings.

        Deliberately does not fall back to nuclei's own raw
        ``raw_severity`` string when no valid CVSS exists.
        ``Finding.ai_severity_level`` is named for what it is: an AI
        provider's own estimate (Milestone 6), not a scanner's
        self-reported severity claim, and writing scanner data into a
        field named for AI-derived data would misrepresent its
        provenance. Nuclei's raw severity is not lost -- it travels in
        ``FindingOccurrence.raw_evidence`` (see ``_persist``) -- it is
        just not asserted as `the` severity here. A finding enriched with
        neither a valid CVSS nor (later) an AI estimate legitimately has
        no ``effective_severity`` yet; that is correct today, not a bug.
        """
        for item in items:
            n = item.normalized
            if n.cvss_score is None or n.cvss_vector is None:
                continue
            try:
                item.cvss = CVSS(score=n.cvss_score, vector=n.cvss_vector)
            except ValueError:
                item.cvss = None

    async def _persist(self, scan: Scan, items: list[_PipelineItem]) -> None:
        pipeline_run_at = utcnow()
        for item in items:
            n = item.normalized
            if item.asset_id is None:
                raise RuntimeError("correlate must run before persist -- item has no asset_id")

            if item.existing_finding is not None:
                finding = item.existing_finding
                finding.last_seen_at = pipeline_run_at
                await self._finding_repository.update(finding)
            else:
                finding = Finding(
                    id=new_id(),
                    organization_id=scan.organization_id,
                    asset_id=item.asset_id,
                    fingerprint=item.fingerprint,
                    title=n.title,
                    status=FindingStatus.NEW,
                    confidence=ConfidenceLevel.UNCONFIRMED,
                    first_seen_at=pipeline_run_at,
                    last_seen_at=pipeline_run_at,
                    created_at=pipeline_run_at,
                    updated_at=pipeline_run_at,
                    description=n.description,
                    cvss_score=item.cvss.score if item.cvss else None,
                    cvss_vector=item.cvss.vector if item.cvss else None,
                )
                await self._finding_repository.add(finding)
                await self._finding_repository.add_status_history(
                    FindingStatusHistory(
                        id=new_id(),
                        organization_id=scan.organization_id,
                        finding_id=finding.id,
                        from_status=None,
                        to_status=FindingStatus.NEW,
                        created_at=pipeline_run_at,
                        reason=f"created from scan {scan.id}",
                    )
                )

            existing_occurrences = await self._finding_repository.list_occurrences(finding.id)
            if not any(occ.scan_id == scan.id for occ in existing_occurrences):
                await self._finding_repository.add_occurrence(
                    FindingOccurrence(
                        id=new_id(),
                        organization_id=scan.organization_id,
                        finding_id=finding.id,
                        scan_id=scan.id,
                        asset_id=item.asset_id,
                        detected_at=pipeline_run_at,
                        created_at=pipeline_run_at,
                        raw_evidence=n.raw_evidence,
                    )
                )
