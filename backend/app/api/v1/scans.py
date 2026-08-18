"""The public Scanning API -- ``/api/v1/organizations/{organization_id}/
scans``, mounted with that prefix by ``app/main.py``.

Three routes map onto Milestone 4's use cases, a dispatch call, and a
direct repository read:

  - ``POST .../scans``          -> ``TriggerScanUseCase``
  - ``POST .../scans/{id}/run`` -> validates, then dispatches a Celery
                                    task (``app/workers/tasks.py``)
  - ``GET  .../scans/{id}``     -> ``ScanRepositoryPort`` reads directly

Create and run are separate endpoints, not one combined "create and
run" call, because the two use cases are already separate for a reason
(``TriggerScanUseCase``'s own docstring: recording intent to scan is not
the same operation as executing the pipeline) and because splitting them
is what makes this milestone's resumability story visible over HTTP: a
scan that failed partway through can be retried by calling ``run``
again, exactly as ``RunScanWorkflowUseCase.execute`` already supports
for a direct caller (PROJECT_STATE.md section 3).

Milestone 7 update -- the actual API-contract change this milestone
makes, locked in before implementation began (see PROJECT_STATE.md
section 15 and Technical debt item #10 in
docs/implementation_progress.md, both now resolved by this change):
``run_scan`` no longer executes ``RunScanWorkflowUseCase`` inside this
request handler, and no longer returns ``200`` with a finished scan.
Instead it does exactly two things synchronously -- the same two checks
``RunScanWorkflowUseCase.execute`` itself always performed first, before
this milestone, unchanged in substance, just relocated -- and then
dispatches:

  1. Confirm the scan exists (404 if not) -- a cheap, single-row read,
     the same ``ScanRepositoryPort.get_by_id`` this route already used
     for ``get_scan`` below.
  2. Confirm the scan's recorded ``scanner_name`` matches the adapter
     this deployment is wired to (409, via the same
     ``ScannerMismatchError`` -> 409 global handler in ``app/main.py``
     that already existed for this exact error, previously raised from
     inside the use case instead of here).
  3. If the scan is not already ``RUNNING``, dispatch
     ``run_scan_workflow_task`` (via the injected ``ScanDispatcher`` --
     see ``app/api/dependencies.py``'s ``get_scan_dispatcher``) and
     return ``202 Accepted`` with the scan's current, pre-execution
     detail. A scan already ``RUNNING`` is not re-dispatched (avoiding a
     duplicate concurrent worker run of the very same scan -- a new,
     easy-to-trigger failure mode this change introduces, since a client
     can now call ``run`` twice in rapid succession and get a fast
     ``202`` both times, unlike before, when the second call would have
     blocked behind the first one's synchronous execution); ``202`` is
     still returned in that case, since the caller's request -- "make
     sure this scan is running" -- is genuinely satisfied either way.

A client observes the scan's actual progress to completion by polling
``GET .../scans/{scan_id}`` (unchanged by this milestone) -- the
standard REST async-task-dispatch pattern, not a new one invented for
this codebase.

What this route does *not* re-implement: ``RunScanWorkflowUseCase.
execute``'s own early-return for an already ``COMPLETED``/``CANCELLED``
scan. That check still lives exactly once, inside the use case (now
running inside the Celery task, ``app/workers/tasks.py``) -- duplicating
it here would only save one wasted task dispatch for an edge case that
costs nothing else to let the task itself short-circuit on, the same way
it always has.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import (
    ScanDispatcher,
    get_active_scanner,
    get_scan_dispatcher,
    get_scan_repository,
    get_trigger_scan_use_case,
)
from app.api.v1.schemas import ScanCreateRequest, ScanDetailResponse
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.scanning.run_scan_workflow import ScannerMismatchError
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.domain.shared.enums import ScanStatus

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


@router.post(
    "/{scan_id}/run", response_model=ScanDetailResponse, status_code=status.HTTP_202_ACCEPTED
)
async def run_scan(
    scan_id: UUID,
    organization_id: UUID,
    active_scanner: ActiveScanner = Depends(get_active_scanner),
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
    dispatch_scan: ScanDispatcher = Depends(get_scan_dispatcher),
) -> ScanDetailResponse:
    """Validates, then dispatches the processing pipeline for an
    already-triggered scan to run asynchronously -- see module docstring
    for the full account of this milestone's API-contract change, and
    ``RunScanWorkflowUseCase`` (``app/application/scanning/
    run_scan_workflow.py``, unchanged this milestone, now invoked from
    inside ``app/workers/tasks.py``'s Celery task instead of from here)
    for what actually happens once the dispatched task runs. Safe to
    call again on a scan that previously failed: the use case's own
    resumability (every step except ``EXECUTE_SCANNER`` is recomputed;
    that one is skipped once already completed) is unaffected by *where*
    the use case runs.

    Poll ``GET .../scans/{scan_id}`` (below) to observe progress to
    completion.
    """
    scan = await scan_repository.get_by_id(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    if scan.scanner_name != active_scanner.name:
        raise ScannerMismatchError(
            f"scan {scan_id} is registered for scanner_name={scan.scanner_name!r}, "
            f"but this deployment is wired to the {active_scanner.name!r} adapter"
        )

    if scan.status is not ScanStatus.RUNNING:
        dispatch_scan(organization_id, scan.id)

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
    repositories" directly. As of Milestone 7, this is also how a client
    observes a dispatched ``run`` progressing to completion -- see
    ``run_scan`` above.
    """
    scan = await scan_repository.get_by_id(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    steps = await scan_repository.list_workflow_steps(scan.id)
    return ScanDetailResponse.from_domain(scan, steps)
