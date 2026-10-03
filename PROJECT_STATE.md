# PROJECT_STATE.md

Compact operational handoff -- read first each session. States **how things
are now**; per-milestone history is in `docs/implementation_progress.md`,
latest-session handoff in `docs/session_state.md`. Rewritten 2026-10-02
(Phase 6, sandbox) from the ~131 KB prior version. That prior version (full
per-milestone verification narratives, per-session accounts) is to be
preserved byte-exactly as `old historic project state.md` by renaming the
real `PROJECT_STATE.md` at the final repo sync -- it is not present in the
sandbox, and the real file already contains the Phase 6 M1 edits made before
the sandbox-only rule. The repository on disk is ground truth over this file;
re-read real files before editing; flag conflicts, never silently pick one.

**Workflow rule for Phase 6 (user, 2026-10-02):** do and verify all work in
the Claude sandbox only. The real repository is updated once, after ALL of
Phase 6 is complete and verified (transfer, rename the old state file,
commit). Do not copy to / modify the real repo, commit, or push before that.

---

## 1. Status and roadmap

| Phase | Status |
|---|---|
| 1 Architecture/schema/API design | Approved |
| 2 MVP backend (scan -> AI analysis) | **Complete**: Milestones 1-7, plus Auth/Identity (TD #9, "not Milestone 8") |
| 3 MVP frontend | Steps 1-4 complete (scaffold, API client, auth/session, org + scan UI); **Step 5 (tests) awaiting a scope decision** |
| 4 Scanner adapters | Partial: `nuclei`, `nmap` wired + normalized; `sqlmap` adapter-only (no wiring, no normalizer); `burp`/`zap` empty stubs. No complete roster is defined anywhere |
| 5 AI + RAG | **Milestones 1-5 complete**; real Docker build and real Qdrant never verified (TD #19/#20) |
| 6 Multi-tenancy hardening, RBAC, orgs/teams, billing | **M1 complete** (RBAC on scan routes). **M2 NOT started and not defined** -- its scope must be chosen and approved first |
| 7-10 Dashboard; DOCX/PDF/scheduling/notifications; security hardening; deployment | Not started |

Phase 3 backend preparation (not a milestone, done): CORS wiring,
`POST /api/v1/organizations`, and local HTTPS (mkcert) for frontend + backend,
which resolved the Secure-cookie-over-HTTP blocker with **no backend change;
`secure=True` stays locked** (setup in `frontend/README.md`; backend runs with
`--ssl-keyfile/--ssl-certfile` from the same mkcert cert and
`CORS_ALLOWED_ORIGINS=https://localhost:3000`). `POST /auth/logout` also added
(not a milestone).

## 2. Architecture

Clean Architecture, modular monolith. `domain` = zero framework/DB imports;
`application` = use cases + ports; `infrastructure`, `scanner_engine`,
`ai_agents` = outer adapters (the latter two top-level modules). Boundaries by
import discipline. Bounded contexts: **Identity & Access** (orgs, users,
roles, memberships, audit logs) / **Asset Intelligence** / **Scanning**
(scans, scopes, workflow steps, adapters) / **Findings & Analysis** /
**AI Platform** / **Reporting**.

Ports: `ScannerPort` (`ActiveScanner`/`ImportScanner` split -- import-only
tools have no `execute()`), `AIProviderPort`, `EmbeddingPort`,
`VectorStorePort`, `StoragePort`, repository ports per context,
`RefreshTokenRepositoryPort`. `EventBusPort` planned, not built.

Stack: Next.js 15.5.25/TS/Tailwind/shadcn (Vitest) | FastAPI, Python 3.12,
SQLAlchemy async, Alembic | PostgreSQL + RLS | Redis + Celery | MinIO
(`StoragePort`) | Qdrant (`VectorStorePort`) | `sentence-transformers`
`all-MiniLM-L6-v2`, CPU-only (`EmbeddingPort`) | Anthropic only (OpenAI/
Ollama/OpenRouter deferred) | JWT httpOnly cookies + opaque rotating refresh |
ULID ids as native UUID | pytest, Ruff, MyPy strict | Docker Compose,
network-segmented.

## 3. Locked decisions (revisit only for a genuine blocker; explain first)

**Data / RLS**
- RLS enforces tenant isolation only (`organization_id`; `id` for
  `organizations`). It is NOT combined with soft delete (Postgres checks
  `SELECT USING` against the new row of an `UPDATE`, so soft delete would
  reject itself). Soft-delete visibility = explicit `WHERE deleted_at IS NULL`
  in repository reads.
- Every datetime column: `DateTime(timezone=True)` (naive default breaks
  asyncpg with tz-aware `utcnow()`; a real past bug). `StrEnum` columns use
  `native_enum=False`. IDs: ULID generated in `domain/shared/ids.py`.
- Soft delete only on current-state tables (orgs, users, assets, findings,
  scans, reports); never on append-only logs or tables with their own
  lifecycle field.
- Findings dedup key `(org_id, fingerprint)`; occurrences keep per-scan
  history. Assets = current-state cache keyed `(org_id, asset_type,
  normalized value)`, observations append-only.
- `DomainEvent` not `slots=True`; `utcnow()` a plain function (YAGNI).

**Pipeline**
- Eight stages, fixed order, one `scan_workflow_steps` row each:
  `validate_target -> execute_scanner -> normalize -> deduplicate ->
  correlate -> enrich -> ai_analyze -> persist`. Only `EXECUTE_SCANNER` is
  exempt from retry recomputation (re-reads raw output from `StoragePort`);
  others recompute, including `AI_ANALYZE` (cost only, TD #11). A step
  failure is recorded and reflected in `Scan.status`, not raised. The
  `AI_ANALYZE` result is stashed on `_PipelineItem.ai_analysis`, written by
  `_persist`; per-finding `AIProviderError`/`AnalysisError` is caught, never
  fails the step.
- **Scanner-native severity is never written to `ai_severity_level`** (an AI
  estimate); raw severity goes in `FindingOccurrence.raw_evidence`.
- No scanner registry / `NormalizerPort` / `BaseAgent` (YAGNI):
  `AppState.active_scanners` is a plain tuple; `_select_active_scanner`
  (`app/workers/tasks.py`) and `normalize_scan_output` are explicit if/elif.
- `POST .../run`: 404 missing scan, 409 `scanner_name` not wired, dispatch
  Celery, **202**; an already-RUNNING scan is not re-dispatched.
  `RunScanWorkflowUseCase` runs unmodified inside one `ingestion_worker` task.

**Network / Docker**: `backend` (API) on `internal` only -- no internet, no
MinIO/Anthropic credentials. `worker` on `internal` + `queue` +
`worker-egress` (needs DB + internet; no `scanner_worker` split, TD #12).
`ANTHROPIC_API_KEY` is required only for `worker_role=ingestion_worker`.

**Auth / security**
- JWT delivered **only** via httpOnly/Secure/SameSite=Lax cookies, never a
  JSON body (an `Authorization: Bearer` implementation was once built and
  corrected back). `SameSite=Lax` is the CSRF baseline; double-submit token
  not built. Access token: stateless JWT, 15 min, unrevocable before expiry.
  Refresh token: opaque random, SHA-256-hashed, rotated on every
  `/auth/refresh`; family-wide reuse detection NOT built.
- `password_hashing.py`, `token_service.py`, `target_validation.py` are
  imported directly into the application layer (no port; zero framework
  imports, one real impl).
- `RegisterUserUseCase` creates only a `User` (never an org/membership).
- `get_current_user` -> 401; `require_organization_member` -> 403 unless
  ACTIVE, reusing `get_org_session`'s single RLS-scoped transaction.
  `get_org_session` returns 404 for a missing org.
- `validate_target`: DNS-resolving SSRF guard (private/loopback/link-local/
  cloud-metadata rejected); scanner subprocess = argument list, timeout,
  non-root guard.

**RBAC (Phase 6 M1, matrix decided explicitly 2026-10-02)**

| Role | Read scans | Create / run scans |
|---|---|---|
| OWNER | yes | yes |
| ADMIN | yes | yes |
| MEMBER | yes | yes |
| VIEWER | yes | **no -> 403** |

Policy = pure domain logic in `app/domain/identity/access.py`
(`SCAN_WRITE_ROLES`, `can_write_scans`). `require_scan_write_access`
(`app/api/dependencies.py`) sits on top of `require_organization_member`
(unchanged: still owns 401/404/ACTIVE-403 and the transaction) and only maps a
denial to 403. Applied to `create_scan` and `run_scan`; `get_scan` is
unchanged (any ACTIVE member). Dependencies resolve before the handler, so a
VIEWER gets 403 (never 404) even for a nonexistent scan, and nothing is
written/dispatched. Role comes from the member row already loaded -- no extra
query, **no schema/migration/RLS change**. No permission framework: a new
role-gated action adds its own constant in `access.py` when a real route
needs it. ADMIN == MEMBER in capability today (nothing distinguishes them).

**Scanner decisions and permanent exclusions**
- **ReconX and BugHunter PRO are permanently excluded** (the owner's own
  separate earlier projects): never reuse, port, reconstruct, or reference
  them. Their stub folders are unused; a 2026-10-02 directory listing shows
  they no longer exist (older docs said they could not be deleted).
- The "14 remaining adapters" figure had no source and is retired.
- `nmap`: `-sT -Pn`, one validated hostname (never a range), output
  `nmap-xml`; non-zero exit is always a failure. Normalizer: one `info`
  finding per open port on each up host, no CVE/CVSS, `template_id =
  open-port-{protocol}-{portid}`.
- `sqlmap`: `-u validated.original`, `--batch --risk=1 --level=1
  --technique=BEUT` (no stacked queries), output `sqlmap-stdout`; empty output
  is always a failure, non-zero exit tolerated. **Deliberately NO normalizer**
  (TD #18): `normalize_scan_output` raises `UnsupportedScanOutputFormatError`
  rather than parse undocumented text. Re-investigated once: no real fixture
  exists (the repo's only sample is documented as fabricated). Revisit only
  with a documented format, a real fixture, or an explicit human decision --
  never by guessing stdout format from memory.
- `nuclei`: output `nuclei-jsonl`.

**Phase 5 RAG**
- `EmbeddingPort` is separate from `AIProviderPort`; `VectorStorePort` binds
  one collection per instance, exactly three ops (`ensure_collection`,
  `upsert`, `search`); Qdrant adapter uses the native async client, cosine.
  Point ids must be UUID/uint: `uuid5(NAMESPACE_URL,
  "https://cwe.mitre.org/data/definitions/{id}.html")`.
- Corpus = vendored `backend/data/cwe/2025_top25.xml` (MITRE CWE View-1435,
  placed by the owner -- never download or reconstruct it from memory). Parser
  raises `CweSourceError` unless root name matches and exactly 25 weaknesses.
  Ingestion (`app/application/knowledge/ingest_cwe_top25.py`: three pure
  functions + `IngestCweTop25UseCase`, `CORPUS_NAME="cwe_top25"`) is static,
  one-time; no refresh mechanism.
- Retrieval lives inside `AnalysisService` (optional injected ports, top-k 3),
  not the workflow use case. Retrieved text is labeled reference context, not
  instruction. Any retrieval failure or zero matches degrades to the exact
  pre-RAG prompt. `PROMPT_VERSION="v2"`. Provenance in
  `model_metadata["retrieval"]` (never a raw vector); `kb_version` populated
  as `"<corpus>:<version>"` (e.g. `cwe_top25:2025`) when there is a match.
- Wiring is in `app/workers/tasks.py::_build_analysis_service`, never
  `app/main.py`; RAG adapters imported lazily so the API image never needs
  them. `qdrant_url` is deliberately NOT validated (a scan runs without Qdrant).
  `EMBEDDING_VECTOR_SIZE=384`. Packaging: `rag` optional group in
  `pyproject.toml`; `Dockerfile` build arg `INSTALL_RAG_DEPENDENCIES`
  (default false) installs CPU-only torch; only `worker` opts in.

**Frontend**: route protection is client-side only (no `middleware.ts`; never
the security boundary). Selected org id in `localStorage` (not a credential).
One silent `/auth/refresh` retry on 401; the session is restored on mount via
that same refresh call (there is no `/auth/me`). Next pinned 15.5.25 (TD #13).

## 4. Code map (backend)

`backend/app/`: `main.py`, `config.py` (role-based fail-fast validation in
`check_role_boundaries`); `domain/{shared,findings,scanning,assets,identity,
reporting}` (plain dataclasses; `identity/access.py` = RBAC policy);
`application/{interfaces,scanning,identity,knowledge}` (`assets/findings/
reporting` are empty scaffolds); `api/dependencies.py`,
`api/v1/{scans,auth,organizations,schemas,auth_schemas,organization_schemas}`,
`api/internal/health.py`; `infrastructure/{db,storage,security,ai_providers,
embeddings,vector_store}` (19 tables, 5 aggregate + refresh-token repos;
`event_bus`/`observability` empty); `scanner_engine/adapters/{nuclei,nmap,
sqlmap,burp,zap}`; `ai_agents/analysis_service.py`; `workers/{celery_app,
tasks}`. `backend/alembic/versions/6bdbf0ab25b0_initial_schema.py` = 19
tables + 15 RLS policies. Tests: `backend/tests/unit` (37 files) and
`integration` (14 files + `support.py`, real Postgres) as of 2026-10-02.
Not built: Findings/Assets/Reporting use cases and routes, `/internal/metrics`
and `/internal/admin`, org list/get/rename, member management. Eight planned
`docs/*.md` files were never written (this file is the interim substitute).

Domain notes: Finding state machine `new -> triaged -> {confirmed,
false_positive} -> {fixed, accepted_risk, wont_fix}`, `effective_severity`
prefers CVSS over AI; Scan `queued -> running -> {completed, failed,
cancelled}` derived from steps via `derive_scan_status()`; "at least one
Owner always" is NOT enforced by any use case yet; most state-machine
transition enforcement still needs future use cases.

## 5. API contracts (`/api/v1`)

- **Scans** `/organizations/{org}/scans`: all need `require_organization_member`.
  `POST /` create (`scanner_name` `Literal["nuclei","nmap"]`, default nuclei;
  records `triggered_by_user_id`) and `POST /{id}/run` (202) additionally
  need role OWNER/ADMIN/MEMBER (VIEWER 403). `GET /{id}` any ACTIVE member.
- **Auth** `/auth`: `register` (201, User only, no cookies), `login`,
  `refresh` (cookie-driven, rotates), `logout` (204; tolerant of
  missing/revoked token; does not require an access token).
- **Organizations**: `POST /` (201; creates org + OWNER/ACTIVE membership for
  the caller; 409 duplicate slug via the DB unique constraint -- a slug
  pre-check cannot work under the self-referential `organizations` RLS
  policy). Only `get_current_user` required. No list/get/rename routes.
- **Internal**: `GET /internal/health/live`, `/internal/health/ready`.

## 6. Configuration

Required env: `DATABASE_URL`, `TEST_DATABASE_URL`, `REDIS_URL`,
`MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD/BUCKET`, `JWT_SECRET`,
`AI_DEFAULT_PROVIDER` (anthropic), `AI_MODEL` (claude-sonnet-4-5),
`ANTHROPIC_API_KEY` (ingestion_worker only). Optional:
`ACCESS_TOKEN_EXPIRE_MINUTES` (15), `REFRESH_TOKEN_EXPIRE_DAYS` (30),
`QDRANT_URL` (enables RAG on the worker), `CORS_ALLOWED_ORIGINS`
(comma-separated; unset = CORS off; read from raw `os.environ` by
`get_cors_allowed_origins()`, deliberately not via `Settings`/dotenv, and not
yet in `docker-compose.yml`). Runtime deps: pydantic(-settings), python-ulid,
sqlalchemy[asyncio], asyncpg, alembic, minio, fastapi, uvicorn[standard],
anthropic, **celery[redis]** (a past sandbox pyproject wrongly had bare
`celery`), pyjwt, bcrypt, email-validator; `rag` extra: sentence-transformers,
qdrant-client. Dev deps: pytest, pytest-cov, pytest-asyncio, ruff, mypy, httpx,
celery-types. Add no dependency before code imports it.
Local `backend/.venv` appears to be CPython 3.13 (docs say 3.12;
`requires-python >= 3.12`).

## 7. Verification state -- read this before trusting any "passed"

**Standing limits.** No command-execution tool exists against the real
repository (project root `C:\Users\gamer\Downloads\claudeOnly`, reached via
the Filesystem MCP only; the sandbox is disposable verification scratch: no
delete tool, nested `create_directory`
unsupported, one file per write, reads cap ~500 KB, large writes unstable).
**Every result below is a sandbox result** (Claude's own rebuilt slice of the
repo with real PostgreSQL 16 and a non-superuser role so RLS is genuinely
enforced), never a run on the real repo or on the owner's machine. Sessions
rebuild only the files they touch; most of the suite is not re-run. No Docker
build, no real MinIO/Qdrant/nuclei/nmap/sqlmap, ever. A skipped test is not a
pass. Recommended: run `pytest tests/ -q`, `ruff check .`, `ruff format
--check .`, `mypy app` and a real `docker build --build-arg
INSTALL_RAG_DEPENDENCIES=true` where execution exists (e.g. Claude Code).

| Work | Last recorded sandbox result |
|---|---|
| Phase 2 M1-M7 | M1 55 unit; M2 102 (64 unit + 38 integ); M3 105 unit; M4 +44; M5 +28; M6 +84; M7 +12. Docker never built |
| Auth work | 91/92 (one RLS-sandbox artifact) |
| Phase 3 backend prep | 101/101 on a non-superuser role (found + fixed the slug pre-check bug) |
| Logout | 135/135 |
| Frontend Steps 1-4 | Vitest 47/47; Playwright/Chromium vs real uvicorn + PG16 + Redis **49/49** with zero product-code changes. Not verified: a real scan executing (Run checked to a real 202 + Celery enqueue only), the owner's mkcert trust chain/browser |
| Nmap adapter / wiring / normalizer | 20 / 70 / 89 passed |
| SQLMap adapter (+ TD #18 re-check) | 99 passed |
| Phase 5 M1 / M2 / M3 | 8 / 17 / 40 (23 new) passed |
| Phase 5 M4 | `test_analysis_service.py` 33 (20 new); `test_run_scan_workflow.py` not re-run (file unchanged) |
| Phase 5 M5 | 82 unit passed; `test_scan_worker_task.py` NOT re-run; no Docker build; no real Qdrant |
| **Phase 6 M1** | **52 passed, 0 skipped** on PG16 / non-superuser `app_user` (21 unit + 31 integration); mutation check: reverting the routes to the old gate failed exactly the 4 role tests; Ruff, format, MyPy strict (70 files) clean; re-verified in the sandbox 2026-10-02. NOT run on the real repo; sandbox slice used stubs (Celery tasks, scanner adapters, config validator, `run_scan_workflow`) and a hand-condensed migration (same 19 tables / 15 RLS policies); only `access.py` is byte-identical to the repo |

Known unfixed observations: a pre-existing Ruff `E501` on
`app/api/dependencies.py` (`require_organization_member`'s 103-char 403
message line) -- deliberately untouched; `pytest --cov` under-reports
async-DB code (TD #24). Older docs' test-file counts (30 unit/14 integ,
integration "13") are stale.

## 8. Technical debt (open)

| # | Item |
|---|---|
| 1 | `domain/shared/enums.py` staging area; move enums into their owning contexts as built |
| 5 | Repository `update()`/`soft_delete()` use unfiltered `session.get()` -- can revive a soft-deleted row (no use case triggers it) |
| 6, 7, 15, 17, 20 | MinIO, nuclei, nmap, sqlmap, Qdrant verified only against mocks/patches (no real server/binary; compose `qdrant` has no host port) |
| 8 | Concurrent scans of the same org+host can hit an unhandled unique-constraint `IntegrityError`; fix = catch + re-fetch (no concurrency exists yet) |
| 11 | `AI_ANALYZE` re-calls the provider on every retry |
| 12 | No network-isolated `scanner_worker` split (needs a use-case restructure with a durable hand-off); code-level scan safety unaffected |
| 13 | Next 15.5.25 pinned; one accepted residual PostCSS advisory in Next's bundled build tooling; Next 16 upgrade needs its own session |
| 14 | `frontend/package-lock.json` not committed (run `npm install`, commit) |
| 18 | No `sqlmap-stdout` normalizer (deliberate; see section 3) |
| 19 | CPU-only PyTorch split coded (rag extras + `INSTALL_RAG_DEPENDENCIES`) but never `docker build`-verified |
| 21 | CWE file's 25-entry count not exhaustively enumerated (19/25 sampled, root/namespace/mtime consistent); the parser's runtime assertion fails loudly at first real ingestion |
| 22 | No role awareness in UI/API responses (VIEWER learns only after a 403; no `me`/list-orgs endpoint); non-OWNER roles unreachable until a member flow exists |
| 23 | RBAC covers only the three scan routes; ADMIN == MEMBER; denials are not audited (`audit_logs` + port exist, nothing writes) |
| 24 | No `concurrency = ["greenlet","thread"]` in pyproject coverage config; `--cov` under-reports async DB code (100% vs 57% on `scans.py`). Not changed |

Resolved: #9 (auth), #10 (async dispatch), #16 (nmap normalizer). TD #2-#4 do
not appear in the prior file's debt list (numbering gap); their status is not
recorded in current docs.

## 9. Deferred / not started

- Phase 6 remaining (no breakdown, **M2 not started**): teams, billing,
  member management/invitations (`OrganizationRepositoryPort` has
  `list_members`/`update_member`/`count_active_owners`; no use case or route
  calls them), org list/get/rename, `/internal/admin`, audit-log writes, other
  multi-tenancy hardening; also "list my organizations" endpoint.
- Findings/Assets/Reporting use cases + HTTP surface; `EventBusPort`;
  OpenAI/Ollama/OpenRouter providers; `scanner_worker` split (TD #12);
  OAuth, MFA, password reset, email verification, CSRF double-submit; SQLMap
  pipeline/API wiring (TD #18 would still block completion); `burp`/`zap`
  (`ImportScanner`-shaped, different architecture); Phase 3 Step 5; Next 16.
- Any further RAG work (second knowledge source, tuning, refresh) is new
  scope, not a Phase 5 carry-over.

## 10. Rules that never change

- Ruff (lint + format) + MyPy strict; full type hints; module docstrings
  explain *why*. Domain: zero framework imports; value objects validate on
  construction. Repository pattern for all DB access; routes call use cases
  only, no business logic in handlers.
- One milestone per session; stop after it and update docs. Fix lint/type
  issues immediately; document tech debt the moment it is introduced; unit
  tests for every new function/class in the same session; fakes/spies for unit
  tests, real implementations only in the smaller integration suite; never
  assert exact LLM text.
- No redesign without a genuine implementation blocker (explain it first);
  section 2-3 boundaries/decisions are locked. Never recreate completed work;
  extend, don't rewrite; never skip tests; no speculative abstractions or
  frameworks; no unrelated cleanup.
- Do not weaken RLS, RBAC, audit/security boundaries, secure cookies, Docker
  network isolation, or worker separation for convenience.
- Never fabricate: no invented scanner output parsers, knowledge sources,
  adapters, or counts; state exactly what was and was not verified; never
  claim runtime/real-repo verification that did not run; distinguish
  sandbox/mocked verification from real integration.

## 11. Current next step

Phase 6 M1 is complete and sandbox-verified. **Phase 6 M2 has NOT started and
is not defined** -- wait for the owner's explicit instruction to define it; do
not begin Phase 6 M2 or Phase 7 unprompted. Open decisions awaiting the owner:
Phase 3 Step 5's scope; Phase 6 M2's scope. At the end of Phase 6: sync the
verified sandbox state to the real repo (rename the old `PROJECT_STATE.md` to
`old historic project state.md` byte-exactly, add this file), run the real
test suite where execution exists, then commit.
