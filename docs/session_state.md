# Session State

Overwritten at the end of every coding session -- reflects the most
recent session only. Cumulative history: `docs/implementation_progress.md`.
Permanent architecture/decisions reference: `PROJECT_STATE.md`.

## Date
2026-09-25

## Last completed task
Phase 5 Milestone 5 -- an eleventh out-of-sequence explicit-instruction
session, directly following the tenth (Milestone 4). Goal: finish Phase
5 wiring/verification so the RAG path works through the real
application composition/deployment path.

**Inspection before editing (per instruction):** read fresh from disk:
`app/main.py`, `app/workers/tasks.py`, `app/config.py`, `backend/
Dockerfile`, `docker-compose.yml`, `app/infrastructure/ai_providers/
anthropic_provider.py`, Qdrant's docker-compose config, `AnalysisService`
construction, current Phase 5 tests, and integration/e2e test
conventions (`tests/conftest.py`, `tests/integration/support.py`,
`test_scan_worker_task.py`).

**A real mistake made and corrected mid-session, disclosed rather than
hidden:** to build a fuller sandbox reconstruction than Milestone 4's
(this milestone touches `run_scan_workflow.py`, unlike Milestone 4,
which avoided it entirely), an initial attempt reconstructed several
files from recollection to save read calls. One of them --
`run_scan_workflow.py` -- turned out **not** to match the real file's
actual structure (guessed a `_PipelineFindingItem`/single-
`active_scanners` shape; the real file has a `_DedupResult`/
`_PipelineState`/single-`active_scanner` shape, entirely different).
Caught before any production edit was made by re-fetching the file
fresh and comparing. From that point on, **every subsequent production
edit was made only against a same-turn fresh read of the exact file
being changed** -- the sandbox reconstruction was used only for testing
confidence on the new logic in isolation, never as the source for a
real edit. `config.py` and `tasks.py`, independently re-verified the
same way, matched their fresh reads exactly.

**Key finding during inspection:** `app/main.py`'s `_lifespan`
(worker_role=api) already does not construct `AnalysisService`/
`AnthropicProvider`/`MinioStoragePort` at all (confirmed unchanged since
Milestone 7) -- the API process was already correctly excluded from
this composition, satisfying "not unnecessarily in the API process"
before any Milestone 5 code was written. The actual composition root is
`app/workers/tasks.py`'s `_run_scan_workflow_from_settings`
(`worker_role=ingestion_worker`), confirmed by tracing
`app/api/dependencies.py`'s import of `run_scan_workflow_task` (only
the Celery task *object*, never executing that module's own function
bodies from the API process).

**Design decisions, all derivable from existing convention or explicit
instruction (none required stopping to report a blocker):**
1. Retrieval wiring lives in a new `tasks.py` helper,
   `_build_analysis_service(*, provider, qdrant_url)` -- takes an
   already-constructed `provider` (not `Settings` itself) so the
   existing `assert settings.anthropic_api_key is not None` mypy-strict
   narrowing pattern a few lines above stays effective (mypy does not
   carry an `assert`'s narrowing across a function call).
2. `qdrant_url` stays **unvalidated** in `check_role_boundaries` --
   unlike `anthropic_api_key`, requiring it would be a bigger behavior
   change than Phase 5 has actually made: `AnalysisService`'s retrieval
   is optional-by-design (Milestone 4), so a scan can run, and always
   could, with no Qdrant configured at all. When `qdrant_url` is `None`,
   `_build_analysis_service` returns `AnalysisService(provider=
   provider)` -- the exact call shape used before this milestone.
3. `SentenceTransformerEmbeddingPort`/`QdrantVectorStorePort` are
   imported **lazily**, inside `_build_analysis_service`'s own
   RAG-configured branch, not at `tasks.py`'s top. Necessary because
   `app/api/dependencies.py` imports this module (for the task object)
   from the API process -- a top-level import would force `backend`'s
   image to have `sentence-transformers`/`qdrant-client` importable too,
   just to import a task definition whose body it never executes.
4. Collection name: `IngestCweTop25UseCase`'s own `CORPUS_NAME`
   (`"cwe_top25"`), imported directly, not duplicated as a literal.
5. Vector size: new `EMBEDDING_VECTOR_SIZE = 384` constant added to
   `sentence_transformer_provider.py` itself (co-located with
   `DEFAULT_MODEL_NAME`, since it's a fixed fact about that one locked
   model), imported by the wiring rather than hardcoded at the call
   site.
6. **TD #19 (CPU-only PyTorch) resolution approach:** inspected whether
   the existing single-Dockerfile-plus-`pyproject.toml`-optional-groups
   architecture could support a clean `backend`-vs-`worker` dependency
   split without a larger deployment refactor -- concluded yes, since
   `pyproject.toml` already had a `dev` optional group precedent to
   extend. Implemented via a new `rag` optional-dependency group (moved
   `sentence-transformers`/`qdrant-client` out of the base
   `dependencies` list) plus a Dockerfile `ARG INSTALL_RAG_DEPENDENCIES`
   (default `false`) that, when `"true"`, installs `torch` from
   PyTorch's own CPU wheel index before `pip install ".[rag]"`.
   `docker-compose.yml`'s `worker` service passes that arg (plus
   `QDRANT_URL` and a `qdrant` `depends_on` entry); `backend` does not.
   `dev` self-references `security-platform-backend[rag]` (verified
   working in sandbox) so `pip install -e ".[dev]"` -- this project's
   one established local/CI command -- keeps working unchanged.
7. **`kb_version` decision (explicit instruction to investigate, not
   assume):** inspected the ORM model
   (`app/infrastructure/db/models/findings.py`) and the repository
   mapping (`add_analysis`/`_analysis_to_domain`) -- confirmed
   `kb_version` is a real, already-migrated, already-round-tripping
   column whose own docstring reads "nullable -- no RAG until Phase 5,"
   unambiguously identifying it as the intended persisted
   representation. Populated via a new `_build_kb_version` helper in
   `analysis_service.py`, mirroring `_build_retrieval_metadata`'s own
   first-match-payload read: `"<corpus>:<corpus_version>"` (e.g.
   `"cwe_top25:2025"`) when retrieval had a match, else `None`. Threaded
   through `FindingAnalysisResult` and `run_scan_workflow.py`'s
   `_persist`.

## Files changed
- `backend/app/workers/tasks.py` (edited) -- new
  `_build_analysis_service` helper, imports, call site, docstring.
- `backend/app/config.py` (edited) -- `qdrant_url` documented as
  deliberately unvalidated/usage-gated; pointer comment in
  `check_role_boundaries`.
- `backend/app/ai_agents/analysis_service.py` (edited) -- `kb_version`
  field, `_build_kb_version`, wired through `analyze()`/
  `_parse_completion`, docstring.
- `backend/app/application/scanning/run_scan_workflow.py` (edited) --
  `kb_version=item.ai_analysis.kb_version` added to `_persist`'s
  `FindingAnalysis(...)` construction.
- `backend/app/infrastructure/embeddings/sentence_transformer_provider.py`
  (edited) -- new `EMBEDDING_VECTOR_SIZE = 384` constant, docstring
  updated (now wired, not "deferred").
- `backend/app/infrastructure/vector_store/qdrant_vector_store.py`
  (edited) -- docstring updated to match (now wired, not "deferred").
- `backend/pyproject.toml` (edited) -- new `rag` optional group;
  `dev` self-references it.
- `backend/Dockerfile` (edited) -- `INSTALL_RAG_DEPENDENCIES` build arg,
  conditional CPU-only-PyTorch-first install path.
- `docker-compose.yml` (edited) -- `worker` gains the build arg,
  `QDRANT_URL`, and a `qdrant` `depends_on` entry; `qdrant`'s own
  comment updated; `backend` unchanged.
- `backend/tests/unit/test_tasks_composition.py` (new, 6685 bytes) --
  5 tests isolating `_build_analysis_service`.
- `backend/tests/unit/test_analysis_service.py` (edited) -- 4 new
  `kb_version` tests appended (37 tests total in that file now).
- `PROJECT_STATE.md`, `docs/implementation_progress.md`,
  `docs/session_state.md` (this file) -- all three updated.

**Confirmed untouched, by direct fresh-read comparison, not just by
claim:** `app/main.py`, `EmbeddingPort`, `VectorStorePort`, the Qdrant
adapter's actual logic (docstring only touched), `AIProviderPort`,
CWE ingestion, scanner adapters, `docker-compose.yml`'s `backend`
service and every network/security boundary (`internal`/`queue`/
`worker-egress`/`scan-egress`, `secure=True` cookies -- neither
mentioned nor touched).

## Verification -- exact results
- **Sandbox `pytest tests/unit/ -q` -> 82 passed, 0 failed** (73
  pre-existing across Milestones 1-4 + 9 new: 4 `kb_version` + 5
  `_build_analysis_service` composition tests). 100% coverage on
  `analysis_service.py`.
- **Ruff (lint + format) and MyPy `--strict`** both clean across the
  reconstructed sandbox package (71 files).
- Self-referential `rag` extras mechanism confirmed working in sandbox
  before being written to the real `pyproject.toml`.
- **Explicitly not claimed as verified, per instruction:**
  - `test_scan_worker_task.py` (the full Postgres-backed integration
    harness) was not re-run this session -- given the memory-drift risk
    above, faithfully reconstructing that harness plus its ~25-file
    dependency tree was judged too failure-prone to trust as a signal;
    the new `test_tasks_composition.py` covers the actual new logic
    directly, and every other file that harness touches was confirmed
    unchanged by direct diff review during the real edits.
  - No real Qdrant server was reachable this session (TD #20 unchanged
    -- same constraint as Milestone 2: no host port mapping, no network
    path from Claude's sandbox regardless).
  - No actual Docker build was run -- no command-execution tool via the
    Filesystem MCP, and Claude's own sandbox cannot build Docker images.
    The CPU-only-torch/image-split Dockerfile logic is standard,
    well-documented technique, not confirmed working end-to-end here.
  - The full real-repository test suite (~48 files) was not run against
    the actual repo -- no execution access exists against the real path
    directly; verification is sandbox-only, as always, but this
    session's sandbox coverage of the untouched surrounding system is
    narrower than some prior sessions' for the reason above.
  - Recommended: a real `docker build --build-arg
    INSTALL_RAG_DEPENDENCIES=true` for `worker`, and a full `pytest
    tests/ -q` run, in an environment with execution access (e.g.
    Claude Code).

## Pending work
- **TD #19**: updated to "addressed in code/config, not build-verified"
  -- needs a real `docker build` to close out.
- **TD #20**: unchanged -- still needs a reachable real Qdrant instance
  to verify against.
- **TD #21**: unchanged, untouched this session.
- Phase 5 as originally scoped is now complete (Milestones 1-5). Any
  further Phase 5 work (real-environment verification, retrieval
  tuning, a second knowledge source) is new scope, not a carry-over.
- Everything else is exactly where the tenth session left it: TD #18
  open; SQLMap pipeline/API wiring not started; Phase 3 frontend Step 5
  awaiting a scope decision; the two orphaned `reconx`/`bughunter` stub
  folders still not deletable -- see `PROJECT_STATE.md` §12/§13 for the
  current, authoritative list, not re-duplicated here since nothing
  about those items changed this session.

## Next immediate task
No further Phase 5 milestone is defined -- per instruction, stopped
after Milestone 5, no Phase 6 or unrelated work begun. The two
concrete, mechanical follow-ups this session itself identified (a real
`docker build` to close TD #19; a reachable Qdrant instance to close TD
#20) are verification work, not new implementation, and were left for
an environment that can actually perform them. Also outstanding,
independent of Phase 5 and unaffected by this session: Step 5's scope
decision, a future SQLMap pipeline/API wiring session, TD #18, and the
`reconx`/`bughunter` manual-deletion item.
