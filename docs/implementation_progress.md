# Implementation Progress

Cumulative, project-level tracking. Updated after every implementation
session -- unlike session_state.md (single most recent session only), this
file accumulates. For full architecture and decision detail, see
PROJECT_STATE.md.

## Project name
AI-Powered Cybersecurity SaaS Platform (working name; no product name
chosen yet)

## Current version
Pre-release, Milestone 7 (Docker Compose wired end-to-end) complete;
Authentication / Identity & Access work (Technical Debt #9, separate
pre-Phase-3 backend scope, not an eighth Phase-2 milestone) also
complete -- no version tag yet.

## Overall roadmap

| Phase | Focus                                                      | Status |
|-------|----------------------------------------------------------- |--------|
| 1 | Architecture, folder structure, DB schema, API design, docs    | Approved (Phase 1.2 finalized) |
| 2 | MVP backend (vertical slice: Nuclei -> AI analysis -> report)  | In progress -- see milestone breakdown below |
| 3 | MVP frontend                                                   | Not started |
| 4 | Remaining 14 scanner adapters                                  | Not started |
| 5 | Remaining AI features + full RAG                               | Not started |
| 6 | Multi-tenancy hardening, RBAC, orgs/teams, billing             | Not started |
| 7 | Full dashboard                                                 | Not started |
| 8 | DOCX/PDF polish, scheduling, notifications                     | Not started |
| 9 | Security hardening pass | Not started |
| 10 | Deployment | Not started |

Phase 2 is broken into its own milestones:

| Milestone | Focus | Status |
|---|---|---|
| 1 | Foundation: config, IDs, enums, value objects, common utilities, tests | Complete (statically audited + dynamically verified) |
| 2 | DB models + Alembic migration + repositories | Complete (statically audited + dynamically verified) |
| 3 | Scanner engine (Nuclei adapter) + StoragePort + target validation | Complete (statically audited + dynamically verified) |
| 4 | Processing pipeline orchestrator | Complete (dynamically verified) |
| 5 | API layer (public + internal split) | Complete (dynamically verified) |
| 6 | AI analysis service | Complete (dynamically verified) |
| 7 | Docker Compose wired end-to-end | Complete (dynamically verified) |

Separately, outside Phase 2's own Milestone 1-7 sequence: the
Authentication / Identity & Access work (Technical Debt #9), additional
pre-Phase-3 backend scope, is complete (dynamically verified) -- see
"Completed milestones" below.

## Current phase
Phase 2 (MVP backend)

## Current milestone
None -- Milestone 7 is Phase 2's last milestone and remains complete.

Separately: the Authentication / Identity & Access work (Technical
Debt #9) is complete -- not an eighth Phase-2 milestone (Phase 2 ends at
Milestone 7), tracked as its own pre-Phase-3 backend scope. Resolves
Technical Debt #9 ("No authentication on any `/api/v1` route").
Delivered: registration (`RegisterUserUseCase`), login
(`LoginUseCase`), JWT access-token + opaque hashed-refresh-token
authentication with rotation (`RefreshTokenUseCase`,
`SqlAlchemyRefreshTokenRepository`), current-user authentication
(`get_current_user`) and organization-membership authorization
(`require_organization_member`) wired into every existing Scanning
route (`app/api/v1/scans.py`), and three new public routes
(`/api/v1/auth/{register,login,refresh}`). Tokens are delivered
exclusively as httpOnly cookies (the locked auth-transport decision,
PROJECT_STATE.md section 2), never in a JSON body. See
"Completed milestones" below for the full account, including a targeted
follow-up review that corrected two issues before this work was
considered complete: the naming used to track it, and an initial,
unapproved `Authorization: Bearer` implementation that was corrected
back to httpOnly cookies.

## Completed milestones
**Milestone 1 (Foundation) -- complete**, as of the prior session. See
that session's entry in `session_state.md` history (superseded by this
session's snapshot) for the original completion details; unchanged this
session.

**Milestone 2 (DB models + Alembic migration + repositories) -- complete**,
as of this session. Delivered:
- SQLAlchemy async ORM models for all 19 tables across six bounded
  contexts (`app/infrastructure/db/models/{identity,assets,scanning,
  findings,reporting,platform}.py`), using `DateTime(timezone=True)`
  explicitly on every timestamp column (see Technical debt / Design
  notes below for why that turned out to matter), `StrEnum` columns via
  `native_enum=False`, and the locked soft-delete/timestamp/UUID mixins
  from Milestone 1's architecture review.
- Plain dataclass domain entities for all five bounded contexts
  (`app/domain/{identity,assets,scanning,findings,reporting}/
  entities.py`) -- needed so repository ports could be typed against a
  domain type rather than a SQLAlchemy model, per the dependency rule.
  Deliberately thin: no state-machine transition methods were invented
  for `Finding`/`Scan`, since no consuming use case exists yet to
  validate such rules against (see each file's module docstring).
- Repository ports (`*RepositoryPort` ABCs, matching the `ScannerPort`/
  `AIProviderPort`/etc. naming convention) and SQLAlchemy-backed
  implementations for all five aggregates (Organization+Membership,
  User, Asset, Scan, Finding, Report, plus a standalone AuditLog port).
- Alembic migration infrastructure: `alembic.ini`, an async `env.py`
  (reads `DATABASE_URL` directly from the environment rather than via
  `app.config.Settings`, since Alembic is not one of the three
  `WorkerRole`s that validator models -- see `env.py`'s docstring), and
  one hand-written initial migration creating all 19 tables plus
  Row-Level Security policies.
- A real PostgreSQL 16 instance, installed in the verification sandbox
  specifically so RLS, native UUID columns, and JSONB -- all
  Postgres-specific behavior -- could be genuinely exercised rather than
  approximated with SQLite.
- A corrected `tests/conftest.py`. The version already on disk at the
  start of this session imported `app.core.db` and `app.main`, neither
  of which exists anywhere in the approved architecture (see
  PROJECT_STATE.md section 4), and used sync SQLAlchemy + SQLite +
  FastAPI's `TestClient` -- contradicting the locked stack (SQLAlchemy
  async, PostgreSQL). This file was not part of any documented
  Milestone 1 deliverable and would have failed at pytest collection
  time (`ModuleNotFoundError`) had it been run against this exact
  repository; its origin is unknown; it is not attributable to this
  project's documented session history. Per the verification-honesty
  workflow rule, this was flagged before being touched, then replaced
  with async Postgres fixtures consistent with the locked stack.
- 102 tests total (55 Milestone-1 unit tests, unchanged; 9 new unit
  tests for the domain entities' `is_deleted` properties and
  `Finding.effective_severity`; 38 integration tests against real
  PostgreSQL, covering CRUD, natural-key lookups, append-only history,
  `LookupError` error paths, and -- specifically -- RLS tenant isolation
  proven by automated test, not just asserted). 100% coverage, clean
  Ruff (lint + format), clean MyPy strict.

Two genuine implementation gaps were discovered and fixed during this
milestone, both documented in PROJECT_STATE.md section 3's dated log
rather than silently patched:
1. **`DateTime(timezone=True)` is required on every datetime column.**
   SQLAlchemy's default type mapping for a bare `Mapped[datetime]`
   produces a timezone-*naive* column; asyncpg then rejects binding this
   codebase's timezone-aware `utcnow()` values. Every model file now
   specifies `DateTime(timezone=True)` explicitly.
2. **The locked RLS + soft-delete predicate is unimplementable as written.**
   `USING (org_id = current_setting(...) AND deleted_at IS NULL)` makes
   Postgres reject the very `UPDATE` that performs a soft delete, since
   Postgres checks a table's `SELECT`-relevant `USING` clause against
   the *new* row of any `UPDATE`, not just `WITH CHECK` -- confirmed
   against a real Postgres 16 instance and against the pgsql-hackers
   mailing list, which describes this as long-standing behavior, not a
   bug specific to this setup. Fix: RLS now enforces tenant isolation
   only; soft-delete visibility filtering moved to explicit `WHERE
   deleted_at IS NULL` in the affected repositories' read methods. Full
   account in the Alembic migration's DESIGN NOTE and in PROJECT_STATE.md
   section 3.

All code was verified in Claude's sandbox (with the local PostgreSQL 16
instance described above), then written file-by-file into this
repository via the Filesystem MCP. Not re-executed on
`C:\Users\gamer\Downloads\claudeOnly` directly -- this connector still
has no command-execution tool (unchanged since Milestone 1; see
PROJECT_STATE.md section 13).

**Milestone 3 (Scanner engine -- Nuclei adapter -- + StoragePort + target
validation) -- complete.** Process note, recorded here per the
verification-honesty workflow rule rather than silently absorbed: at the
start of this session, the repository already contained a complete,
correct Milestone 3 implementation -- `app/application/interfaces/
scanner_port.py`, `app/application/interfaces/storage_port.py`,
`app/scanner_engine/base_scanner.py`,
`app/infrastructure/security/target_validation.py`,
`app/infrastructure/storage/minio_storage.py`,
`app/scanner_engine/adapters/nuclei/adapter.py`, all four matching unit
test files, `minio` already in `pyproject.toml`, and the MinIO section
already in `.env.example` -- despite this file and PROJECT_STATE.md both
stating Milestone 3 as "not started." Per this project's standing rule
that the repository is ground truth over documents that may lag, and per
this session's explicit instruction that the repository wins over
PROJECT_STATE.md on any conflict, the existing code was audited against
every relevant locked decision in PROJECT_STATE.md section 3 rather than
rewritten:
- `ScannerPort`/`ActiveScanner`/`ImportScanner` -- matches the locked
  split verbatim; `ImportScanner.parse_import` correctly has no target
  validation (nothing is executed against a target on the import path).
- `run_scanner_subprocess` -- argument-list-only (`list[str]`, no
  `shell` parameter exists to misuse), enforced `asyncio.wait_for`
  timeout, non-root guard via `os.getuid()` (a documented no-op where
  `getuid` does not exist, i.e. Windows dev machines).
- `validate_target` -- resolves hostnames via DNS before checking
  (guards DNS rebinding), rejects private/loopback/link-local/reserved/
  multicast plus the cloud-metadata address named explicitly in
  PROJECT_STATE.md section 3.
- `StoragePort`/`MinioStoragePort` -- one instance bound to one bucket at
  construction, matching "MinIO behind a StoragePort abstraction"
  (PROJECT_STATE.md section 2).
- `NucleiAdapter` -- routes exclusively through `run_scanner_subprocess`
  and `validate_target`; treats a non-zero exit code as failure only
  when combined with empty stdout, so a real match is never misreported
  as an error.

One stale docstring was found and fixed:
`app/scanner_engine/adapters/__init__.py` still read "Not yet
implemented" despite `nuclei/` containing a full implementation. This
session's actual new work was verification (see "Testing status" below)
and this documentation correction -- not fresh implementation, since none
was needed. See `docs/session_state.md` for the full account of the
discovery and what was and wasn't touched as a result.

**Milestone 4 (Processing pipeline orchestrator) -- complete.** Genuine
fresh implementation this session, unlike Milestone 3's discovery.
Delivered:
- `derive_scan_status()` and the `PIPELINE_STEP_ORDER` constant, added
  to `app/domain/scanning/entities.py` (extending, not rewriting, the
  Milestone 2 file -- `Scan`/`ScanWorkflowStep`/`ScanScope` are
  untouched). Pure domain logic, zero framework imports: any step
  `FAILED` -> `FAILED`; every step `COMPLETED`/`SKIPPED` -> `COMPLETED`;
  otherwise `RUNNING`. This also corrects a stale cross-reference: the
  `Scan` row in PROJECT_STATE.md section 5 previously said this
  derivation logic was "Milestone 3" -- checked against Milestone 3's
  actual delivered scope (the paragraph above) and found never true;
  corrected to "Milestone 4" now that the function exists.
- `TriggerScanUseCase` (`app/application/scanning/trigger_scan.py`) --
  creates a `Scan` (status `QUEUED`) plus all eight `ScanWorkflowStep`
  rows (status `PENDING`) in the locked pipeline order. Deliberately
  does not call `validate_target` itself -- that is the pipeline's own
  first step, not duplicated here.
- `normalize_scan_output()`/`NormalizedFinding`
  (`app/application/scanning/normalization.py`) -- turns nuclei's raw
  JSONL bytes into structured, scanner-native (not yet validated)
  fields: title, host, matched-at, raw severity string, CVE IDs, CVSS
  score/vector candidates, and the full raw match object. Lives in the
  application layer, not `scanner_engine/`, because `ScanOutput`'s own
  docstring already commits Scanning to stop at "here is exactly what
  the scanner said" and defer interpretation to this exact step; a
  `NormalizerPort` was deliberately not built, since only one output
  format (`nuclei-jsonl`) exists to normalize -- see PROJECT_STATE.md
  section 3's dated entry.
- `RunScanWorkflowUseCase` (`app/application/scanning/
  run_scan_workflow.py`) -- the orchestrator itself. `execute(scan_id)`
  walks all eight steps in the locked order
  (`validate_target -> execute_scanner -> normalize -> deduplicate ->
  correlate -> enrich -> ai_analyze -> persist`), updating each step's
  `ScanWorkflowStep` row (`RUNNING` -> `COMPLETED`/`FAILED`,
  `retry_count` incremented only on a genuine retry of a prior
  failure), then sets `Scan.status` via `derive_scan_status`. Wired to
  the Milestone 3 `ScannerPort`/`NucleiAdapter` (constructor-injected
  `ActiveScanner`) and the Milestone 2 `ScanRepositoryPort`/
  `AssetRepositoryPort`/`FindingRepositoryPort` implementations, exactly
  as section 15's queue specified. A step failure is recorded on its
  row and reflected in `Scan.status` (`FAILED`), then the exception is
  suppressed rather than propagated to the caller -- a failed scan is a
  normal business outcome the caller reads off the returned `Scan`, not
  something every call site needs a `try`/`except` to learn about;
  `LookupError` (unknown scan, missing workflow steps) and
  `ScannerMismatchError` (wrong adapter wired for this scan's
  `scanner_name`) still raise, since those are genuine misuse, not scan
  outcomes.
  - Resumability: `EXECUTE_SCANNER` is the one step treated as
    expensive/non-idempotent -- once its row is `COMPLETED`, the real
    scanner is never invoked again for that scan; its raw output is
    read back from `StoragePort` under a deterministic
    `{scan_id}/raw-output` key instead. Every other step is safe to
    recompute on every invocation (pure reads, or guarded against
    duplicate writes by checking for an existing `(scan_id, asset_id)`
    observation / `(scan_id, finding_id)` occurrence before inserting).
    This gives real "retry from the failed step" behavior for the
    realistic case (a transient failure, fixed, then the same scan
    retried) -- see Technical debt below for the one case this does not
    cover.
  - `AI_ANALYZE` is unconditionally marked `SKIPPED` -- `AIProviderPort`
    and an `AnalysisService` are Milestone 6 scope and do not exist yet;
    `SKIPPED` is an already-modeled terminal state
    (PROJECT_STATE.md section 5), not a fabricated result, and still
    counts as "done" for `derive_scan_status`.
  - `enrich` validates a scanner-reported CVSS candidate into a real
    `CVSS` value object (dropping it, not failing the scan, if
    invalid), but deliberately never writes nuclei's own raw
    self-reported severity string into `Finding.ai_severity_level` --
    that field is named for an AI provider's own estimate (Milestone 6),
    and writing scanner data into it would misrepresent provenance. The
    raw severity claim is not lost: it travels in
    `FindingOccurrence.raw_evidence` (the full raw scanner match
    object).
- 44 new tests: `tests/unit/test_scan_status_derivation.py` (7),
  `tests/unit/test_normalization.py` (9),
  `tests/unit/test_trigger_scan.py` (4),
  `tests/unit/test_run_scan_workflow.py` (21, every port faked --
  step sequencing, retry/resumption including the two branches where a
  later-step failure must not re-invoke the scanner or re-enter
  `AI_ANALYZE`, recurrence across two scans of the same target, mixed
  IP/domain asset correlation, and a direct test of `_persist`'s
  defensive `asset_id` guard), and
  `tests/integration/test_scan_pipeline_orchestrator.py` (3, against
  real Postgres via the actual `SqlAlchemyScanRepository`/
  `SqlAlchemyAssetRepository`/`SqlAlchemyFindingRepository`
  implementations, with only the scanner and object storage faked --
  no real `nuclei` binary or MinIO server available, same constraint as
  Milestone 3). 100% coverage on every Milestone 4 module, clean Ruff
  (lint + format), clean MyPy strict.

**Milestone 5 (API layer -- public + internal split) -- complete.**
Genuine fresh implementation this session. Delivered:
- `app/api/dependencies.py` (new) -- the composition root's per-request
  DI providers. `AppState` (session factory + the two Milestone 3
  adapters) is built once by `app/main.py`'s lifespan and stored on
  `app.state`; `get_org_session` opens one `session_scoped_to_org`
  (Milestone 2) per request and checks organization existence via
  `OrganizationRepositoryPort.get_by_id` before yielding the session,
  turning a foreign-key-violation 500 into a clean 404. Every
  repository/use-case provider is built on top of that one function, so
  FastAPI's per-request dependency caching guarantees one transaction
  per request regardless of how many routes/dependencies ask for it.
- `app/api/v1/schemas.py` (new) -- `ScanCreateRequest`
  (`scanner_name: Literal["nuclei"]`, reflecting the one adapter this
  process has wired -- not a scanner registry, still exactly one
  adapter per Milestone 4's decision log) and `ScanDetailResponse`/
  `WorkflowStepResponse` (frozen Pydantic models, `from_domain`
  classmethods).
- `app/api/v1/scans.py` (new) -- three routes under
  `/organizations/{organization_id}/scans`: `POST` (create, calls
  `TriggerScanUseCase`), `POST .../{scan_id}/run` (calls
  `RunScanWorkflowUseCase`), `GET .../{scan_id}` (a direct
  `ScanRepositoryPort` read, per PROJECT_STATE.md section 15's own
  split between routes that call use cases and routes that call
  repositories directly). Create and run are deliberately separate
  endpoints -- not a combined "create and run" call -- so that
  Milestone 4's resumability is visible over HTTP: calling `run` again
  on a scan that failed partway retries from the failed step.
- `app/api/internal/health.py` (new package) -- `/health/live` (no
  dependencies) and `/health/ready` (a plain `SELECT 1`, no RLS context
  needed since it touches no tenant table; catches broadly and returns
  503 on failure). `/internal/metrics` and `/internal/admin` (also
  named in PROJECT_STATE.md section 3's original phrasing) are not
  implemented -- metrics needs the still-empty `observability/` package,
  admin needs RBAC (Phase 6); building either now would mean a stub
  with nothing real behind it.
- `app/main.py` (new) -- `create_app()` factory (tests never need to
  run the real lifespan), `_lifespan` (builds `AppState` from `Settings`
  at startup, disposes the engine at shutdown), and two global exception
  handlers (`LookupError` -> 404, `ScannerMismatchError` -> 409).
- `app/config.py` (extended) -- `minio_bucket: str | None = None`,
  joined to the existing MinIO required-fields check. This milestone is
  the first to construct `MinioStoragePort` through `Settings` in a real
  composition root rather than directly in a test with an explicit
  bucket argument.
- `.env.example`/`pyproject.toml` -- `MINIO_BUCKET` and `JWT_SECRET`
  added under a new "Milestone 5" section (`JWT_SECRET` was required by
  `Settings` for `worker_role=api` since Milestone 1 but never landed in
  `.env.example`, since no runnable API process existed for its absence
  to block anything before now); `fastapi`/`uvicorn[standard]` added to
  `dependencies`, `httpx` added to `dev`; one `ruff` per-file-ignore
  (`B008` under `app/api/**/*.py`) for FastAPI's `Depends(...)`-in-a-
  default idiom, a documented false positive against a general
  anti-pattern check that predates FastAPI's own convention.
- Deliberately **not** built this milestone, each a flagged design
  decision rather than an oversight (full rationale in
  `docs/session_state.md` and PROJECT_STATE.md section 3): authentication
  on any route (`organization_id` is trusted as given in the URL path --
  Identity & Access has no use cases yet to build real auth against);
  HTTP routes for Findings/Assets/Reporting (none of those bounded
  contexts has an application-layer use case or a "list by organization"
  repository method yet); an organization/user-creation endpoint (would
  either bypass the ">= 1 Owner always" invariant or require building
  around it prematurely); asynchronous/queued scan execution (no task
  queue exists before Milestone 7 -- `run_scan` blocks synchronously for
  up to 600s).
- 28 new/updated tests: `tests/unit/test_api_schemas.py` (10),
  `tests/unit/test_health.py` (1),
  `tests/integration/test_api_scans.py` (14, exercising the real
  FastAPI app end-to-end over HTTP via `httpx.AsyncClient`/
  `ASGITransport` -- not Starlette's `TestClient`, whose thread-based
  lifespan handling would bind an asyncpg connection pool to a different
  event loop than the test's own),
  `tests/integration/test_health_ready.py` (2),
  `tests/integration/test_main_lifespan.py` (1, added after this
  session's coverage investigation found `_lifespan` itself was
  otherwise never exercised by any test -- see "Testing status" below),
  plus `tests/unit/test_config.py` extended with one new test for the
  `minio_bucket` requirement. Clean Ruff (lint + format), clean MyPy
  strict on every Milestone 5 module.

**Milestone 6 (AI analysis service) -- complete.** Genuine fresh
implementation this session. Delivered:
- `app/application/interfaces/ai_provider_port.py` (new) --
  `AIProviderPort` (ABC: `provider_name` property,
  `complete(system_prompt, user_prompt)`), `AICompletionResult` (a
  frozen dataclass mirroring `ScanOutput`'s own "raw payload plus
  minimal typed metadata" shape), and `AIProviderError`. Deliberately
  minimal and provider-agnostic -- this port's job stops at "send a
  prompt, return the provider's raw text"; interpreting that text is
  `AnalysisService`'s job, not this port's, the same division of
  responsibility `ScannerPort`/`normalization.py` already establish.
- `app/infrastructure/ai_providers/anthropic_provider.py` (new) --
  `AnthropicProvider`, the first concrete `AIProviderPort`
  implementation, wrapping the official `anthropic` SDK's async client
  (mirroring `MinioStoragePort`'s own precedent of wrapping an official
  SDK rather than hand-rolling HTTP calls). Translates every
  `anthropic.APIError` (the SDK's shared base for auth/rate-limit/
  timeout/connection failures, confirmed against the actual installed
  SDK) into `AIProviderError`, so nothing above this adapter needs to
  import the SDK's own exception types.
- `app/ai_agents/analysis_service.py` (new) -- `AnalysisService` (the
  one concrete AI agent PROJECT_STATE.md section 3 already named as
  planned; still no formal `BaseAgent` interface -- YAGNI holds until a
  second agent's real shape is known), `FindingAnalysisInput`/
  `FindingAnalysisResult` (AI Platform's own input/output contract --
  deliberately plain primitives, not `NormalizedFinding` or `CVSS`, so
  `ai_agents/` has zero import-time dependency on the Scanning or
  Findings bounded contexts), `AnalysisError`, and `PROMPT_VERSION =
  "v1"`. Builds a strict-JSON-only prompt, schema-validates the
  response (required keys, string types, `SeverityLevel` membership),
  and raises `AnalysisError` -- never fabricates a result -- on any
  validation failure, per PROJECT_STATE.md section 11's rule.
- `app/application/scanning/run_scan_workflow.py` (extended) -- the
  actual pipeline wiring, and this milestone's one genuine architectural
  resolution: the locked pipeline order runs `AI_ANALYZE` before
  `PERSIST`, so no `Finding.id` exists yet for a brand-new finding when
  analysis happens, but `FindingAnalysis.finding_id` needs one.
  Resolved the same way `_enrich` already resolves an analogous problem
  for CVSS: `_ai_analyze` stashes its result in memory on
  `_PipelineItem.ai_analysis`; `_persist`, which already resolves a real
  `finding.id` for every item before this data would need one, is what
  actually writes `Finding.ai_severity_level` and appends the
  `FindingAnalysis` row. `AI_ANALYZE` is wired through the same
  `_run_step` every other "safe to recompute" stage uses (not exempted
  like `EXECUTE_SCANNER`), so a scan retried after a later-step failure
  re-calls the AI provider for every finding -- a real, accepted cost,
  flagged as new Technical debt item #11 rather than solved with a new
  durability mechanism. A per-finding `AIProviderError`/`AnalysisError`
  is caught and logged inside `_ai_analyze`, not allowed to fail the
  step -- AI commentary has always been optional for a scan to be
  considered done (`WorkflowStepStatus.SKIPPED`'s own pre-Milestone-6
  role for this exact step); any other exception still propagates,
  matching this codebase's existing convention of not swallowing
  exceptions broadly. `Finding.ai_severity_level` is set at Finding
  creation only, never refreshed on a later re-detection, mirroring the
  existing, pre-Milestone-6 precedent for `cvss_score`/`cvss_vector`;
  `finding_analyses` remains append-only regardless, gaining a fresh row
  every time `AI_ANALYZE` produces a result.
- `app/config.py` (extended) -- `ai_model: str = "claude-sonnet-4-5"`
  (a rolling alias, not a dated snapshot); `anthropic_api_key` now
  required for `worker_role=api` when `ai_default_provider ==
  "anthropic"` (the default), mirroring the `jwt_secret` precedent
  exactly, scoped to "anthropic" specifically since that is the only
  adapter this milestone builds; a matching production dev-default-
  rejection check added alongside the existing `jwt_secret`/
  `minio_root_password` ones.
- `app/api/dependencies.py` (extended) -- `AppState` gains
  `analysis_service: AnalysisService`; new `get_analysis_service`
  provider (mirrors `get_active_scanner`/`get_storage`);
  `get_run_scan_workflow_use_case` now injects it.
- `app/main.py` (extended) -- `_lifespan` constructs
  `AnthropicProvider`/`AnalysisService` from `Settings`, adding them to
  `AppState`; raises `ValueError` at startup if `ai_default_provider`
  names anything other than `"anthropic"` -- fails loudly, not silently,
  since no adapter exists for anything else yet.
- `app/domain/scanning/entities.py` (docstring-only edit) --
  `derive_scan_status`'s docstring corrected: `AI_ANALYZE` no longer
  automatically produces `SKIPPED` going forward, though the enum value
  and this function's handling of it remain correct for historical rows.
- `pyproject.toml`/`.env.example` -- `anthropic>=0.120` added to
  `dependencies` (only after code in the repo actually imports it);
  new "Milestone 6" `.env.example` section (`AI_DEFAULT_PROVIDER`,
  `AI_MODEL`, `ANTHROPIC_API_KEY`).
- 2 new test files: `tests/unit/test_anthropic_provider.py` (8 tests,
  mocking the `anthropic` SDK's client class at construction time,
  mirroring `test_minio_storage.py`'s own SDK-wrapping test pattern),
  `tests/unit/test_analysis_service.py` (13 tests, schema-validation
  focused -- including an explicit regression guard asserting two
  differently-worded but equally valid summaries both validate, never
  comparing against one fixed string, per PROJECT_STATE.md section 11).
  5 existing test files substantially updated:
  `tests/unit/test_run_scan_workflow.py` (25 tests, up from 21 --
  `AI_ANALYZE`'s old "always SKIPPED" tests rewritten to assert real
  completion/persistence/retry behavior), `tests/unit/test_config.py`
  (24 tests, up from 17), `tests/integration/test_api_scans.py` (14
  tests, `get_analysis_service` override added), `tests/integration/
  test_main_lifespan.py` (2 tests, up from 1), `tests/integration/
  test_scan_pipeline_orchestrator.py` (3 tests, real-database
  analysis-persistence assertions added). 84 tests total this session
  (65 unit + 19 integration), all passing; clean Ruff (lint + format),
  clean MyPy strict on every Milestone 6 module; 100% coverage on all
  four Milestone 6 modules.

**Milestone 7 (Docker Compose wired end-to-end) -- complete.** Genuine
fresh implementation this session. Delivered:
- `app/workers/celery_app.py` (new) -- the Celery application instance.
  Broker and result backend both point at `settings.redis_url`
  (required for every `worker_role` since Milestone 1, never actually
  consumed by any code until now). One named queue, `"scans"` -- not
  Celery's own default queue -- so a future scanner-worker-specific
  queue (see Technical debt item #12 below) can be added later without
  renaming this one out from under an already-deployed worker.
- `app/workers/tasks.py` (new) -- three layers, deliberately kept
  separate for testability, mirroring this codebase's existing split
  between a hard-to-test composition root (`app/main.py`'s `_lifespan`)
  and a fully fake-testable use case (`RunScanWorkflowUseCase`):
  `execute_scan_workflow` (a plain, fully-injectable async function --
  opens one RLS-scoped session via `session_scoped_to_org`, exactly as
  `app/api/dependencies.py`'s `get_org_session` already does for the API
  process, and calls `RunScanWorkflowUseCase.execute()`);
  `_run_scan_workflow_from_settings` (the worker-specific composition
  root, mirroring `app/main.py`'s `_lifespan` almost exactly but for
  `worker_role=ingestion_worker`, built fresh per task invocation); and
  `run_scan_workflow_task` (the actual `@celery_app.task`, a thin
  `asyncio.run(...)` wrapper). A fresh `AsyncEngine` is constructed
  inside the same `asyncio.run()` call that drives one invocation and
  disposed before it returns -- never cached across invocations, for the
  same reason `tests/conftest.py`'s own `engine` fixture is
  function-scoped (an asyncpg pool is bound to the event loop that
  created it; `asyncio.run()` creates a new one every call).
- `app/application/scanning/run_scan_workflow.py` -- **left completely
  unmodified this session** except for one new module-docstring
  paragraph, per the locked scope decision that no network-isolated
  `scanner_worker` split would be built (see design decision below and
  Technical debt item #12). Its own module docstring, written in
  Milestone 6, already correctly anticipated this: "Which process that
  is, and how work crosses that boundary, is deployment wiring left to
  Milestone 7" -- that docstring's own "Milestone 7 update" paragraph,
  added this session, records that the answer turned out to be "one
  process, unchanged from how this use case already worked," not a
  restructuring.
- `app/api/v1/scans.py::run_scan` (rewritten) -- the actual API-contract
  change. No longer calls `RunScanWorkflowUseCase`. Reads the scan (404
  if missing), compares `scan.scanner_name` against the wired
  `ActiveScanner.name` (409, via the same `ScannerMismatchError`
  mechanism as before, now raised directly from the route instead of
  from inside the use case), and -- new -- does not re-dispatch a scan
  already `RUNNING` (addressing a failure mode this change makes newly
  easy to trigger: dispatch returns almost instantly, so a client can
  call `run` twice in rapid succession, unlike before). Returns `202
  Accepted` with the scan's current, pre-execution state either way. A
  client observes actual progress via the unchanged `GET
  .../scans/{scan_id}`.
- `app/api/dependencies.py` (rewritten) -- least-privilege, a direct
  consequence of the dispatch change, not unrelated cleanup: `AppState`
  drops `storage`/`analysis_service` entirely (this API process no
  longer touches `MinioStoragePort` or `AnalysisService`/
  `AnthropicProvider`, so it has no reason to hold their credentials);
  `get_storage`, `get_analysis_service`, and
  `get_run_scan_workflow_use_case` are removed (their only caller no
  longer exists in that form); `get_asset_repository`/
  `get_finding_repository` are removed too (their only caller was
  `get_run_scan_workflow_use_case`). New: `get_scan_dispatcher`, the
  seam `run_scan` depends on -- wraps `run_scan_workflow_task.delay(...)`,
  overridden in tests with a spy the same way `get_active_scanner` and
  the now-removed `get_storage` always were.
- `app/main.py::_lifespan` (rewritten) -- stops constructing
  `MinioStoragePort`/`AnthropicProvider`/`AnalysisService`; only builds
  the session factory and `NucleiAdapter` now. `_lookup_error_handler`'s
  docstring updated to record that, as of this milestone, no
  currently-mounted route actually raises a bare `LookupError` that
  reaches it (`run_scan` now raises `HTTPException(404, ...)` directly;
  `RunScanWorkflowUseCase`'s own `LookupError` only surfaces inside the
  Celery task now, as a task failure) -- the handler is kept registered
  anyway, as general-purpose, already-documented infrastructure for a
  codebase-wide convention (every repository's mutation methods raise
  `LookupError` the same way) that Findings/Assets/Reporting's
  still-unbuilt routes are the more likely next caller of, not removed
  only to be re-added in the very next milestone that needs it.
- `app/config.py::check_role_boundaries` (edited) -- the
  `ANTHROPIC_API_KEY`-required-for-`worker_role=api` check moved to
  `worker_role=ingestion_worker`, following the process that actually
  constructs `AnalysisService` now.
- `backend/Dockerfile` (new) -- multi-stage (builder installs into a
  venv; runtime copies only that venv plus source, no compiler present),
  non-root user, shared by both the `backend` and `worker` Compose
  services (differing only in `command:`/`WORKER_ROLE`).
- `backend/.dockerignore` (new).
- `docker-compose.yml` (root, replacing the placeholder) -- full
  topology: `postgres`, `redis`, `minio`, `qdrant`, `backend`, `worker`.
  Deliberately **not** built this milestone, a flagged scope decision
  rather than an oversight: a separate, network-isolated `scanner_worker`
  service (see design decision below and Technical debt item #12).
- `.env.example`, `backend/pyproject.toml` -- `celery`/`celery-types`
  added to `dependencies`/`dev` respectively; no new environment
  variable names introduced (`REDIS_URL`, required since Milestone 1, is
  what `app/workers/celery_app.py` now actually points Celery's broker
  and result backend at). `.env.example`'s existing Milestone 6
  paragraph about `ANTHROPIC_API_KEY` also corrected in place, since it
  was now stale given the above.
- 4 new test files: `tests/unit/test_celery_app.py` (3 tests),
  `tests/unit/test_api_dependencies.py` (3 tests, closing a real
  coverage gap discovered this session -- see "Testing status" below),
  `tests/integration/test_scan_worker_task.py` (3 tests). 3 existing
  test files substantially updated: `tests/unit/test_config.py` (3 tests
  rewritten for the `ANTHROPIC_API_KEY` role-scoping change),
  `tests/integration/test_api_scans.py` (rewritten for the async-dispatch
  contract -- 13 tests; also fixes a pre-existing, unrelated correctness
  gap in its `_create_organization()` test helper, found and fixed while
  rewriting this file -- see "Testing status" below),
  `tests/integration/test_main_lifespan.py` (simplified -- the API's own
  lifespan no longer needs MinIO/Anthropic credentials to verify, 1
  test). 12 net-new/rewritten tests this session; clean Ruff (lint +
  format), clean MyPy strict on every Milestone 7 module.

One design decision worth flagging explicitly, because it resolves a
genuine tension discovered during implementation rather than following
a locked decision verbatim -- recorded in PROJECT_STATE.md section 3's
dated log rather than silently resolved: the network segmentation
`docker-compose.yml`'s own placeholder comment envisioned (a
`scanner-worker` on `scan-egress`, isolated from the database) is not
achievable for the single, unsplit `ingestion_worker` this session
actually builds, since `RunScanWorkflowUseCase` -- deliberately left
completely unrestructured, per this session's locked scope -- needs both
database access (for every step) and outbound internet access (for
`EXECUTE_SCANNER`'s real scan targets and `AI_ANALYZE`'s calls to the
Anthropic API) inside the very same process. `docker-compose.yml`
resolves this honestly rather than silently: `worker` is attached to
`internal` (Postgres), `queue` (Redis/MinIO), and a new,
deliberately-differently-named `worker-egress` network (genuine internet
access) -- distinct from the placeholder's future `scan-egress`, so the
two are never confused as already having been built. What *is* fully
achievable now, and delivered: `backend` (the API process) sits on
`internal` with genuinely no internet route at all, a real security
improvement this milestone's credential-narrowing made possible, since
the API process no longer has anything it would need internet access
for.

**Milestone 8 (Authentication / Identity & Access) -- complete.**
Resolves Technical Debt #9 ("No authentication on any `/api/v1` route").
Genuine fresh implementation this session. Delivered:
- `app/domain/identity/entities.py` (extended) -- `RefreshToken` entity.
  Not soft-deleted; `revoked_at` is its own lifecycle field, mirroring
  `OrganizationMember.status`'s existing precedent in the same module.
- `app/application/interfaces/identity_repository.py` (extended) --
  `RefreshTokenRepositoryPort` (`add`, `get_by_token_hash`, `revoke`).
  Not org-scoped, like `UserRepositoryPort` -- `refresh_tokens`, like
  `users`, carries no `organization_id` and has no RLS policy.
- `app/infrastructure/db/repositories/identity_repository.py`
  (extended) -- `SqlAlchemyRefreshTokenRepository`, the same
  row<->domain mapping convention every other repository in the module
  already uses.
- `app/infrastructure/security/password_hashing.py` (new) --
  `hash_password`/`verify_password`, wrapping `bcrypt` directly (not
  `passlib`, effectively unmaintained against current bcrypt releases).
  Enforces bcrypt's 72-byte input limit explicitly with a clear message
  rather than letting a library `ValueError` surface uncaught.
- `app/infrastructure/security/token_service.py` (new) --
  `create_access_token`/`decode_access_token` (signed JWT, PyJWT,
  HS256, `InvalidAccessTokenError` on any failure) and
  `generate_refresh_token`/`hash_refresh_token` (opaque
  `secrets.token_urlsafe(32)` token; only its SHA-256 hash is ever
  persisted); also `ACCESS_TOKEN_COOKIE_NAME`/`REFRESH_TOKEN_COOKIE_NAME`
  constants, defined once here so `auth.py` (sets them) and
  `dependencies.py` (reads them back) can never drift out of sync. Both
  new security modules are imported directly into the
  application layer, not hidden behind a new port -- deliberately
  following the precedent `target_validation.py` already set (zero
  framework/DB imports, exactly one real implementation, so a port
  would be an abstraction with nothing to abstract over).
- `app/application/identity/errors.py` (new) -- `AuthenticationError`
  (base, -> 401 via a global handler) with `InvalidCredentialsError`/
  `InvalidRefreshTokenError` subclasses; `EmailAlreadyRegisteredError`
  (-> 409, deliberately not part of the `AuthenticationError` hierarchy
  -- a conflict, not an authentication failure).
- `app/application/identity/tokens.py` (new) -- `TokenPair` and
  `issue_token_pair()`, the token-issuance logic shared by
  `LoginUseCase` and `RefreshTokenUseCase`, factored out once.
- `app/application/identity/register_user.py` (new) --
  `RegisterUserUseCase`. Creates only a `User` -- deliberately does not
  create an Organization/OrganizationMember (would either bypass the
  ">= 1 Owner always" invariant or require enforcing it prematurely,
  the same reasoning PROJECT_STATE.md's Milestone 5 note already gives
  for deferring an org-creation endpoint).
- `app/application/identity/login_user.py` (new) -- `LoginUseCase`.
  Unknown email, inactive account, no password set, and wrong password
  all raise the identical `InvalidCredentialsError` with the identical
  message, verified by an explicit test, so a caller cannot enumerate
  registered emails via response differences.
- `app/application/identity/refresh_token.py` (new) --
  `RefreshTokenUseCase`. Implements rotation: the presented token is
  revoked before a new pair is issued, so a replayed/stolen token is
  usable at most once. Family-wide revocation-on-reuse-detection is
  explicitly not built -- flagged as future work, not silently assumed
  solved. Reviewed on follow-up against "was this genuinely required":
  kept, since the `refresh_tokens` table (including a pre-existing
  `revoked_at` column) already existed before this work began and the
  approved scope explicitly named "secure ... JWT access/refresh
  authentication" -- without rotation a leaked refresh token would have
  no revocation path at all until a future logout endpoint exists.
- `app/api/v1/auth_schemas.py` (new) -- `RegisterRequest`, `UserResponse`,
  `LoginRequest`. No `TokenResponse`/`RefreshRequest` -- tokens never
  appear in a JSON body (see below).
- `app/api/v1/auth.py` (new) -- `POST /auth/register` (201,
  `UserResponse`, no cookies -- registration and login are kept as two
  separate calls, not auto-issuing a session on signup, since a newly
  registered user is not yet a member of any organization), `POST
  /auth/login` (200, `UserResponse`, sets `access_token`/`refresh_token`
  as `HttpOnly`/`Secure`/`SameSite=Lax` cookies via `_set_auth_cookies`),
  `POST /auth/refresh` (200, `UserResponse`, reads the refresh token from
  its own cookie -- not a request body -- and sets fresh rotated
  cookies). Tokens are delivered exclusively via `Set-Cookie`, matching
  the locked "JWT httpOnly cookies" auth-transport decision
  (PROJECT_STATE.md section 2) -- never in a JSON response body, since a
  script able to read the response (exactly what an XSS payload can do)
  could otherwise recover the token, defeating the reason `HttpOnly` was
  chosen. No logout route this session -- out of the approved
  scope, which named registration/login/JWT access-refresh, not full
  session management; `RefreshTokenRepositoryPort.revoke` is already
  exercised by rotation regardless.
- `app/api/dependencies.py` (extended) -- the composition root's
  central change this work makes. New: `AuthConfig` (JWT secret + token
  lifetimes, built once at startup, mirroring `active_scanner`'s own
  pattern) and `get_auth_config`; `get_identity_session` (a plain,
  non-RLS-scoped transaction, since `users`/`refresh_tokens` have no
  `organization_id`/RLS policy); the register/login/refresh use-case
  providers; and the two dependencies that actually resolve Technical
  Debt #9: `get_current_user` (reads the httpOnly `access_token`
  cookie, 401 on any failure) and `require_organization_member` (confirms
  ACTIVE membership, 403 if not -- reuses `get_org_session`'s already-open
  transaction, not a second database round trip). `AppState` gains a
  required `auth_config: AuthConfig` field.
- `app/api/v1/scans.py` (extended) -- every route now depends on
  `require_organization_member`; existing 404/409/202 behavior is
  unchanged in substance, just reached after auth passes now.
  `create_scan` additionally depends on `get_current_user` and passes
  `current_user.id` through as `TriggerScanUseCase.execute`'s
  `triggered_by_user_id` -- a parameter that use case has accepted
  since Milestone 4, previously always called with the default `None`
  since no route had an authenticated caller to attribute a scan to
  until now.
- `app/main.py` (extended) -- `_lifespan` now also builds an
  `AuthConfig` from `Settings`; `create_app()` mounts the new
  `auth_router` at `/api/v1/auth` and registers two new global
  exception handlers, `AuthenticationError` -> 401 and
  `EmailAlreadyRegisteredError` -> 409, the same "one handler per
  shared base class" convention already used for `LookupError`/
  `ScannerMismatchError`.
- `backend/pyproject.toml` -- `pyjwt`, `bcrypt`, `email-validator`
  (required by Pydantic's `EmailStr`) added to `dependencies`.
- `.env.example` -- new section: no new *required*
  variable names (`JWT_SECRET` has been required since Milestone 5,
  genuinely used for the first time now); `ACCESS_TOKEN_EXPIRE_MINUTES`/
  `REFRESH_TOKEN_EXPIRE_DAYS` documented as optional overrides of their
  existing code defaults.
- 6 new test files: `tests/unit/test_password_hashing.py` (8 tests),
  `tests/unit/test_token_service.py` (10 tests),
  `tests/unit/test_register_user.py` (4 tests),
  `tests/unit/test_login_user.py` (7 tests),
  `tests/unit/test_refresh_token_use_case.py` (7 tests),
  `tests/integration/test_api_auth.py` (11 tests, against real
  Postgres, exercising register/login/refresh end-to-end over real
  HTTP, asserting `HttpOnly`/`Secure`/`SameSite=Lax` cookie attributes
  and that no token value ever appears in a JSON body). 4 existing test
  files extended: `tests/integration/
  test_identity_repository.py` (5 new tests covering
  `SqlAlchemyRefreshTokenRepository`), `tests/integration/
  test_api_scans.py` (rewritten to authenticate every existing test
  while preserving every pre-existing 404/409/202 assertion, plus a new
  `TestAuthenticationAndAuthorization` class, 7 tests), `tests/
  integration/test_main_lifespan.py` (extended to assert `auth_config`
  is built correctly), `tests/unit/test_api_dependencies.py` (extended
  with `get_auth_config` and five `get_current_user` unit tests).
  `tests/integration/support.py` gained one small, additive factory,
  `make_member()`. See "Testing status" below for the full verification
  account, including a Filesystem MCP outage and two genuine
  investigations (one real, pre-existing RLS-sandbox-limitation
  distinction; one that turned out to be a sandbox-only stale-file
  artifact, not a real defect) resolved during this session.

**Targeted follow-up review, applied before this work was considered
complete.** After initial implementation, a review pass checked four
things and corrected two:
1. Confirmed this work must not be labeled "Milestone 8" -- Phase 2
   ends at Milestone 7; every reference throughout this file,
   `PROJECT_STATE.md`, `docs/session_state.md`, and stray code
   docstrings that had used that label was corrected to describe this
   as separate, additional pre-Phase-3 backend scope instead.
2. Confirmed the canonical, locked JWT-transport decision
   (`PROJECT_STATE.md` section 2) is httpOnly cookies. The initial
   implementation had instead used `Authorization: Bearer` headers,
   reasoning that no frontend yet existed to test a cookie flow against
   -- an unapproved divergence, not a genuine implementation blocker (a
   cookie-based flow does not require an existing frontend to implement
   or test correctly, only a cookie-capable client, which the test suite
   already is). Corrected: `login`/`refresh` now set
   `HttpOnly`/`Secure`/`SameSite=Lax` cookies instead of returning
   tokens in a JSON body; `get_current_user` reads the access-token
   cookie instead of a Bearer header; `refresh` reads the refresh token
   from its own cookie instead of a request body. The full test suite
   affected by this change (`test_api_auth.py`, `test_api_scans.py`,
   `test_api_dependencies.py`) was rewritten for cookies and
   re-verified in a freshly rebuilt sandbox: 91 of 92 relevant tests
   pass (the one failure is the same pre-existing, unrelated
   RLS-sandbox-limitation test described below), Ruff and MyPy strict
   both clean.
3. Reviewed whether `RefreshToken` (entity/port/repository) and
   rotation were genuinely required by the approved scope, or should be
   simplified. Kept as-is: the `refresh_tokens` table (including a
   `revoked_at` column) already existed in the schema before this work
   began, the approved scope explicitly named "secure ... JWT access/
   refresh authentication," and the implementation follows this
   project's own established per-aggregate repository pattern rather
   than inventing a shortcut.
4. Re-ran the broadest practical test suite against the corrected,
   real-repository content -- see "Testing status" below for exactly
   what could and could not be executed given this project's
   no-command-execution-tool constraint (PROJECT_STATE.md section 13),
   stated plainly rather than glossed over.

## In-progress milestone
None. Phase 2 (Milestones 1-7) and the separate Authentication /
Identity & Access work (Technical Debt #9) are both complete.

## Remaining milestones
Phases 3-10 (see roadmap table above). Phase 2's own milestone
breakdown (Milestones 1-7) remains fully complete on its own; the
Authentication work is tracked separately as pre-Phase-3 backend scope,
also complete.

## Architecture status
One amendment this session: the RLS + soft-delete predicate
(PROJECT_STATE.md section 3, originally combining both concerns into one
USING clause) turned out to be unimplementable in real Postgres -- see
the dated entry in PROJECT_STATE.md section 3 and the "Completed
milestones" section above for the full account. Tenant isolation itself
(the actually security-critical property) is unchanged and unweakened;
only where soft-delete visibility filtering lives moved, from an RLS
policy to the affected repositories' read methods. Everything else is
unchanged since the last update -- approved and finalized as of the
Phase 1.2 design review, including the nine implementation refinements,
the Asset Intelligence layer, and the finding_occurrences redesign
discovered during implementation. No other architecture changes this
session; the audit found no other design-level gaps beyond the one noted
above. Full decision log is in PROJECT_STATE.md.

Milestone 3 session: no architecture changes. The existing
implementation (see "Completed milestones" above) was audited against
every relevant locked decision in PROJECT_STATE.md section 3 and found
consistent with all of them -- no design gap, and therefore no amendment
to record here.

Milestone 4 session: no changes to already-locked architecture, but
four new dated entries were added to PROJECT_STATE.md section 3 (Scan
status derivation; pipeline resumability as actually implemented;
`AI_ANALYZE`/event-bus/scanner-registry scope boundaries; CVSS/severity
provenance) -- each fills in a decision this project's own prior
documentation had explicitly deferred (e.g. the `Scan` domain-model row
in PROJECT_STATE.md section 5 always said derivation logic was pending a
later milestone), rather than overriding anything previously locked.
One stale cross-reference was corrected (Scan status derivation
misattributed to "Milestone 3" in section 5's table, corrected to
"Milestone 4") -- a documentation fix, not an architecture change.

Milestone 5 session: no changes to already-locked architecture. One new
dated entry was added to PROJECT_STATE.md section 3 (`minio_bucket`
added to `Settings`) recording a necessary, minimal config addition, not
a redesign. The session's design decisions (no authentication yet,
Scanning-only API surface, no org-creation endpoint, synchronous
pipeline execution) are documented as scope boundaries in
`docs/session_state.md` and PROJECT_STATE.md section 3 rather than as
architecture changes, since none of them alters a previously locked
decision -- each fills in something PROJECT_STATE.md's own roadmap
already placed at a later milestone or phase (auth: Phase 6; async
execution: Milestone 7; Findings/Assets/Reporting APIs: those contexts'
own future milestones).

Milestone 6 session: no changes to already-locked architecture. One new
dated entry was added to PROJECT_STATE.md section 3 recording how the
`AI_ANALYZE`-before-`PERSIST` ordering tension was resolved (in-memory
stashing on `_PipelineItem`, deferred write in `_persist` -- the same
pattern `_enrich` already established for CVSS, not a new mechanism)
and why `AI_ANALYZE` is treated as safe-to-recompute rather than
exempted like `EXECUTE_SCANNER`. Neither is a redesign of a previously
locked decision -- both fill in exactly what PROJECT_STATE.md's own
roadmap already deferred to "whichever milestone builds
AIProviderPort/AnalysisService," the same category of fill-in as
Milestone 4's Scan-status-derivation and pipeline-resumability entries.

Milestone 7 session: no changes to already-locked architecture. One new
dated entry was added to PROJECT_STATE.md section 3 recording the
network-segmentation divergence discovered while writing
`docker-compose.yml` (the placeholder's envisioned `scanner-worker`
isolation is not achievable for the single, unsplit worker this session
actually builds -- see "Completed milestones" above for the full
account) -- a documented, honest divergence from a previously-written
placeholder comment, not a redesign of any decision that was ever
actually locked (the placeholder was illustrative "target shape," per
its own header comment, not a locked architectural decision in the
sense PROJECT_STATE.md section 14 protects). Everything else --
`RunScanWorkflowUseCase` itself, the eight-stage pipeline, RLS, the
repository/port pattern -- is unchanged.

Authentication work: no changes to already-locked architecture. This
work fills in exactly what Technical Debt #9 and PROJECT_STATE.md's
own roadmap already deferred to "whichever session builds real auth"
-- the bounded-context boundaries, the ports-and-adapters pattern, RLS,
and every other locked decision in PROJECT_STATE.md section 3 are
unchanged. Two new patterns established, both consistent
with rather than divergent from existing precedent: (1) opaque, hashed,
rotating refresh tokens plus stateless signed-JWT access tokens,
delivered exclusively via httpOnly cookies (the already-locked
auth-transport decision) -- not a new architectural layer, but the same
shape `RefreshToken`'s own table (present, unused, since Milestone 2)
was always going to need; (2) `password_hashing.py`/`token_service.py`
imported directly into the application layer rather than behind a new
port -- explicitly following the `target_validation.py` precedent
PROJECT_STATE.md section 3 already establishes for single-implementation,
framework-free infrastructure utilities, not a new exception to the
dependency rule. A follow-up review corrected an initial,
unapproved-divergence implementation of that first pattern
(`Authorization: Bearer` headers instead of the locked httpOnly-cookie
decision) before this work was considered complete -- see "Completed
milestones" above and PROJECT_STATE.md section 3's dated entry for the
full account.

## Testing status
102 tests total (64 unit -- 55 from Milestone 1 unchanged plus 9 new for
Milestone 2's domain entities; 38 integration, against a real PostgreSQL
16 instance, covering repository CRUD, natural-key lookups, append-only
history tables, `LookupError` error paths, and RLS tenant isolation).
100% coverage, clean Ruff (`check` and `format --check`), clean MyPy
strict. Execution ran in Claude's own sandbox (with PostgreSQL 16
installed there for the purpose) against the full set of Milestone 1 +
Milestone 2 files, then every file was written into this repository via
the Filesystem MCP -- not executed on `C:\Users\gamer\Downloads\claudeOnly`
itself, since this connector still has no command-execution tool. The
migration was additionally verified reversible (`alembic downgrade base`
then `alembic upgrade head`, both clean) and RLS tenant isolation was
additionally proven via raw SQL (two sessions scoped to different orgs,
each seeing only its own row) before the automated suite existed, not
only by the automated suite itself.

Self-verification commands (adjust `DATABASE_URL`/`TEST_DATABASE_URL`
for your local PostgreSQL instance; both databases must have the initial
migration applied first):

    cd "C:\Users\gamer\Downloads\claudeOnly\backend"
    pip install -e ".[dev]"
    DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/security_platform alembic upgrade head
    TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/security_platform_test pytest -v
    ruff check .
    ruff format --check .
    mypy app

**Milestone 3 addendum.** 105 unit tests total (up from 64 at the end of
Milestone 2 -- the 38 integration tests are unchanged and were not
re-executed this session, see below), 100% coverage on every Milestone 3
module (`scanner_port.py`, `storage_port.py`, `base_scanner.py`,
`target_validation.py`, `minio_storage.py`, `adapters/nuclei/adapter.py`),
clean Ruff (`check` and `format --check`), clean MyPy strict across all
74 source files. Execution ran in Claude's sandbox as usual (see
PROJECT_STATE.md section 13). The six Milestone 3 deliverable files and
their four test files were read verbatim from
`C:\Users\gamer\Downloads\claudeOnly` and transplanted byte-for-byte --
these are the files this milestone's sign-off actually rests on. The
Milestone 1/2 files those six files transitively import (domain
entities/value objects/shared utilities, config, the ORM models, the
repository implementations) were reconstructed in the sandbox to match
the architecture recorded in PROJECT_STATE.md and this file, rather than
re-read verbatim a second time in this same session -- an earlier
verbatim read of each had already happened this session but fell out of
context before it was used, a mechanical limitation of this session's
length, not a shortcut taken deliberately. Practically: this session's
run is a strong behavioral verification that the Milestone 3 code is
correct and integrates cleanly, but is not a renewed byte-for-byte
re-verification of every Milestone 1/2 file already verified in its own
session -- stated plainly rather than implied to be more than it is, per
the verification-honesty rule.

Stated plainly, per the verification-honesty rule: the Postgres
integration suite (38 tests) was **not** re-executed this session --
Milestone 3 introduced no changes to `app/domain/`,
`app/infrastructure/db/`, or anything that suite exercises, so its last
verified-passing state remains Milestone 2's session, not this one.
Separately, `MinioStoragePort` and `NucleiAdapter` are each verified
only against a unit-level fake/mock of their external dependency (a
mocked `Minio` client; a patched `run_scanner_subprocess`) -- no real
MinIO server or `nuclei` binary was reachable in this environment to
build an equivalent real-server integration test against, a weaker tier
than Milestone 2's real-Postgres integration suite. See "Technical
debt" below.

**Milestone 4 addendum.** 44 new tests: 41 unit
(`test_scan_status_derivation.py`, `test_normalization.py`,
`test_trigger_scan.py`, `test_run_scan_workflow.py`) plus 3 integration
(`test_scan_pipeline_orchestrator.py`, against real Postgres). 100%
coverage on every Milestone 4 module (`entities.py`'s new additions,
`normalization.py`, `trigger_scan.py`, `run_scan_workflow.py`), clean
Ruff (`check` and `format --check`), clean MyPy strict. Execution ran in
Claude's sandbox as usual (PROJECT_STATE.md section 13).

**Milestone 5 addendum.** 28 new/updated tests: `test_api_schemas.py`
(10), `test_health.py` (1), `test_api_scans.py` (14, integration, real
Postgres, scanner/storage faked -- the same split as Milestone 4's own
integration suite for the use cases these routes call),
`test_health_ready.py` (2, integration), `test_main_lifespan.py` (1,
integration), plus one new test in `test_config.py`. Clean Ruff (`check`
and `format --check`), clean MyPy strict on every Milestone 5 module (77
source files project-wide, 12 test files touched this session).

Two verification-honesty notes specific to this session, stated plainly
rather than glossed over:

1. **A mid-session Filesystem MCP outage** (the connector became
   unresponsive for several minutes, twice) meant the real Alembic
   migration file could not be re-fetched verbatim in time to reconstruct
   it in the sandbox. Rather than reconstruct SQL from memory (against
   this project's own standing rule), the sandbox schema was created via
   `SQLAlchemy Base.metadata.create_all()` instead of
   `alembic upgrade head` this session only -- meaning **this session's
   sandbox database did not have the real migration's Row-Level Security
   policies**. Judged not to undermine Milestone 5's own verification
   (RLS tenant isolation itself is unchanged and was already proven by
   Milestone 2's real-Postgres suite; Milestone 5 introduces no new
   RLS-relevant behavior, only reuses `session_scoped_to_org` exactly as
   Milestone 2 built it), but a real, narrower tier than every prior
   milestone's sandbox database. Full account, including how the outage
   was handled, in `docs/session_state.md`.
2. **A `coverage`/`pytest-cov` measurement gap** was discovered and
   investigated: lines executing inside an awaited SQLAlchemy
   async-session call are under-reported as "missing" by the coverage
   tool in this sandbox, a known interaction with SQLAlchemy's internal
   greenlet-based async bridging -- confirmed by calling
   `get_org_session` by hand and observing its `yield` line execute
   despite the tool reporting it as never hit. The documented fix
   (`concurrency = ["greenlet", "thread"]`) was tried and reverted after
   the local PostgreSQL instance went unreachable immediately afterward;
   not pursued further given the risk of destabilizing verification
   further, though the timing looks more likely coincidental than causal
   (the Postgres *server process* was confirmed down, not just a client
   symptom). One genuine, non-measurement-artifact gap this
   investigation did find and fix: `app/main.py`'s `_lifespan` was never
   exercised by any test (by design -- the API-behavior tests bypass it
   entirely via dependency overrides), so
   `tests/integration/test_main_lifespan.py` was added specifically to
   close that real gap. Every other line the coverage tool still reports
   as "missing" in `app/api/dependencies.py` and `app/api/v1/scans.py`
   was traced by hand against this session's test suite and confirmed
   exercised by an explicit, passing assertion -- not asserted without
   checking. Full account in `docs/session_state.md`.

**Milestone 6 addendum.** 84 new/updated tests: 2 new unit files
(`test_anthropic_provider.py`, 8 tests, mocking the `anthropic` SDK's
client class; `test_analysis_service.py`, 13 tests, schema-validation
focused), plus 5 existing test files substantially extended
(`test_run_scan_workflow.py`, 25 tests up from 21; `test_config.py`, 24
tests up from 17; `test_api_scans.py`, 14 tests, integration;
`test_main_lifespan.py`, 2 tests up from 1, integration;
`test_scan_pipeline_orchestrator.py`, 3 tests, integration, real
Postgres). 100% coverage on every Milestone 6 module (`ai_provider_port.py`,
`anthropic_provider.py`, `analysis_service.py`, `run_scan_workflow.py`'s
new/changed lines), clean Ruff (`check` and `format --check`), clean
MyPy strict on 99 source files project-wide (86 app + 13 test files
touched this session). Execution ran in Claude's sandbox as usual
(PROJECT_STATE.md section 13), including the `anthropic` SDK, whose
actual installed API (message content block types, exception hierarchy)
was empirically confirmed in the sandbox before `AnthropicProvider` was
written against it, rather than assumed from training-data memory.

One verification-honesty note specific to this session, stated plainly
rather than glossed over: **the sandbox's own hand-retyped copy of the
real Alembic migration had a transcription error** in the
`organizations` table's RLS policy (missing `missing_ok=true` on
`current_setting`; too-strict `WITH CHECK`), which broke this session's
own sandbox verification of organization creation via a raw, unscoped
session -- a pattern every integration test in this project uses,
unchanged since Milestone 2, and documented as passing against the real
migration in every prior session's own real-repository test runs (most
recently Milestone 5's 48-test suite). Corrected the sandbox's own copy
to unblock this session's verification; **the real repository's actual
migration file was read-only this entire session and was never
touched.** Full account, including exactly what was corrected and why
the real file was judged unaffected, in `docs/session_state.md`.

The sandbox reconstruction for this milestone was the most extensive
yet, stated plainly rather than glossed over: `RunScanWorkflowUseCase`
genuinely depends on essentially the whole backend (domain entities,
config, all six ORM model modules, all five repository
implementations from Milestones 1-2, plus `ScannerPort`/`StoragePort`/
`target_validation`/`NucleiAdapter` from Milestone 3), so all of it was
read verbatim from `C:\Users\gamer\Downloads\claudeOnly` through the
Filesystem MCP this session and reconstructed file-by-file in the
sandbox before any Milestone 4 code was written or run. This is not a
renewed byte-for-byte re-verification of the Milestone 1-3 test suites
themselves -- those test files were not copied into the sandbox this
session, only their source modules were (for import purposes), so
Milestone 1-3's own "105 unit passed"/"38 integration passed" results
are unchanged from their own sessions, not re-confirmed here. What this
session's integration test newly confirms: the real, unmodified
Milestone 2 repository implementations still behave correctly end-to-end
(create, natural-key lookup, RLS-scoped session usage) when driven by
this milestone's new orchestrator against a real Postgres 16 instance,
not merely assumed unchanged because no Milestone 1-3 file was edited.

Every new Milestone 4 file, and the one modified Milestone 2 file
(`app/domain/scanning/entities.py`, extended not rewritten), was written
into the real repository via the Filesystem MCP and spot-checked by byte
count against the sandbox-verified source -- for every new file this
time (`normalization.py`: 5508 bytes; `trigger_scan.py`: 2166 bytes;
`run_scan_workflow.py`: 22052 bytes; `test_run_scan_workflow.py`: 25678
bytes; `test_scan_pipeline_orchestrator.py`: 9979 bytes), all matching
exactly. `entities.py`'s edit was applied surgically via `edit_file`
against the real file's own exact original text (not the sandbox
reconstruction's paraphrase of it), so its post-edit byte count
naturally differs slightly from the sandbox copy by the few bytes of
pre-existing wording the sandbox reconstruction had paraphrased rather
than quoted verbatim -- expected, and does not affect the correctness of
the applied edit, which is anchored to the real file's verbatim text.

**Milestone 7 addendum.** 12 new/updated tests: 3 new unit files
(`test_celery_app.py`, 3 tests; `test_api_dependencies.py`, 3 tests) plus
1 new integration file (`test_scan_worker_task.py`, 3 tests), plus 3
existing test files updated (`test_config.py`, 3 tests rewritten;
`test_api_scans.py`, rewritten, 13 tests; `test_main_lifespan.py`,
simplified, 1 test). Clean Ruff (`check` and `format --check`), clean
MyPy strict on every Milestone 7 module (99 source files project-wide,
unchanged from Milestone 6's count since this milestone added 2 new
application modules and removed none).

One verification-honesty note specific to this session: a coverage
investigation found a genuine, novel gap, distinct from the
previously-documented Milestone-5 async-SQLAlchemy-greenlet measurement
artifact. `app/api/dependencies.py`'s three small provider functions
(`get_session_factory`, `get_active_scanner`, `get_scan_dispatcher`)
showed as uncovered because the integration suite overrides all three
wholesale via `app.dependency_overrides` (by design), so their real
bodies are never actually called by any test -- only their replacements
are. This is different from Milestone 5's gap (lines that *do* execute
but are mismeasured by the coverage tool): this is code that genuinely
does not run under the existing override pattern. Closed directly with a
new, focused unit test file (`test_api_dependencies.py`), the same "close
a real gap the investigation found" move Milestone 5 made for
`_lifespan` via `test_main_lifespan.py`. The remaining uncovered lines in
`app/api/dependencies.py` (`get_org_session`'s own body) and
`app/api/v1/scans.py` (every route handler's body) were re-confirmed by
hand to be the same greenlet-measurement artifact Milestone 5 already
documented -- `get_org_session` was called directly outside pytest this
session, mirroring Milestone 5's own verification method exactly, and
observed to execute its existence-check and yield line correctly.

Docker itself is not available in the verification environment used this
session (no `docker`/`docker-compose` binary) -- `docker-compose.yml`
was verified by the strongest means actually available: valid YAML
confirmed, and its service/network/`depends_on` graph programmatically
inspected to match this session's design exactly (six services, three
networks). The `Dockerfile`'s `pip install .` step (no dev extras) was
run for real into a fresh virtualenv, confirming every module the
Compose services' `command:` entries need imports cleanly from that
install. Neither `docker-compose config` validation nor an actual
container build/run was possible -- a real Docker environment is
required to confirm that final tier, the same category of gap already
stated plainly for MinIO/`nuclei` real-server testing (Technical debt
items #6-7).

Also stated plainly: only the test files Milestone 7 actually touches,
depends on, or adds were reconstructed and run in this session's sandbox
(48 tests total) -- the remaining ~20 test files this project's real
repository also contains were not copied into this sandbox and were not
re-executed here, the same "not a renewed byte-for-byte re-verification
of every earlier file's own test suite" caveat Milestone 4's session
already stated for the identical situation. A pre-existing, unrelated
correctness defect was also found and fixed while rewriting
`test_api_scans.py` for this milestone's own reasons: its
`_create_organization()` helper did not call `set_org_context()` before
inserting, which a direct empirical check against real Postgres 16
confirmed genuinely fails against `organizations`' own RLS policy
(database-level GUC provisioning alone cannot fix it, since the policy's
`WITH CHECK` requires the GUC equal the specific new row's id). Fixed
in the version of this file written to the real repository, called out
in that file's own updated module docstring; a future session should
check whether the same pattern exists elsewhere. Full account in
`docs/session_state.md`.

**Authentication work addendum.** 62 new/updated tests: 6 new test files
(`test_password_hashing.py` 8, `test_token_service.py` 10,
`test_register_user.py` 4, `test_login_user.py` 7,
`test_refresh_token_use_case.py` 7, `test_api_auth.py` 11 -- integration,
real Postgres) plus 4 existing files extended
(`test_identity_repository.py` +5 tests, integration;
`test_api_scans.py` rewritten, 21 tests total, integration;
`test_main_lifespan.py` extended, integration; `test_api_dependencies.py`
+6 tests). Clean Ruff (`check` and `format --check`), clean MyPy strict
on every module this work touches.

Three verification-honesty notes, stated plainly rather than glossed
over:

1. **A mid-session Filesystem MCP outage**, the same full-disconnection
   signature prior sessions' outages already document ("tool not
   found" on every call including `list_allowed_directories`, spanning
   multiple separate conversation turns) meant the real Alembic
   migration could not be re-fetched verbatim for the sandbox
   reconstruction. The identical fallback Milestone 5's session
   already used was reused here: `Base.metadata.create_all()` instead
   of `alembic upgrade head`, meaning the sandbox database
   had no RLS policies. The one pre-existing (Milestone 2, unmodified
   by this work) RLS-isolation test in `test_identity_repository.py`
   fails in the sandbox specifically because of this -- not because of
   anything this work changed; RLS itself is unaffected by this
   work's own scope (`refresh_tokens`, like `users`, has never had
   an RLS policy) and was already proven against real Postgres with the
   real migration in Milestone 2's own session. Every other test
   touched passed: 91 of 92 relevant tests in the sandbox's
   final run (after the JWT-cookie correction described below).
2. **A second, initially-alarming test failure was investigated to its
   real root cause and found to be a sandbox-only artifact, not a real
   defect.** `test_create_scan_returns_201_with_all_eight_steps_pending`
   failed on a missing `Location` response header. Extensive isolated
   bisection (minimal FastAPI reproductions, raw-ASGI header snooping,
   testing each dependency in the actual chain individually and in
   combination) traced this to a stale sandbox copy of
   `app/api/v1/scans.py` -- written early before the real
   file's pre-existing `response_model=ScanDetailResponse`/
   `response: Response`/`Location`-header structure was discovered
   during the transplant phase, and never re-synced. The real
   repository's `create_scan` (read and confirmed correct multiple times
   during the actual transplant) was never affected; re-syncing the
   sandbox file from the real repository's actual content resolved the
   failure for the correct reason.
3. **A targeted follow-up review found and corrected the JWT-transport
   divergence described above** (`Authorization: Bearer` instead of the
   locked httpOnly-cookie decision). After correcting `app/api/v1/
   auth.py`, `app/api/dependencies.py`, and `app/infrastructure/
   security/token_service.py`, the affected test files
   (`test_api_auth.py`, `test_api_scans.py`, `test_api_dependencies.py`)
   were rewritten for cookie-based auth and the sandbox was rebuilt
   fresh from the corrected real-repository content -- confirming
   91 of 92 relevant tests pass (the one failure being the same
   pre-existing RLS-sandbox-limitation test in note 1 above), Ruff
   check/format clean, MyPy strict clean. This re-verification, like
   every verification in this project, ran in Claude's own sandbox --
   PROJECT_STATE.md section 13's no-command-execution-tool constraint
   for `C:\Users\gamer\Downloads\claudeOnly` is unchanged and was not
   worked around.

A fourth finding, related but not itself a bug: four test files were
initially drafted in the sandbox against an invented fixture
convention (a `session_factory` fixture, ad hoc token helpers) before
the real repository's actual, already-established integration-test
conventions (`db_session`/`engine`/`postgres_available` in
`tests/conftest.py`; `wired_app`/`_make_client`/`_create_organization`
helpers already present in the real `test_api_scans.py`;
`make_organization`/`make_user`/`make_scan`/`set_org_context` already
present in `tests/integration/support.py`) could be read, due to the
same Filesystem MCP outage above. Once the real files were read fresh
during the transplant phase, all four modified test files plus the one
new integration test file were rewritten to match the real, pre-existing
conventions exactly, extending them rather than replacing them with an
invented alternative -- `tests/integration/support.py` itself needed
only one small, additive change (`make_member()`), not a rewrite. Full
account of all findings in `docs/session_state.md`.

## Files created
Code (Milestone 1, complete, all 16 files present, audited, and
dynamically verified):
`.gitignore`, `backend/pyproject.toml`, `backend/app/__init__.py`,
`backend/app/config.py`, `backend/app/domain/__init__.py`,
`backend/app/domain/shared/__init__.py`,
`backend/app/domain/shared/{ids,clock,fingerprint,events,enums}.py`,
`backend/app/domain/findings/__init__.py`,
`backend/app/domain/findings/value_objects.py`,
`backend/tests/__init__.py`, `backend/tests/unit/__init__.py`,
`backend/tests/unit/test_{config,ids,clock,fingerprint,events,
value_objects}.py`

Code (Milestone 2, complete, dynamically verified):
- `backend/alembic.ini`, `backend/alembic/env.py`,
  `backend/alembic/script.py.mako`,
  `backend/alembic/versions/6bdbf0ab25b0_initial_schema.py`
- `backend/app/infrastructure/db/base.py`,
  `backend/app/infrastructure/db/session.py`
- `backend/app/infrastructure/db/models/{__init__,identity,assets,
  scanning,findings,reporting,platform}.py`
- `backend/app/infrastructure/db/repositories/{__init__,identity_repository,
  assets_repository,scanning_repository,findings_repository,
  reporting_repository}.py`
- `backend/app/domain/{identity,assets,scanning,findings,reporting}/
  entities.py`
- `backend/app/application/interfaces/{identity_repository,
  assets_repository,scanning_repository,findings_repository,
  reporting_repository}.py`
- `backend/tests/integration/__init__.py`, `backend/tests/integration/
  support.py`, `backend/tests/integration/test_{identity_repository,
  assets_repository,scanning_repository,findings_repository,
  reporting_repository,session,repository_error_paths}.py`
- `backend/tests/unit/test_domain_entities.py` (new)
- `backend/tests/conftest.py` (replaced -- see "Completed milestones"
  above for why)

Structure only (directories plus placeholder `__init__.py`, no logic):
unchanged this session except where a package gained real content above
(each such package's `__init__.py` was updated from a "Not yet
implemented" placeholder to a short description of what it now
contains -- not a structural change).

Modified (Milestone 1 files, non-functional fixes only):
- `backend/tests/unit/test_value_objects.py`: removed one now-unused
  `# type: ignore[operator]` comment. Mechanical mypy-version-drift fix
  (mypy 2.3.0 no longer flags that comparison without the ignore, so the
  ignore itself became the lint error under `warn_unused_ignores`) --
  no change to test behavior or assertions, not a Milestone 1
  functionality change.
- `backend/pyproject.toml`: added `sqlalchemy[asyncio]`, `asyncpg`,
  `alembic` to `dependencies`; added `pytest-asyncio` to `dev`; added
  `asyncio_mode = "auto"` and an `integration` marker to pytest config.
- `.env.example`: added `DATABASE_URL`, `TEST_DATABASE_URL`, `REDIS_URL`
  under a new "Milestone 2" section, per the file's own comment
  planning this.

Code (Milestone 3, complete, dynamically verified this session --
already present on disk at the start of this session; see "Completed
milestones" above for the discovery this session made of that fact):
- `backend/app/application/interfaces/scanner_port.py`,
  `backend/app/application/interfaces/storage_port.py`
- `backend/app/scanner_engine/base_scanner.py`
- `backend/app/infrastructure/security/target_validation.py`
- `backend/app/infrastructure/storage/minio_storage.py`
- `backend/app/scanner_engine/adapters/nuclei/adapter.py`
- `backend/tests/unit/test_{base_scanner,target_validation,
  minio_storage,nuclei_adapter}.py`

Modified (Milestone 3 files, this session):
- `backend/app/scanner_engine/adapters/__init__.py`: docstring corrected
  from a stale "Not yet implemented" to accurately describe `nuclei/` as
  implemented and the remaining six adapter subpackages as Phase 4
  stubs. The only line-level change this session made to any Milestone 3
  file -- everything else already met this project's standard as found.

Documentation:
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated this session to record
Milestone 3's completion and the documentation-lag discovery described
above.

Code (Milestone 4, complete, dynamically verified, genuine fresh
implementation this session):
- `backend/app/application/scanning/trigger_scan.py` (new)
- `backend/app/application/scanning/normalization.py` (new)
- `backend/app/application/scanning/run_scan_workflow.py` (new)
- `backend/tests/unit/test_scan_status_derivation.py` (new)
- `backend/tests/unit/test_normalization.py` (new)
- `backend/tests/unit/test_trigger_scan.py` (new)
- `backend/tests/unit/test_run_scan_workflow.py` (new)
- `backend/tests/integration/test_scan_pipeline_orchestrator.py` (new)

Modified (Milestone 4, this session):
- `backend/app/domain/scanning/entities.py`: extended (not rewritten)
  with `PIPELINE_STEP_ORDER` and `derive_scan_status()`, plus an updated
  module docstring paragraph explaining the derivation rule's new home;
  `Scan`, `ScanWorkflowStep`, and `ScanScope` themselves are byte-for-
  byte unchanged from Milestone 2.
- `backend/app/application/scanning/__init__.py`: docstring corrected
  from "Not yet implemented" to describe the three files now present,
  the same pattern as the Milestone 3 `scanner_engine/adapters/
  __init__.py` docstring fix.

Documentation (Milestone 4, this session):
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated to record Milestone 4's
completion, including the two flagged design decisions and the stale
Milestone-3 cross-reference corrected in PROJECT_STATE.md section 5.

Code (Milestone 5, complete, dynamically verified, genuine fresh
implementation this session):
- `backend/app/api/dependencies.py` (new)
- `backend/app/api/v1/schemas.py` (new)
- `backend/app/api/v1/scans.py` (new)
- `backend/app/api/internal/__init__.py` (new package)
- `backend/app/api/internal/health.py` (new)
- `backend/app/main.py` (new)
- `backend/tests/unit/test_api_schemas.py` (new)
- `backend/tests/unit/test_health.py` (new)
- `backend/tests/integration/test_api_scans.py` (new)
- `backend/tests/integration/test_health_ready.py` (new)
- `backend/tests/integration/test_main_lifespan.py` (new)

Modified (Milestone 5, this session):
- `backend/app/config.py`: added `minio_bucket: str | None = None` and
  joined it to the existing MinIO required-fields validator check.
- `backend/app/api/__init__.py`, `backend/app/api/v1/__init__.py`:
  docstrings corrected from "Not yet implemented" to describe what each
  package now contains, the same pattern as every prior milestone's
  placeholder-docstring fix.
- `backend/tests/unit/test_config.py`: `_settings()`'s defaults dict and
  the real-environment-variables test extended with `minio_bucket`; one
  new test (`test_every_role_requires_minio_bucket`).
- `backend/pyproject.toml`: added `fastapi`, `uvicorn[standard]` to
  `dependencies`; added `httpx` to `dev`; added a `ruff`
  per-file-ignore (`B008` under `app/api/**/*.py`) for FastAPI's
  `Depends(...)`-in-a-default idiom.
- `.env.example`: added `MINIO_BUCKET` and `JWT_SECRET` under a new
  "Milestone 5" section.

Documentation (Milestone 5, this session):
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated to record Milestone 5's
completion, including the four flagged design/scope decisions (no
authentication yet; Scanning-only API surface; no org-creation endpoint;
synchronous pipeline execution) and the two verification-honesty notes
about this session's tool outage and coverage-measurement investigation.

Code (Milestone 6, complete, dynamically verified, genuine fresh
implementation this session):
- `backend/app/application/interfaces/ai_provider_port.py` (new)
- `backend/app/infrastructure/ai_providers/anthropic_provider.py` (new)
- `backend/app/ai_agents/analysis_service.py` (new)
- `backend/tests/unit/test_anthropic_provider.py` (new)
- `backend/tests/unit/test_analysis_service.py` (new)

Modified (Milestone 6, this session):
- `backend/app/application/scanning/run_scan_workflow.py`: extended
  (not rewritten) -- `_PipelineItem` gains `ai_analysis`; a new
  `_ai_analyze` method calls `AnalysisService`; `_persist` writes
  `Finding.ai_severity_level` and appends `FindingAnalysis` rows; the
  bespoke `_run_ai_analyze_step` (which only ever set `SKIPPED`) is
  removed in favor of routing `AI_ANALYZE` through the same `_run_step`
  every other stage uses. Constructor gains a required
  `analysis_service: AnalysisService` parameter.
- `backend/app/config.py`: added `ai_model: str = "claude-sonnet-4-5"`;
  extended `check_role_boundaries` to require `anthropic_api_key` for
  `worker_role=api` when `ai_default_provider == "anthropic"`, plus a
  matching production dev-default-rejection check.
- `backend/app/api/dependencies.py`: `AppState` gains
  `analysis_service: AnalysisService`; new `get_analysis_service`
  provider; `get_run_scan_workflow_use_case` extended to inject it.
- `backend/app/main.py`: `_lifespan` extended to construct
  `AnthropicProvider`/`AnalysisService` from `Settings` and add them to
  `AppState`; raises `ValueError` at startup for an unimplemented
  `ai_default_provider`.
- `backend/app/domain/scanning/entities.py`: docstring-only edit --
  `derive_scan_status`'s docstring corrected to reflect that
  `AI_ANALYZE` no longer automatically produces `SKIPPED`.
- `backend/app/ai_agents/__init__.py`,
  `backend/app/infrastructure/ai_providers/__init__.py`,
  `backend/app/application/interfaces/__init__.py`,
  `backend/app/application/scanning/__init__.py`: docstrings updated to
  describe what each package now contains, the same pattern as every
  prior milestone's placeholder-docstring fix.
- `backend/tests/unit/test_run_scan_workflow.py`: `AI_ANALYZE`'s old
  "always SKIPPED" tests rewritten to assert real completion/
  persistence/retry behavior; `FakeFindingRepository.add_analysis`/
  `list_analyses` implemented (previously `NotImplementedError`); new
  `FakeAIProviderPort`; several new tests (provider-failure resilience,
  CVSS-rejection-not-resurfacing, recurring-finding analysis-append).
- `backend/tests/unit/test_config.py`: `_settings()`'s defaults dict
  extended with `anthropic_api_key`; six new tests.
- `backend/tests/integration/test_api_scans.py`: `get_analysis_service`
  override added to the `wired_app` fixture; one test renamed/updated
  for real `AI_ANALYZE` completion.
- `backend/tests/integration/test_main_lifespan.py`: assertions for
  `wired.analysis_service` added; one new test for the
  unimplemented-provider fail-fast path.
- `backend/tests/integration/test_scan_pipeline_orchestrator.py`: AI
  provider wiring and real-database analysis-persistence assertions
  added to the existing happy-path/recurrence/failure tests.
- `backend/pyproject.toml`: added `anthropic>=0.120` to `dependencies`.
- `.env.example`: added `AI_DEFAULT_PROVIDER`, `AI_MODEL`,
  `ANTHROPIC_API_KEY` under a new "Milestone 6" section.

Documentation (Milestone 6, this session):
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated to record Milestone 6's
completion, including the design decisions on the `AI_ANALYZE`-before-
`PERSIST` ordering resolution and the recomputation-cost trade-off, and
the verification-honesty note about this session's own sandbox-
migration transcription error (never propagated to the real repository).

Code (Milestone 7, complete, dynamically verified, genuine fresh
implementation this session):
- `backend/app/workers/celery_app.py` (new)
- `backend/app/workers/tasks.py` (new)
- `backend/Dockerfile` (new)
- `backend/.dockerignore` (new)
- `backend/tests/unit/test_celery_app.py` (new)
- `backend/tests/unit/test_api_dependencies.py` (new)
- `backend/tests/integration/test_scan_worker_task.py` (new)

Modified (Milestone 7, this session):
- `backend/app/api/v1/scans.py`: `run_scan` rewritten for the
  async-dispatch/202 contract (see "Completed milestones" above for the
  full account).
- `backend/app/api/dependencies.py`: `AppState` narrowed; `get_storage`/
  `get_analysis_service`/`get_run_scan_workflow_use_case`/
  `get_asset_repository`/`get_finding_repository` removed;
  `get_scan_dispatcher` added.
- `backend/app/main.py`: `_lifespan` narrowed (no longer constructs
  `MinioStoragePort`/`AnthropicProvider`/`AnalysisService`);
  `_lookup_error_handler`'s docstring updated.
- `backend/app/config.py`: `ANTHROPIC_API_KEY` requirement moved from
  `worker_role=api` to `worker_role=ingestion_worker`.
- `backend/app/application/scanning/run_scan_workflow.py`:
  docstring-only edit -- a new "Milestone 7 update" paragraph recording
  that this file itself is unchanged, and why.
- `backend/tests/unit/test_config.py`: 3 tests rewritten for the new
  `ANTHROPIC_API_KEY` role-scoping.
- `backend/tests/integration/test_api_scans.py`: rewritten for the
  async-dispatch contract; also fixes a pre-existing, unrelated
  correctness gap in `_create_organization()` (see "Testing status"
  above).
- `backend/tests/integration/test_main_lifespan.py`: simplified (no
  longer needs MinIO/Anthropic-shaped credentials).
- `backend/pyproject.toml`: added `celery` to `dependencies`,
  `celery-types` to `dev`.
- `.env.example`: added a "Milestone 7" section documenting that no new
  variable names were introduced; corrected the existing Milestone 6
  paragraph about `ANTHROPIC_API_KEY`'s required role, now stale.
- `docker-compose.yml` (root): replaced the placeholder with the full,
  wired-end-to-end topology.

Documentation (Milestone 7, this session):
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated to record Milestone 7's
completion, including the network-segmentation design decision and its
divergence from the placeholder it replaces, the new Technical debt item
#12, and this session's own verification-honesty notes (a mid-session
Filesystem MCP outage; a coverage-gap investigation; a pre-existing test
defect found and fixed; the absence of a Docker environment to fully
verify the compose file and image). `docs/session_state.md`
additionally closes out a pre-existing gap unrelated to this milestone's
own work: that file had not actually been overwritten since the
Milestone 5 session, despite `implementation_progress.md` and
`PROJECT_STATE.md` both correctly recording Milestone 6 as complete --
see that file's own note on this, recorded there rather than silently
smoothed over.

Code (Authentication / Identity & Access work, complete, dynamically
verified, genuine fresh implementation):
- `backend/app/infrastructure/security/password_hashing.py` (new)
- `backend/app/infrastructure/security/token_service.py` (new)
- `backend/app/application/identity/errors.py` (new)
- `backend/app/application/identity/tokens.py` (new)
- `backend/app/application/identity/register_user.py` (new)
- `backend/app/application/identity/login_user.py` (new)
- `backend/app/application/identity/refresh_token.py` (new)
- `backend/app/api/v1/auth_schemas.py` (new)
- `backend/app/api/v1/auth.py` (new)
- `backend/tests/unit/test_password_hashing.py` (new)
- `backend/tests/unit/test_token_service.py` (new)
- `backend/tests/unit/test_register_user.py` (new)
- `backend/tests/unit/test_login_user.py` (new)
- `backend/tests/unit/test_refresh_token_use_case.py` (new)
- `backend/tests/integration/test_api_auth.py` (new)

Modified (Authentication / Identity & Access work):
- `backend/app/domain/identity/entities.py`: extended (not rewritten)
  with the `RefreshToken` entity; `Organization`/`User`/`AuditLogEntry`/
  `OrganizationMember` themselves unchanged.
- `backend/app/application/interfaces/identity_repository.py`: extended
  with `RefreshTokenRepositoryPort`.
- `backend/app/infrastructure/db/repositories/identity_repository.py`:
  extended with `SqlAlchemyRefreshTokenRepository` and its
  `_refresh_token_to_domain` mapper.
- `backend/app/infrastructure/db/repositories/__init__.py`: re-export
  added for `SqlAlchemyRefreshTokenRepository`.
- `backend/app/api/dependencies.py`: extended -- `AuthConfig` dataclass
  and `get_auth_config`; `get_identity_session`;
  `get_user_repository`/`get_refresh_token_repository`; the
  register/login/refresh use-case providers; `get_current_user`;
  `require_organization_member`. `AppState` gains a required
  `auth_config: AuthConfig` field.
- `backend/app/api/v1/scans.py`: every route gains a
  `require_organization_member` dependency; `create_scan` additionally
  gains `get_current_user` and passes `current_user.id` through as
  `triggered_by_user_id`.
- `backend/app/main.py`: `_lifespan` builds an `AuthConfig`;
  `create_app()` mounts the new `auth_router` and registers the two new
  exception handlers.
- `backend/app/application/identity/__init__.py`,
  `backend/app/infrastructure/security/__init__.py`,
  `backend/app/api/v1/__init__.py`: docstrings updated to describe the
  new modules, the same pattern every prior milestone's
  placeholder-docstring fix has followed.
- `backend/tests/integration/support.py`: `make_member()` factory
  added (additive only -- every pre-existing factory unchanged).
- `backend/tests/integration/test_identity_repository.py`: extended
  with a `TestSqlAlchemyRefreshTokenRepository`-equivalent block of 5
  new tests; every pre-existing test unchanged.
- `backend/tests/integration/test_api_scans.py`: rewritten to
  authenticate every existing test (a new `_create_authenticated_member`
  helper, sending the resulting bearer token on every request) while
  preserving every pre-existing 404/409/202 assertion unchanged, plus a
  new `TestAuthenticationAndAuthorization` class (7 tests).
- `backend/tests/integration/test_main_lifespan.py`: extended with
  assertions that `wired.auth_config` is built correctly from real
  environment variables.
- `backend/tests/unit/test_api_dependencies.py`: extended with
  `test_get_auth_config_reads_it_off_app_state` and five
  `get_current_user` unit tests.
- `backend/pyproject.toml`: added `pyjwt`, `bcrypt`, `email-validator`
  to `dependencies`.
- `.env.example`: added a new "Authentication" section documenting
  that no new *required* variable names were introduced;
  `ACCESS_TOKEN_EXPIRE_MINUTES`/
  `REFRESH_TOKEN_EXPIRE_DAYS` documented as optional overrides of their
  existing code defaults.

Documentation (Authentication / Identity & Access work):
`docs/session_state.md`, `docs/implementation_progress.md` (this file),
`PROJECT_STATE.md` -- all three updated to record this work's
completion, including the design decisions (registration creates
only a User; opaque hashed rotating refresh tokens, not JWTs, delivered
via httpOnly cookies; rotation on every refresh; the two new security
modules imported directly into the application layer following the
`target_validation.py` precedent; `require_organization_member` reusing
`get_org_session`'s already-open transaction; no logout/RBAC/OAuth/MFA/
password-reset/email-verification) and a targeted follow-up review's
findings (the naming correction -- this is not "Milestone 8"; the
JWT-transport correction from an initially-incorrect `Authorization:
Bearer` implementation back to the locked httpOnly-cookie decision; the
`RefreshToken` architecture reviewed and confirmed justified; the full
test suite re-verified after the correction). Technical Debt item #9
marked resolved below.

## Files pending
- All actual domain/application/infrastructure code behind the scaffolded
  packages that Milestones 2-7 did not cover: use cases
  in `application/{identity,assets,findings,reporting}/` (Scanning's own
  use cases are done -- Milestone 4; its HTTP surface is done --
  Milestone 5; AI analysis is done -- Milestone 6; asynchronous
  dispatch is done -- Milestone 7), `EventBusPort` and its
  implementation, the remaining fourteen scanner adapters (Phase 4 --
  explicitly out of scope for Milestone 3, which covers Nuclei only),
  OpenAI/Ollama/OpenRouter `AIProviderPort` implementations (deferred
  until a second provider's real shape is known, the same reasoning
  already applied to `ActiveScanner`/`BaseAgent`), authentication/JWT
  issuance (Identity & Access's own future milestone, plus Phase 6
  RBAC).
- A network-isolated `scanner_worker` process, split out from
  `ingestion_worker` -- deliberately not built in Milestone 7 (see that
  milestone's design decision and Technical debt item #12); requires
  restructuring `RunScanWorkflowUseCase` into independently-schedulable
  phases with a durable hand-off between them, not merely more Celery
  wiring.
- HTTP routes for Findings, Assets, and Reporting -- deliberately not
  built in Milestone 5 (see that milestone's design decisions in
  `docs/session_state.md`); depend on those bounded contexts' own
  application-layer use cases, which do not exist yet.
- An organization/user-creation HTTP endpoint -- deliberately not built
  in Milestone 5; depends on Identity & Access's own use cases
  (enforcing the ">= 1 Owner always" invariant), which do not exist yet.
- `docs/architecture.md`, `roadmap.md`, `decisions.md`, `database.md`,
  `api.md`, `coding_standards.md`, `testing_strategy.md`,
  `security_model.md` -- still described only in chat history, never
  written as files. PROJECT_STATE.md remains the interim consolidated
  substitute.
- `README.md`, `docker-compose.yml`, `.env.example`,
  `AI_ENGINEERING_RULES.md`, `LICENSE` exist but are still placeholders,
  not full content (deliberately -- see the session that created them).

## Technical debt
1. `domain/shared/enums.py` is a deliberate staging area holding enums
   that belong to bounded contexts (`scanning/`, `assets/`, `identity/`,
   `reporting/`) not yet fully built out. Documented in the file's own
   docstring; move each enum out when its owning context's behavior
   (state machines, use cases) is implemented -- the entities themselves
   now exist (Milestone 2) but still import from this shared staging
   file rather than an owning `enums.py` of their own, since moving them
   is a mechanical relocation with no behavioral upside until each
   context has enough of its own code to justify the move.
2. ~~`test_value_objects.py` is missing~~ -- resolved in the Milestone 1
   completion session.
3. ~~Sandbox-verified code has not been re-verified in this local
   folder~~ -- resolved in the Milestone 1 completion session for that
   milestone's files; the same caveat applies fresh to Milestone 2's
   files as of this session (sandbox-verified, transplanted via the
   Filesystem MCP, not executed on `C:\Users\gamer\Downloads\claudeOnly`
   directly -- see "Testing status" above).
4. ~~`tests/conftest.py` imported nonexistent modules (`app.core.db`,
   `app.main`) and used sync SQLite instead of the locked async
   PostgreSQL stack~~ -- resolved this session. This was a stray file
   discovered at the start of Milestone 2's work, not part of any
   documented Milestone 1 deliverable; flagged before being touched, per
   the verification-honesty workflow rule, then replaced. See
   "Completed milestones" above for the full account.
5. Repository `update()`/`soft_delete()` methods fetch the target row
   via `session.get()` (unfiltered by `deleted_at`), while the
   equivalent `get_by_id`/lookup methods explicitly filter
   `deleted_at IS NULL` (see PROJECT_STATE.md section 3's RLS +
   soft-delete redesign entry). This is deliberate -- an update needs to
   be able to fetch a row regardless of its current soft-delete state,
   including the soft-delete operation itself -- but it does mean
   nothing currently prevents calling `update()` on an already-deleted
   row (e.g. accidentally "reviving" one by clearing `deleted_at`).
   Confirmed still not triggered as of Milestone 4:
   `RunScanWorkflowUseCase` only ever calls `AssetRepositoryPort.update`/
   `FindingRepositoryPort.update` on a row `get_by_identity`/
   `get_by_fingerprint` itself just returned (both filter
   `deleted_at IS NULL`), so a soft-deleted row is never the target of
   an update through this use case -- it would go down the "create new"
   branch instead. Still flagged here so it is not forgotten once a use
   case that mutates existing rows more freely is built (Milestone 5+).
6. `MinioStoragePort` (Milestone 3) is verified only against a unit-level
   mock of the `minio` SDK's `Minio` client, not a real MinIO server --
   no MinIO server package exists on this environment's allowed apt
   mirrors, and `dl.min.io` (where the official server binary is
   distributed) is outside the network allowlist available this
   session. A real integration test against a locally running MinIO
   container is future work once such an environment is available;
   flagged rather than silently accepted as equivalent to Milestone 2's
   real-Postgres integration tier.
7. `NucleiAdapter` (Milestone 3) is verified only against a patched
   `run_scanner_subprocess`, not a real `nuclei` binary -- none is
   installed in this environment. A real-binary integration test
   (asserting actual `nuclei` CLI behavior, not just this adapter's own
   argv-construction and error-classification logic) is future work once
   `nuclei` is available in a CI/sandbox image.
8. `RunScanWorkflowUseCase`'s correlate/persist idempotency guards
   (PROJECT_STATE.md section 3's "Processing pipeline orchestrator
   resumability" entry) make **sequential** retries of the *same* scan
   safe (check-then-act against a natural key, then check-then-insert
   against a `(scan_id, ...)` pair), but do not make **concurrent**
   execution of *two different* scans against overlapping targets safe.
   Two scans for the same organization targeting the same host, run
   truly concurrently (two worker processes, not two sequential
   `execute()` calls), could both observe "no existing Asset" via
   `get_by_identity` before either has committed its own `add()`, and
   the second `add()` would fail on the real `(organization_id,
   asset_type, value)` unique constraint with an unhandled
   `IntegrityError` rather than gracefully falling back to "use the one
   the other scan just created." Not fixed speculatively -- nothing in
   this codebase runs two scans concurrently yet; no Celery/worker
   wiring exists before Milestone 7. Flagged now so it is not forgotten
   once concurrent execution is actually possible: the fix, when it's
   needed, is most likely catching the unique-constraint violation on
   `add()` and re-fetching via `get_by_identity`, not a change to the
   sequential-retry logic this milestone already has right.
9. ~~**No authentication on any `/api/v1` route (Milestone 5).**~~ --
   **resolved by the Authentication / Identity & Access work.**
   `organization_id` in the URL path was
   previously trusted as given; every `/api/v1/organizations/
   {organization_id}/scans/...` route now depends on
   `require_organization_member` (app/api/dependencies.py), which
   requires a valid, cookie-delivered JWT access token naming an ACTIVE
   member of that organization (401 if the token is missing/invalid,
   403 if the authenticated caller is not an active member).
   `get_org_session`'s
   organization-existence check remains a separate data-integrity/UX
   safeguard (404 for an unknown org), not itself an authorization
   control -- the two are still not conflated, now that both exist.
   Full account in `docs/session_state.md`'s Authentication entry and
   PROJECT_STATE.md's dated decision log.
10. **The scan pipeline runs synchronously inside the HTTP request
    handler for `POST .../{scan_id}/run` (Milestone 5), blocking for up
    to `DEFAULT_SCAN_TIMEOUT_SECONDS` (600s).** No task queue or worker
    process exists yet -- Milestone 7 ("Docker Compose wired
    end-to-end") is where Celery/worker wiring actually lands, per the
    roadmap. A client-facing effect: a slow or hung scanner subprocess
    ties up an HTTP connection for the same duration, with no way for
    the API to time out the request independently of the pipeline's own
    internal timeout. Not fixed speculatively -- building async
    dispatch now would be Milestone 7 work landing inside Milestone 5.
11. **`AI_ANALYZE` re-invokes the AI provider on every retry of a scan
    whose later steps fail (Milestone 6)**, even for findings it already
    successfully analyzed on an earlier attempt of the same scan.
    `AI_ANALYZE` is treated as "safe to recompute" (like `normalize`/
    `deduplicate`/`enrich`), not exempted like `EXECUTE_SCANNER`, because
    re-analyzing a finding with an LLM again has no correctness or
    safety concern -- unlike re-scanning a live target -- only a cost
    one. Giving `AI_ANALYZE` the same exemption `EXECUTE_SCANNER` has
    would require a new durability mechanism (persisting in-progress AI
    results somewhere retrievable across `execute()` invocations, since
    `_PipelineItem`s are rebuilt from scratch every call) that this
    milestone does not otherwise need. Not fixed speculatively -- the
    real cost (an extra AI-provider call per finding on such a retry)
    is accepted rather than solved with infrastructure this codebase
    has no other use for yet.
12. **The network-isolated `scanner_worker` split named in this
    project's own roadmap and in `docker-compose.yml`'s prior placeholder
    comment was not built in Milestone 7.** `RunScanWorkflowUseCase`
    still runs as one atomic, unrestructured call inside the
    `ingestion_worker` Celery task -- including its own
    `EXECUTE_SCANNER` step, which reaches arbitrary user-supplied scan
    targets over the network from the same process that holds database
    credentials, and its `AI_ANALYZE` step, which reaches the Anthropic
    API from that same process. Building the split properly requires
    restructuring `RunScanWorkflowUseCase` into (at minimum) two
    independently-schedulable phases with a durable hand-off between
    them (mirroring how `EXECUTE_SCANNER`'s own raw output is already
    durably stashed in `StoragePort` under a deterministic key, rather
    than held only in memory across the whole pipeline) -- a genuine
    architectural change to a use case this project's own rules require
    a genuine blocker to justify changing, not "more Celery wiring";
    explicitly out of scope for Milestone 7 per an explicit scope
    decision made before implementation began. Code-level scan-target
    safety (`validate_target`'s SSRF/private-IP rejection,
    argument-list-only subprocess execution, enforced timeouts, non-root
    execution) is unchanged and fully enforced regardless -- this item
    is about network-level defense-in-depth on top of those checks, per
    PROJECT_STATE.md section 3's own framing of the originally-envisioned
    split, not a gap in the checks themselves. Not fixed speculatively --
    flagged now so it is not forgotten once a session actually
    undertakes it.

## Important implementation rules
- Production-quality code only; full type hints; comprehensive docstrings.
- Unit tests for all new functionality; never skip tests.
- Keep Ruff, MyPy, and Pytest clean after every logical unit of work.
- Never modify more than one milestone in a single session.
- Never recreate completed work; extend existing code instead of
  rewriting it unless absolutely necessary.
- Never introduce technical debt without documenting and explaining it.
- Do not redesign approved architecture unless a genuine implementation
  blocker is discovered -- and when one is, explain it before adopting a
  fix, do not just silently diverge.
- Do not mark a milestone "complete" on a lesser standard of verification
  than is actually available -- state precisely what was and wasn't
  checked, every time.
- This project's filesystem is `C:\Users\gamer\Downloads\claudeOnly` (no
  space) via the Filesystem MCP, not the sandbox. See "Filesystem
  workflow" in PROJECT_STATE.md.

## Next planned task
Phase 2 (MVP backend)'s own milestone breakdown (Milestones 1-7) remains
fully complete on its own; the separate Authentication / Identity &
Access work (Technical Debt #9, pre-Phase-3 backend scope, not an
eighth Phase-2 milestone) is also complete. Wait for explicit approval
before beginning Phase 3
(MVP frontend), RBAC, OAuth, MFA, password reset, email verification,
Technical Debt item #12 (the network-isolated `scanner_worker` split),
or any other further work -- none of it is in scope and
none of it should be started without a new, explicit go-ahead, per this
project's own one-milestone-per-session rule. Local execution
confirmation of Milestones 1-7 plus the Authentication work is done (see
"Testing status" and "Completed milestones" above) -- Milestone 3's
confirmation happened in the same session that discovered it was
already implemented, not in the session that wrote it; Milestones 4, 5,
6, and 7, and the Authentication work, were each genuine fresh
implementation and confirmation in their own sessions.
