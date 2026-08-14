"""AI agents: the AI Platform bounded context's concrete capability.

Milestone 6: ``analysis_service.py`` (``AnalysisService`` -- one concrete
service with no formal ``BaseAgent`` interface yet; PROJECT_STATE.md
section 3: extract one when a second agent's real shape is known, not
before). Wraps an injected ``AIProviderPort``
(``app/application/interfaces/ai_provider_port.py``) to turn one
finding's scanner-reported data into a schema-validated
``FindingAnalysisResult``, which ``RunScanWorkflowUseCase``'s
``AI_ANALYZE``/``PERSIST`` steps consume.
"""
