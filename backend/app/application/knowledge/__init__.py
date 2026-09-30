"""Phase 5 knowledge-ingestion use cases.

- ``ingest_cwe_top25.py`` -- ``IngestCweTop25UseCase``, static one-time
  ingestion of the 2025 CWE Top 25 (MITRE CWE View-1435) into the
  vector store. Implemented in Phase 5 Milestone 3. Deliberately
  independent of ``app.ai_agents.analysis_service.AnalysisService`` --
  no retrieval wiring yet.
"""
