"""The Celery application instance -- Milestone 7's "real Celery/worker
wiring" (PROJECT_STATE.md section 15), so that
``RunScanWorkflowUseCase.execute()`` (app/application/scanning/
run_scan_workflow.py, unchanged this milestone) no longer runs
synchronously inside the HTTP request handler for
``POST .../scans/{scan_id}/run`` (Technical debt item #10).

Both the broker and the result backend point at ``settings.redis_url`` --
the same connection this codebase has required for every ``worker_role``
since Milestone 1 (``app/config.py``), just never actually consumed by
any code until now. No second queue technology is introduced; this is
"a lightweight in-process/Celery-backed domain event dispatcher, not a
new message broker" (PROJECT_STATE.md section 1), applied here to task
dispatch rather than domain events specifically.

This module is imported by two different kinds of process:
  - The API process (``worker_role=api``), which only ever calls
    ``.delay()``/``.apply_async()`` on a task defined in
    ``app/workers/tasks.py`` -- it never needs Celery's worker runtime
    itself, only a client that can publish a task message.
  - The actual Celery worker process (``worker_role=ingestion_worker``,
    started via ``celery -A app.workers.celery_app worker``), which
    additionally needs every ``@celery_app.task``-decorated function to
    already be registered -- guaranteed by ``app/workers/tasks.py``
    importing ``celery_app`` from here (not the reverse), so importing
    that module always has this one already configured.

``get_settings()`` is safe to call from either process type: ``redis_url``
is validated as required for every role (``Settings.check_role_boundaries``),
so this module never has to know or care which role imported it.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

#: The one task queue this milestone defines. A single queue is
#: deliberate, not a placeholder: Milestone 7 wires exactly one worker
#: role (``ingestion_worker``, running the complete, unrestructured
#: ``RunScanWorkflowUseCase``) onto exactly one Celery task -- see
#: ``app/workers/tasks.py``'s module docstring for why the further split
#: into a network-isolated scanner-worker queue is documented future
#: work (Technical debt item #12), not built here. Naming the queue now,
#: rather than leaving every task on Celery's built-in ``celery`` default
#: queue, is what lets a future scanner-worker queue be added later
#: without renaming this one out from under any already-deployed worker.
SCANS_QUEUE_NAME = "scans"


def create_celery_app() -> Celery:
    settings = get_settings()
    # Settings.check_role_boundaries (app/config.py) already guarantees
    # redis_url is not None for every worker_role -- a genuinely missing
    # value already raised at Settings() construction, before this
    # function ever runs. This assert exists for mypy strict's benefit,
    # matching the same pattern already used for the other Settings-
    # sourced values app/main.py's _lifespan asserts on.
    assert settings.redis_url is not None

    app = Celery("security_platform", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.task_default_queue = SCANS_QUEUE_NAME
    # Every task result is a scan's side effects already durably
    # persisted in Postgres via RunScanWorkflowUseCase -- nothing reads a
    # Celery result object back, so results are not kept once a task
    # finishes. Setting this explicitly (rather than relying on Celery's
    # own default) states that as a deliberate choice, not an oversight.
    app.conf.result_expires = 3600
    return app


celery_app = create_celery_app()
