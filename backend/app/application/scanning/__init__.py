"""Scanning use cases.

Milestone 4: ``trigger_scan.py`` (``TriggerScanUseCase`` -- creates a
``Scan`` plus its eight ``ScanWorkflowStep`` rows) and
``run_scan_workflow.py`` (``RunScanWorkflowUseCase`` -- the processing
pipeline orchestrator: ``validate_target -> execute_scanner -> normalize
-> deduplicate -> correlate -> enrich -> ai_analyze -> persist``, per
PROJECT_STATE.md section 3). ``normalization.py`` is a supporting module
for the ``normalize`` step, not a use case of its own.

Milestone 6: ``run_scan_workflow.py``'s ``AI_ANALYZE`` step now calls a
real ``AnalysisService`` (``app/ai_agents/analysis_service.py``) instead
of unconditionally marking itself ``SKIPPED`` -- see that module's
docstring for the pipeline-ordering and recomputation-cost design notes
this introduced.
"""
