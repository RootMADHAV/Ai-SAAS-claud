"""Celery tasks that run application-layer use cases off the HTTP
request thread.

Milestone 7: ``celery_app.py`` (the Celery application instance) and
``tasks.py`` (``execute_scan_workflow``,
``_run_scan_workflow_from_settings``, ``run_scan_workflow_task`` -- the
task that runs ``RunScanWorkflowUseCase`` inside an ``ingestion_worker``
process instead of inside the API's HTTP request handler). Analysis- and
report-specific tasks are not yet needed -- Findings/Reporting have no
HTTP surface or use cases of their own yet (see PROJECT_STATE.md).
"""
