# PROJECT_STATE.md

Compact canonical current-state reference — read this first each session.
Describes **how things are now**, not the history of how they got there;
full narrative/rationale/verification detail lives in
`docs/implementation_progress.md` (never pruned, cumulative). Latest
single session's own account: `docs/session_state.md`.

Compacted 2026-08-27 from ~1000 lines. No architectural decision was
changed, added, or removed — only prose narrative already duplicated in
`docs/implementation_progress.md` was cut from here.

---

## 1. Current phase & status

**Phase 2 (MVP backend), Milestones 1–7: complete.** Separately, the
Authentication / Identity & Access work (Technical Debt #9, pre-Phase-3
backend scope, *not* an eighth Phase-2 milestone) is also complete.

**Phase 3 backend preparation (not a milestone, not Phase 3 itself):**
two of three frontend-blockers resolved — CORS (`CORSMiddleware` wiring)
and organization bootstrap (`POST /api/v1/organizations`). The third
(httpOnly+Secure cookies cannot be set by a browser over plain
`http://localhost` in local frontend dev) is **not resolved** — it
conflicts with this file's own locked cookie-transport decision (§4) and
requires an explicit human decision, not a silent code change. See §16.

No Phase 3 (frontend) implementation approved or started yet. **Wait
for explicit approval before starting new work** — see §16.

## 2. Architecture summary

Clean Architecture: `domain` has zero framework/DB imports;
`application` holds use cases and defines *ports*; `infrastructure`,
`scanner_engine`, `ai_agents` are outer-layer adapters. `scanner_engine`/
`ai_agents` are top-level modules (core extensibility points), not
buried in `infrastructure/`. Modular monolith, not microservices —
boundaries enforced by import discipline, not network calls. Scanning
is the most likely future service-split candidate.

Bounded contexts (DDD):

| Boundary | Owns | Reads from |
|---|---|---|
| Identity & Access | orgs, users, roles, permissions, memberships, audit logs | — |
| Asset Intelligence | assets, observations, dedup/confidence, relationships | org_id from Identity |
| Scanning | scans, scan_scopes, workflow steps, adapter invocation | Identity, Asset Intelligence |
| Findings & Analysis | findings, finding_occurrences, finding_analyses, triage state machine | scan_id, asset_id |
| AI Platform | agent orchestration, provider routing | Findings data via injected context |
| Reporting | reports | Findings, Scanning |

Ports defined: `ScannerPort` (`ActiveScanner`/`ImportScanner` split),
`AIProviderPort`, `StoragePort`, `RefreshTokenRepositoryPort`, plus a
repository port per bounded context. `EventBusPort` — planned
(lightweight in-process/Celery dispatcher, not a broker), not yet built.

## 3. Technology stack

| Layer | Choice |
|---|---|
| Frontend | Next.js/TS/Tailwind/shadcn (Phase 3 — not started) |
| Backend | FastAPI, Python 3.12, SQLAlchemy async, Alembic |
| Database | PostgreSQL with Row-Level Security |
| Cache/queue | Redis, Celery |
| Object storage | MinIO, behind `StoragePort` |
| Vector DB | Qdrant (running, unused until Phase 5 RAG) |
| AI providers | Anthropic (built); OpenAI/Ollama/OpenRouter deferred, behind `AIProviderPort` |
| Auth | JWT httpOnly cookies (access) + opaque hashed/rotating refresh token (also httpOnly cookie) |
| IDs | ULID, generated in app code, stored as native Postgres UUID |
| Testing | pytest, pytest-cov, Ruff, MyPy strict |
| Containers | Docker Compose, network-segmented |

## 4. Locked architectural decisions

Revisit only with a genuine implementation blocker — explain before
diverging (§14). Full rationale: `docs/implementation_progress.md`.

- **RLS** enforces tenant isolation only (`organization_id`, or `id` for
  `organizations`) — **not** combined with soft-delete filtering
  (Postgres checks a table's `SELECT USING` clause against the *new*
  row on every `UPDATE`, so `deleted_at IS NULL` in the policy makes a
  soft delete reject itself). Soft-delete visibility is an explicit
  `WHERE deleted_at IS NULL` in each repository's read methods instead.
- `ActiveScanner`/`ImportScanner` split on `ScannerPort` — import-only
  tools (Burp/ZAP manual export) have no `execute()`.
- ULIDs, generated in `domain/shared/ids.py`, stored as native Postgres
  `UUID` columns. `StrEnum` over free-text columns via
  `native_enum=False` (VARCHAR + CHECK), not native Postgres `ENUM`.
- Soft delete (`deleted_at`) on current-state tables only (orgs, users,
  assets, findings, scans, reports) — never on append-only logs
  (`audit_logs`, `finding_status_history`, `asset_observations`,
  `finding_analyses`) or tables with their own lifecycle field
  (`organization_members.status`, `refresh_tokens.revoked_at`).
- Findings dedup: `findings` keyed by `(org_id, fingerprint)`; a
  recurrence updates `last_seen_at` on the existing row.
  `finding_occurrences` keeps per-scan history underneath.
- Assets: `assets` is a current-state cache, identity =
  `(org_id, asset_type, normalized value)`; `asset_observations` is
  append-only history. No direct `finding → asset_observation` FK
  (derivable via shared `scan_id` + `asset_id`).
- **Eight-stage pipeline, locked order:** `validate_target →
  execute_scanner → normalize → deduplicate → correlate → enrich →
  ai_analyze → persist`, as explicit `scan_workflow_steps` rows (not one
  monolithic task) — resumability + per-step timing. `EXECUTE_SCANNER`
  alone is exempt from retry recomputation (never re-invokes the real
  scanner once `COMPLETED`; re-reads raw output from `StoragePort`).
  Every other step recomputes safely on retry (pure reads, or guarded
  by natural-key/`(scan_id, …)` checks) — including `AI_ANALYZE` (cost,
  not correctness/safety, concern — TD #11). `AI_ANALYZE`'s result is
  stashed on `_PipelineItem.ai_analysis` (mirrors `_enrich`'s CVSS
  pattern, since no `Finding.id` exists until `PERSIST`); `_persist`
  writes `ai_severity_level` and appends `FindingAnalysis`. A
  per-finding `AIProviderError`/`AnalysisError` is caught/logged, never
  fails the step.
- Scanner-native severity is never written to `Finding.ai_severity_level`
  (that field means an AI provider's own estimate) — raw scanner
  severity travels in `FindingOccurrence.raw_evidence` instead.
- No `BaseAgent`/`NormalizerPort`/scanner registry yet — YAGNI, exactly
  one concrete implementation exists for each; build the abstraction
  only when a second real shape exists.
- `POST .../scans/{scan_id}/run` dispatches Celery async, returns `202
  Accepted` (resolved TD #10): checks scan exists (404) and
  `scanner_name` matches the wired adapter (409) before dispatch; does
  not re-dispatch an already-`RUNNING` scan. `RunScanWorkflowUseCase`
  itself runs unmodified inside one `ingestion_worker` Celery task (no
  scanner_worker split — TD #12).
- Network segmentation (`docker-compose.yml`): `backend` (API) is on
  `internal` only, no internet route, no MinIO/Anthropic credentials.
  `worker` is on `internal` + `queue` + `worker-egress` — has both DB
  and internet access, since `EXECUTE_SCANNER`/`AI_ANALYZE` both need
  it and no split exists yet (TD #12).
- **Auth transport: JWT delivered exclusively via httpOnly cookies**
  (`Set-Cookie`), never a JSON body — locked; briefly implemented as
  `Authorization: Bearer` before being corrected back. `SameSite=Lax`
  is the CSRF baseline; a double-submit token awaits Phase 3's frontend
  origin. Access tokens: stateless signed JWTs (15 min default,
  unrevocable before expiry). Refresh tokens: opaque, random, hashed
  values (not JWTs), looked up by SHA-256 hash, rotated (revoked and
  reissued) on every `/auth/refresh` call. Family-wide
  revocation-on-reuse-detection not built (flagged, not solved).
  `password_hashing.py`/`token_service.py` are imported directly into
  the application layer, not behind a port — same precedent as
  `target_validation.py` (zero framework/DB imports, one real impl).
- `RegisterUserUseCase` creates only a `User` row — never an
  Organization/OrganizationMember (would bypass or prematurely enforce
  the "≥ 1 Owner always" invariant). Org membership is a future
  invite/membership flow.
- `require_organization_member` reuses `get_org_session`'s already-open
  transaction — not a second DB connection.
- Every `datetime` column requires explicit `DateTime(timezone=True)` —
  the bare SQLAlchemy default is timezone-naive and asyncpg rejects this
  codebase's timezone-aware `utcnow()` values against it (a real,
  previously-hit bug, not a style preference).
- `DomainEvent` base is not `slots=True`; `utcnow()` is a plain function,
  not an injectable `Clock` — both YAGNI until a real need appears.

## 5. Folder structure (current)

Root: `PROJECT_STATE.md`, `README.md`, `docker-compose.yml`,
`.env.example`, `AI_ENGINEERING_RULES.md`, `LICENSE`, `docs/`
(companions to this file; eight other planned `docs/*.md` files still
pending — §13), `frontend/` (Phase 3, not started).

`backend/`: `pyproject.toml`, `Dockerfile`, `.dockerignore`,
`alembic.ini`, `alembic/env.py`,
`alembic/versions/6bdbf0ab25b0_initial_schema.py` (19 tables + RLS).

`backend/app/` (`main.py`, `config.py` at top level), mirroring the
bounded contexts in §2 under each layer:
- `domain/{shared,findings,scanning,assets,identity,reporting}/` —
  entities/value objects done for all six.
- `application/interfaces/` (all repository ports + `ScannerPort`,
  `StoragePort`, `AIProviderPort`, `RefreshTokenRepositoryPort`;
  `EventBusPort` pending), `application/scanning/` (done),
  `application/identity/` (done; invite/membership pending),
  `application/{assets,findings,reporting}/` (empty scaffolds).
- `api/dependencies.py`,
  `api/v1/{schemas,scans,auth,auth_schemas,organizations,organization_schemas}.py`,
  `api/internal/health.py` (metrics/admin pending).
- `infrastructure/db/{base,session,models/,repositories/}` (19 tables,
  5 aggregate + refresh_token repos), `infrastructure/storage/`
  (`MinioStoragePort`), `infrastructure/security/` (`target_validation`,
  `password_hashing`, `token_service`), `infrastructure/ai_providers/`
  (`anthropic_provider`; OpenAI/Ollama/OpenRouter pending),
  `infrastructure/{vector_store,event_bus,observability}/` (empty).
- `scanner_engine/adapters/nuclei/` (done); `nmap`/`burp`/`zap`/
  `reconx`/`bughunter`/`sqlmap` (empty Phase-4 stubs).
- `ai_agents/analysis_service.py` (no `BaseAgent` yet);
  `workers/{celery_app,tasks}.py`.

`backend/tests/`: `conftest.py`; `unit/` (28 files); `integration/`
(`support.py` + 13 files, against real Postgres).

## 6. Domain model summary

| Entity | Lives in | Key invariant / behavior |
|---|---|---|
| Organization / Membership | identity/ | ≥ 1 Owner always (not yet enforced by a use case) |
| RefreshToken | identity/ | opaque hash + `revoked_at`; not soft-deleted |
| Finding | findings/ | state machine `new → triaged → {confirmed, false_positive} → {fixed, accepted_risk, wont_fix}`; `effective_severity` prefers CVSS over AI estimate |
| Severity (VO) | findings/ | ordered info < low < medium < high < critical |
| CVSS (VO) | findings/ | validates 0.0–10.0 + vector format; derives band |
| Asset | assets/ | current-state cache; identity = `(org_id, asset_type, normalized value)` |
| AssetObservation | assets/ | append-only; recording one updates the Asset cache |
| Scan | scanning/ | state machine `queued → running → {completed, failed, cancelled}`, derived from workflow steps via `derive_scan_status()` |
| WorkflowStep | scanning/ | `pending → running → {completed, failed, skipped}`, independently retryable |

All entities are plain dataclasses (`app/domain/*/entities.py`) — shape
and computed properties are implemented/tested; most state-machine
transition *enforcement* still requires future use cases.

## 7. Completed milestones

| # | Milestone | Delivered |
|---|---|---|
| 1 | Foundation | config system, ULID ids, fingerprint hashing, DomainEvent base, Severity/CVSS value objects |
| 2 | Persistence layer | ORM models (19 tables), domain entities (5 contexts), repository ports + SQLAlchemy impls, Alembic migration + RLS |
| 3 | Scanner engine | `ScannerPort`, `StoragePort`, `run_scanner_subprocess`, `validate_target`, `MinioStoragePort`, `NucleiAdapter` |
| 4 | Pipeline orchestrator | `derive_scan_status`, `TriggerScanUseCase`, `normalize_scan_output`, `RunScanWorkflowUseCase` |
| 5 | API layer | `api/dependencies.py`, `v1/scans.py` (Scanning only, no auth yet), `internal/health.py`, `main.py` |
| 6 | AI analysis service | `AIProviderPort`, `AnthropicProvider`, `AnalysisService`; `AI_ANALYZE` wired for real |
| 7 | Docker Compose | `workers/celery_app.py`/`tasks.py`, async `run_scan` dispatch (202), full compose topology |
| — | Auth / Identity & Access (TD #9, pre-Phase-3, not "Milestone 8") | `RefreshToken` entity/port/repo, password hashing, JWT/refresh tokens + rotation, `register`/`login`/`refresh` routes, `get_current_user`/`require_organization_member` wired into all Scanning routes |
| — | Phase 3 backend preparation (not a milestone) | `get_cors_allowed_origins()` (app/config.py) + `create_app(cors_allowed_origins=...)` CORS wiring; `CreateOrganizationUseCase` + `POST /api/v1/organizations` (org-bootstrap: creates an Organization and an OWNER/ACTIVE membership for the caller in one call). Third blocker (Secure cookies over local HTTP) explicitly **not** resolved — see §1/§16. |

Full per-milestone delivery detail, file lists, verification narrative:
`docs/implementation_progress.md`.

## 8. API contracts (current, `/api/v1`)

**Scanning** (`/organizations/{organization_id}/scans`, all routes
require `require_organization_member`):
- `POST /` — create scan (`TriggerScanUseCase`); `scanner_name:
  Literal["nuclei"]`; also depends on `get_current_user`, passes
  `current_user.id` as `triggered_by_user_id`.
- `POST /{scan_id}/run` — dispatches Celery task, returns `202
  Accepted` with pre-execution state; 404 if missing, 409 on
  `scanner_name` mismatch; no-op (still 202) if already `RUNNING`.
- `GET /{scan_id}` — direct repository read (`ScanDetailResponse`).

**Auth** (`/auth`, no membership required):
- `POST /register` — 201, `UserResponse`, no cookies (User only, not org).
- `POST /login` — 200, `UserResponse`, sets `access_token`/
  `refresh_token` httpOnly/Secure/SameSite=Lax cookies.
- `POST /refresh` — 200, `UserResponse`, reads refresh token from its
  cookie, sets fresh rotated cookies. Tokens never appear in a JSON body.

**Organizations** (`/organizations`, requires `get_current_user` only —
no membership check, since none can exist yet):
- `POST /` — 201, `OrganizationResponse` (`id`/`name`/`slug`);
  `CreateOrganizationUseCase` creates the `Organization` plus an
  OWNER/ACTIVE `OrganizationMember` for the caller in one call; 409 on a
  duplicate `slug`. The only mechanism to obtain an `organization_id` at
  all post-registration — no list/get/rename/member-management routes
  exist (deliberately narrow, Phase 3 backend preparation, not a full
  org-management surface).

**Internal**: `GET /health/live`, `GET /health/ready` (`SELECT 1`).
`/internal/metrics`, `/internal/admin` not implemented (need
observability/RBAC). Findings/Assets/Reporting HTTP surface and a
logout endpoint are not yet built — see §13.

## 9. Authentication state

Fully wired on all Scanning routes. `get_current_user` reads the
httpOnly `access_token` cookie (401 on any failure).
`require_organization_member` additionally confirms ACTIVE membership
(403) — reuses the request's already-open `get_org_session` transaction
(a separate data-integrity 404, not itself an authorization control).
RBAC/OAuth/MFA/password-reset/email-verification/logout/CSRF
double-submit — not built; see §13.

## 10. Configuration & dependencies

Key runtime deps (`backend/pyproject.toml`): `pydantic`/
`pydantic-settings`, `python-ulid`, `sqlalchemy[asyncio]`, `asyncpg`,
`alembic`, `minio`, `fastapi`, `uvicorn[standard]`, `anthropic`,
`celery`, `pyjwt`, `bcrypt`, `email-validator`. Dev: `pytest`,
`pytest-cov`, `pytest-asyncio`, `ruff`, `mypy`, `httpx`, `celery-types`.
No dependency added until code actually imports it.

Required env vars (see `.env.example` for full annotation):
`DATABASE_URL`, `TEST_DATABASE_URL`, `REDIS_URL`, `MINIO_ENDPOINT`,
`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`, `JWT_SECRET`,
`AI_DEFAULT_PROVIDER` (default `anthropic`), `AI_MODEL` (default
`claude-sonnet-4-5`), `ANTHROPIC_API_KEY` (required for
`worker_role=ingestion_worker` when provider is anthropic — not `api`).
Optional: `ACCESS_TOKEN_EXPIRE_MINUTES` (15), `REFRESH_TOKEN_EXPIRE_DAYS`
(30), `CORS_ALLOWED_ORIGINS` (comma-separated, unset/empty = CORS
disabled entirely; read by `app/config.py`'s `get_cors_allowed_origins()`
directly, deliberately **not** through `Settings` — see that function's
own docstring). Role-based fail-fast validation lives in `app/config.py`.

## 11. Testing state

29 unit test files, 14 integration test files (+ `support.py` fixture
factory) under `backend/tests/`, against real PostgreSQL 16 for
integration tests. Most recent full verification (auth work,
post-correction): **91 of 92 relevant tests passing** — the one failure
is a pre-existing, unrelated RLS-sandbox-limitation artifact (sandbox
schema built via `Base.metadata.create_all()` instead of
`alembic upgrade head` due to a mid-session tool outage; not a code
defect). Ruff (lint + format) and MyPy strict both clean on every
module touched.

**Phase 3 backend preparation — full real verification (follow-up
session, superseding this section's earlier, more limited claim for
this same work).** The full `app/` package (all six bounded contexts,
infrastructure, workers, scanner_engine) and a real PostgreSQL 16
instance were reconstructed in Claude's sandbox, the Alembic migration
was applied, and the relevant existing test files
(`test_api_scans.py`, `test_api_auth.py`, `test_identity_repository.py`,
`test_api_dependencies.py`, `test_main_lifespan.py`, `test_health.py`,
`test_register_user.py`) plus every new one
(`test_create_organization.py`, `test_cors.py`, `test_config.py`'s new
cases, `test_api_organizations.py`) were run together for real:
**101 of 101 passed**, against a genuinely non-superuser Postgres role
(RLS actually enforced — see the correction below on why that
distinction mattered). Ruff and MyPy strict both clean on every
new/modified file (`create_organization.py`, `errors.py`, `config.py`,
`dependencies.py`, `main.py`, `organizations.py`,
`organization_schemas.py`, plus the four test files), except the one
pre-existing, untouched `E501` finding noted below.

**A real bug was found and fixed by this verification, not just
confirmed clean** — worth recording precisely, the same way the initial
migration's own DESIGN NOTE records a discovered RLS limitation:
`CreateOrganizationUseCase`'s first version pre-checked `get_by_slug`
before inserting, expecting to catch a duplicate slug before it reached
the database (mirroring `RegisterUserUseCase`'s duplicate-email check).
That pre-check cannot work: `organizations`' RLS policy is
self-referential (`id = current_setting('app.current_org_id')::uuid`),
and `CreateOrganizationUseCase`'s session is scoped to the *new*,
not-yet-existing organization's own id — so a query against
`organizations` from inside it can only ever see a row whose id equals
the new org's id, never any *other* organization's row, regardless of
what slugs already exist. The pre-check silently returned "not found"
for every real duplicate, letting the INSERT fail with a raw, unhandled
`IntegrityError` (an opaque 500) instead of a clean 409 — invisible
under a superuser role (which bypasses RLS entirely) and only surfaced
once verification used a properly-privileged, non-superuser role. Fixed
by relying on the database's own `UNIQUE(slug)` constraint instead:
`add()` is now wrapped, and a duplicate-key violation is translated into
`OrganizationSlugAlreadyTakenError` — the same fix TD #8 below already
prescribes for an analogous RLS/uniqueness interaction, applied here for
real. A second, smaller test-only bug (a direct `organization_members`
read in `test_api_organizations.py` missing its own `set_org_context`
call) was found and fixed the same way.

Incidental finding, not fixed (out of this session's scope —
pre-existing, unmodified code): `app/api/dependencies.py`'s
`require_organization_member` has one line (`user {current_user.id} is
not an active member...`) that Ruff's `E501` measures at 103 characters
against this project's 100-character limit. Not introduced or touched by
this session; flagged for a future session rather than silently fixed as
a drive-by change.

**Standing constraint:** the Filesystem MCP connector has no
command-execution tool. All verification runs in Claude's own sandbox
against file content read verbatim from this repository, then
transplanted — never executed on `C:\Users\gamer\Downloads\claudeOnly`
directly. Each session normally reconstructs and re-runs only the test
files it touches/depends on, not the full suite; this session's own
verification was broader than that norm (see above) because the initial
blind spot (superuser bypassing RLS) was only found by widening scope.
Per-milestone verification scope and exact historical pass counts:
`docs/implementation_progress.md`. Self-verify locally, or use an
environment with execution access (e.g. Claude Code), for a true
full-suite run against the other ~75 test files not reconstructed this
session.

## 12. Active technical debt

1. `domain/shared/enums.py` — staging area for enums belonging to
   `scanning/`/`assets/`/`identity/`/`reporting/`; move each into its
   owning module's own `enums.py` as that context's behavior is built.
5. Repository `update()`/`soft_delete()` fetch via `session.get()`
   (unfiltered by `deleted_at`) while `get_by_id`/lookups filter it —
   deliberate (an update must target a soft-deleted row too), but
   nothing prevents accidentally "reviving" one. Not yet triggered by
   any existing use case.
6. `MinioStoragePort` verified only against a mocked `Minio` client — no
   real MinIO server reachable in the verification environment.
7. `NucleiAdapter` verified only against a patched
   `run_scanner_subprocess` — no real `nuclei` binary reachable.
8. **Concurrent-scan race**: two scans for the same org targeting the
   same host, run truly concurrently, could both pass a `get_by_identity`
   "no existing Asset" check before either commits; the second `add()`
   fails on the unique constraint with an unhandled `IntegrityError`.
   Sequential retries of the *same* scan are safe; concurrent execution
   of *different* scans is not. No concurrent execution exists yet.
   Likely fix: catch the unique-constraint violation and re-fetch.
9. ~~No authentication on any `/api/v1` route~~ — **resolved** by the
   Auth / Identity & Access work.
10. ~~Scan pipeline ran synchronously in the HTTP handler~~ —
    **resolved** in Milestone 7 (async Celery dispatch, `202 Accepted`).
11. **`AI_ANALYZE` re-invokes the AI provider on every retry** of a scan
    whose later steps fail, even for findings already analyzed earlier
    — accepted cost, not exempted like `EXECUTE_SCANNER` (§4). Would
    need a new durability mechanism to fix.
12. **No network-isolated `scanner_worker` split.**
    `RunScanWorkflowUseCase` runs as one atomic call inside
    `ingestion_worker` — `EXECUTE_SCANNER` (arbitrary scan targets) and
    `AI_ANALYZE` (Anthropic API) both run from the same process holding
    DB credentials. Fix requires restructuring the use case into
    independently-schedulable phases with a durable hand-off — a
    genuine architectural change. Code-level scan-target safety
    (`validate_target`'s SSRF/private-IP checks, argument-list
    subprocess, timeouts, non-root) is unaffected and fully enforced.

## 13. Deferred / excluded work

- Findings/Assets/Reporting application-layer use cases and HTTP routes.
- `EventBusPort` and its implementation; `FindingCreated` publication
  (planned, no port yet).
- Remaining 14 scanner adapters (Phase 4 — nmap, burp, zap, reconx,
  bughunter, sqlmap, etc.).
- OpenAI/Ollama/OpenRouter `AIProviderPort` implementations (deferred
  until a second real provider shape is known).
- Network-isolated `scanner_worker` split (TD #12).
- RBAC, OAuth, MFA, password reset, email verification, logout
  endpoint, CSRF double-submit token (Phase 3+/Phase 6).
- Full organization management (list/get/rename organizations, invite/
  remove members, role changes) — `POST /api/v1/organizations` (Phase 3
  backend preparation) only ever creates a new org and makes the caller
  its Owner; it is not that surface.
- `CORS_ALLOWED_ORIGINS` is not yet added to `docker-compose.yml`'s
  `backend` service environment — harmless by default (unset = CORS
  disabled, matching pre-existing behavior), but will need adding
  alongside real Phase 3 frontend deployment.
- **httpOnly+Secure cookies over plain local HTTP** (Phase 3 frontend
  blocker #2) — explicitly not resolved; conflicts with §4's locked
  `secure=True`-unconditional decision. Requires an explicit human
  decision (local HTTPS dev setup, or an approved, documented
  environment-conditional relaxation) before Phase 3 frontend
  implementation can begin — see §1/§16.
- Qdrant/RAG integration (Phase 5).
- The eight `docs/*.md` files named in §5 — described only in chat
  history, never written; this file is the interim substitute.
- Phase 3 (frontend) — not started.

## 14. Coding standards & rules that must never change

- Ruff (lint + format) + MyPy strict; full type hints; every module
  docstring explains *why*, not just *what*. Domain layer: zero
  framework imports. Value objects validate on construction. Repository
  pattern for all DB access — no business logic in route handlers,
  routes call use cases only.
- One milestone per session; fix lint/type issues immediately, never
  "later"; document technical debt the moment it's introduced. Unit
  tests for every new function/class in the same session it's written.
  Fake/spy port implementations for unit tests; real implementations
  reserved for a smaller integration suite. Never assert exact-text
  match against an LLM output.
- Do not redesign approved architecture without a genuine implementation
  blocker; explain any such blocker before adopting a fix. Bounded
  context boundaries (§2) and every decision in §4 are locked.
- Production-quality code only; never recreate completed work; extend
  rather than rewrite; never skip tests; never modify more than one
  milestone per session. State precisely what was/wasn't verified —
  never claim a clean pass that wasn't run.
- This file is the source of truth over chat history; the repository is
  ground truth over this file — always re-read real files before
  editing, and flag any conflict explicitly rather than silently
  picking one.

## 15. Filesystem workflow notes

Project root: `C:\Users\gamer\Downloads\claudeOnly` (no space), via the
Filesystem MCP (not the sandbox — sandbox is disposable per-session
verification only). `create_directory` does not create nested paths in
one call (create shallowest-first). No command-execution tool exists
(see §11). No bulk write/create — one file/directory per call.

## 16. Current next step

Milestones 1–7, the Auth work, and two of three Phase 3 frontend
blockers (CORS, organization bootstrap) are complete. **The third
blocker — httpOnly+Secure cookies cannot be set by a browser over plain
`http://localhost` — is not resolved and requires an explicit human
decision before Phase 3 frontend implementation can begin:**
  - Serve local frontend dev over HTTPS (e.g. `mkcert`-issued certs) —
    no backend code change, `secure=True` stays exactly as locked; or
  - Explicitly approve relaxing `secure` to be environment-conditional
    for non-production — a real change to a decision §4 documents as
    deliberately *not* relaxed for local/test convenience, so this
    needs sign-off, not a silent code change.

**Await explicit approval before starting further work.** Candidates
requiring a scope decision: the cookie blocker above, RBAC, OAuth/MFA/
password reset/email verification, a logout endpoint, a CSRF
double-submit token, TD #12 (scanner_worker split),
Findings/Assets/Reporting HTTP surface, full organization management, or
Phase 3 (frontend) itself.
