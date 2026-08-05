"""The public Scanning API -- ``/api/v1/organizations/{organization_id}/
scans``, mounted with that prefix by ``app/main.py``.

Three routes map 1:1 onto Milestone 4's two use cases plus a direct
repository read, exactly as PROJECT_STATE.md section 15 specifies ("route
handlers that call TriggerScanUseCase/RunScanWorkflowUseCase ... and the
Milestone 2 repositories"):

  - ``POST .../scans``          -> ``TriggerScanUseCase``
  - ``POST .../scans/{id}/run`` -> ``RunScanWorkflowUseCase``
  - ``GET  .../scans/{id}``     -> ``ScanRepositoryPort`` reads directly

Create and run are separate endpoints, not one combined "create and
run" call, because the two use cases are already separate for a reason
(``TriggerScanUseCase``'s own docstring: recording intent to scan is not
the same operation as executing the pipeline) and because splitting them
is what makes this milestone's resumability story visible over HTTP: a
scan that failed partway through can be retried by calling ``run``
again, exactly as ``RunScanWorkflowUseCase.execute`` already supports
for a direct caller (PROJECT_STATE.md section 3).

Running the pipeline synchronously inside this request handler, and
blocking for as long as the scanner itself takes (up to
``DEFAULT_SCAN_TIMEOUT_SECONDS``, 600s), is a known, deliberate
limitation of this milestone, not an oversight: no task queue or worker
process exists yet to run it asynchronously (that wiring is Milestone
7's "Docker Compose wired end-to-end," per the roadmap in
docs/implementation_progress.md), and building one now would be
Milestone 7 work landing inside Milestone 5. See this milestone's
technical-debt entry in docs/implementation_progress.md.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import (
    get_run_scan_workflow_use_case,
    get_scan_repository,
    get_trigger_scan_use_case,
)
from app.api.v1.schemas import ScanCreateRequest, ScanDetailResponse
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.scanning.run_scan_workflow import RunScanWorkflowUseCase
from app.application.scanning.trigger_scan import TriggerScanUseCase

router = APIRouter(prefix="/organizations/{organization_id}/scans", tags=["scans"])


@router.post("", response_model=ScanDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_scan(
    organization_id: UUID,
    payload: ScanCreateRequest,
    response: Response,
    trigger_scan: TriggerScanUseCase = Depends(get_trigger_scan_use_case),
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> ScanDetailResponse:
    """Creates a ``Scan`` (status ``QUEUED``) plus its eight
    ``ScanWorkflowStep`` rows, all ``PENDING`` -- see
    ``TriggerScanUseCase``. Does not execute anything; call ``run``
    (below) to actually run the pipeline.
    """
    scan = await trigger_scan.execute(
        organization_id=organization_id,
        target=payload.target,
        scanner_name=payload.scanner_name,
    )
    steps = await scan_repository.list_workflow_steps(scan.id)
    response.headers["Location"] = f"/api/v1/organizations/{organization_id}/scans/{scan.id}"
    return ScanDetailResponse.from_domain(scan, steps)


@router.post("/{scan_id}/run", response_model=ScanDetailResponse)
async def run_scan(
    scan_id: UUID,
    run_scan_workflow: RunScanWorkflowUseCase = Depends(get_run_scan_workflow_use_case),
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> ScanDetailResponse:
    """Executes the processing pipeline for an already-triggered scan --
    see ``RunScanWorkflowUseCase``. Safe to call again on a scan that
    previously failed: every step except ``EXECUTE_SCANNER`` is
    recomputed from scratch, and that one step is skipped once it has
    already completed (its raw output is read back from durable storage
    instead) -- see that use case's module docstring for the full
    resumability account.

    A ``scan_id`` that does not exist, or a ``Scan`` whose
    ``scanner_name`` does not match the adapter this process is wired to
    (unreachable through ``create_scan`` above, whose request schema
    only ever accepts ``"nuclei"``, but still possible for a ``Scan`` row
    created some other way), surfaces as 404/409 respectively via the
    global exception handlers registered in ``app/main.py`` -- not
    handled here, so this route stays a thin call into the use case.
    """
    scan = await run_scan_workflow.execute(scan_id)
    steps = await scan_repository.list_workflow_steps(scan.id)
    return ScanDetailResponse.from_domain(scan, steps)


@router.get("/{scan_id}", response_model=ScanDetailResponse)
async def get_scan(
    scan_id: UUID,
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> ScanDetailResponse:
    """A direct repository read -- there is no corresponding use case for
    "fetch a scan and its steps," and inventing one purely to satisfy a
    "routes only call use cases" rule this project's own coding
    standards do not state that strictly would be busywork; PROJECT_STATE.md
    section 15 names this route handler as one that calls "the Milestone 2
    repositories" directly, alongside the two that call Milestone 4's use
    cases.
    """
    scan = await scan_repository.get_by_id(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    steps = await scan_repository.list_workflow_steps(scan.id)
    return ScanDetailResponse.from_domain(scan, steps)
