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
all three frontend-blockers resolved — CORS (`CORSMiddleware` wiring),
organization bootstrap (`POST /api/v1/organizations`), and the third
(httpOnly+Secure cookies cannot be set by a browser over plain
`http://localhost`), resolved by explicit human decision: local dev now
runs both frontend and backend over HTTPS via mkcert-issued certs — no
backend code change, `secure=True` stays exactly as locked (§4). See §16.

**Phase 3 (frontend) implementation: approved and in progress.** Step 1
(frontend scaffold) complete — see §7/§16. Steps 2-5 (API client, auth
flow, organization + scan UI, tests) not yet started.

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
| Frontend | Next.js 15.5.25/TS/Tailwind/shadcn (Phase 3 — scaffold + API client + auth flow + org/scan UI complete, §7) |
| Backend | FastAPI, Python 3.12, SQLAlchemy async, Alembic |
| Database | PostgreSQL with Row-Level Security |
| Cache/queue | Redis, Celery |
| Object storage | MinIO, behind `StoragePort` |
| Vector DB | Qdrant (running, unused until Phase 5 RAG) |
| AI providers | Anthropic (built); OpenAI/Ollama/OpenRouter deferred, behind `AIProviderPort` |
| Auth | JWT httpOnly cookies (access) + opaque hashed/rotating refresh token (also httpOnly cookie) |
| IDs | ULID, generated in app code, stored as native Postgres UUID |
| Testing | pytest, pytest-cov, Ruff, MyPy strict (backend); Vitest (frontend, §7) |
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
- No `BaseAgent`/`NormalizerPort`/scanner registry yet — YAGNI. Two
  concrete `ActiveScanner` implementations exist as of Phase 4 (nuclei,
  nmap), selected via an explicit if/elif in `app/workers/tasks.py`'s
  `_select_active_scanner` and carried as a plain tuple in
  `AppState.active_scanners` (`app/api/dependencies.py`) — not a
  registry: no registration API, no dynamic/pluggable dispatch, just
  the two known concrete classes hand-wired in source. Normalization
  (`normalize_scan_output`, `app/application/scanning/normalization.py`)
  follows the identical pattern for its own two known output formats
  (`"nuclei-jsonl"`, `"nmap-xml"` — TD #16, resolved) — a plain
  if/elif, not an injectable `NormalizerPort`, for the same reason.
  `BaseAgent` still has exactly one concrete shape; build it only when a
  second real AI-agent shape exists.
- `POST .../scans/{scan_id}/run` dispatches Celery async, returns `202
  Accepted` (resolved TD #10): checks scan exists (404) and
  `scanner_name` matches one of the wired adapters (409 — widened from
  a single adapter to a small set, Phase 4; see `AppState.active_scanners`
  above) before dispatch; does not re-dispatch an already-`RUNNING`
  scan. `RunScanWorkflowUseCase` itself runs unmodified inside one
  `ingestion_worker` Celery task (no scanner_worker split — TD #12),
  constructed with whichever single concrete adapter
  `_select_active_scanner` (`app/workers/tasks.py`) picks for that
  scan's own `scanner_name` — the use case's own "exactly one adapter
  per construction" shape (its module docstring) is unchanged.
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
pending — §13), `frontend/` (Phase 3, scaffold + API client + auth flow
+ org/scan UI complete — §7).

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
- `scanner_engine/adapters/{nuclei,nmap}/` (done); `burp`/`zap`/
  `reconx`/`bughunter`/`sqlmap` (empty Phase-4 stubs).
- `ai_agents/analysis_service.py` (no `BaseAgent` yet);
  `workers/{celery_app,tasks}.py`.

`backend/tests/`: `conftest.py`; `unit/` (31 files); `integration/`
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
| — | Phase 3 backend preparation (not a milestone) | `get_cors_allowed_origins()` (app/config.py) + `create_app(cors_allowed_origins=...)` CORS wiring; `CreateOrganizationUseCase` + `POST /api/v1/organizations` (org-bootstrap: creates an Organization and an OWNER/ACTIVE membership for the caller in one call). Third blocker (Secure cookies over local HTTP) resolved this session — see §1/§16. |
| — | Phase 3 frontend — Step 1: scaffold (not a milestone) | `frontend/`: Next.js 15.5.25 (App Router) + TypeScript strict + Tailwind + shadcn CLI config (`components.json`), HTTPS-only local dev via mkcert (`next dev --experimental-https`, paths wired in `package.json`). `npm run typecheck`/`lint`/`build` all clean in Claude's sandbox before transplant; `--experimental-https` sanity-checked end-to-end (self-signed cert → `HTTP 200` over `https://localhost:3000`). `frontend/README.md` documents the mkcert setup for both frontend and backend. No business logic yet — API client/auth/org+scan UI are Steps 2-5. |
| — | Phase 3 frontend — Step 2: API client infrastructure (not a milestone) | `frontend/src/lib/api/`: typed `apiRequest<T>()` core (`client.ts`, `credentials: "include"` on every call), a typed error hierarchy (`errors.ts` — `UnauthorizedError`/`ForbiddenError`/`NotFoundError`/`ConflictError`/`ValidationApiError`/`NetworkError`, mapped from the backend's actual response shapes verified against source, including the 422 array-vs-string distinction), hand-mirrored request/response types for every currently-implemented backend contract (`types.ts`), and thin typed wrapper functions per bounded context (`auth.ts`, `scans.ts`, `organizations.ts`). A 401 triggers exactly one silent `POST /auth/refresh` then a single retry (module-level in-flight-refresh dedup for concurrent 401s); `skipAuthRetry` opts login/register/refresh out. Vitest 4.1.11, Node environment. `npm run typecheck`/`lint`/`test` (22/22)/`build` all clean in sandbox before transplant. No auth pages, middleware, or UI — Steps 3-5 remain. |
| — | Auth — logout endpoint (not a milestone) | `POST /api/v1/auth/logout`: `LogoutUseCase` (`app/application/identity/logout.py`, tolerant of an unknown/already-revoked token — treated as a successful no-op, not an error) revokes the presented refresh token via the same `RefreshTokenRepositoryPort.revoke` rotation already exercises; `_clear_auth_cookies()` (`app/api/v1/auth.py`) clears both cookies with the same attributes `_set_auth_cookies` used to set them. Deliberately does not depend on `get_current_user` — an expired access token must not block logout. 204, no body either way. See §8/§9. |
| — | Phase 3 frontend — Step 3: auth/session flow (not a milestone) | `frontend/src/lib/auth/`: `AuthProvider`/`useAuth` (client-side session-state mirror — never reads/stores a token, only reflects what the backend's responses said), restoring a session on mount via the existing `refreshSession()` call (no dedicated `/auth/me` endpoint — a documented design choice, not a gap). `RequireAuth` — client-side-only route guard; deliberately no `middleware.ts` (would need either sharing `JWT_SECRET` with the edge runtime or a cookie-presence-only check that buys little over the already-known session state) — never the security boundary, which stays entirely server-side. `frontend/src/app/{login,register,dashboard}/page.tsx` (dashboard is a minimal protected placeholder, not Step 4's real UI); `frontend/src/components/nav-bar.tsx` (basic authenticated/unauthenticated nav state). Added `logout()` to `lib/api/auth.ts` (the one gap Step 2 correctly left out of scope). A real bug was caught by the new component test, not just avoided: `logout()`'s original `try/finally` (no `catch`) still rethrows after cleanup, leaving an unhandled promise rejection at every call site on a network failure — fixed to catch-and-log instead. `npm run typecheck`/`lint`/`test` (30/30)/`build` all clean in sandbox before transplant. No organization/scan UI — Steps 4-5 remain. |
| — | Phase 3 frontend — Step 4: organization + scan lifecycle UI (not a milestone) | `frontend/src/lib/organization/use-selected-organization.ts`: the one piece of org state this MVP needs client-side (which organization is selected), persisted to `localStorage` — explicitly not a credential (an org id alone grants no access; the backend's `require_organization_member` is what actually decides), and explicitly not multi-org management (a single id, not a list — the backend still has no "list my organizations" endpoint, §13). `frontend/src/lib/scans/use-scan-polling.ts`: bounded polling (fixed 3s interval, recursive `setTimeout` so requests never overlap, stops on terminal status/unmount/a 200-poll ―~10 min― safety cap). `frontend/src/components/{create-organization-form,new-scan-form,scan-detail}.tsx`; `frontend/src/app/scans/[scanId]/page.tsx` (new route); `frontend/src/app/dashboard/page.tsx` rewritten to bootstrap an organization then show scan creation. Added `frontend/src/lib/api/error-message.ts` (`describeApiError()`) to consolidate the instanceof-chain error handling Step 3's pages had each duplicated inline. `npm run typecheck`/`lint`/`test` (47/47)/`build` all clean in sandbox before transplant. No findings/assets/reporting UI, no RBAC/multi-org management — out of scope by design, not deferred. |
| — | Phase 4 — Nmap scanner adapter (not a milestone; out-of-sequence explicit-instruction session, ahead of Phase 3 frontend Step 5) | `scanner_engine/adapters/nmap/adapter.py`: `NmapAdapter(ActiveScanner)`, following `NucleiAdapter`'s exact shape — routes through the existing `validate_target`/`run_scanner_subprocess`, returns the existing `ScanOutput` shape, no new abstractions/registry/pipeline changes. Scan type pinned to `-sT -Pn` (TCP connect + skip host discovery, both unprivileged) — the code-level "no privileged/raw-packet scanning" requirement, on top of (not instead of) `run_scanner_subprocess`'s own non-root guard. Target is always the single already-validated hostname appended last, never a range — no target-expansion surface. Output format tagged `nmap-xml` (nmap's own `-oX -`), unparsed beyond a shallow `<nmaprun` sanity check (real parsing stays normalization's job, per §1's Scanning/Findings boundary). Explicitly handles: missing binary (`FileNotFoundError` → `ScannerExecutionError`), timeout (propagates `ScannerTimeoutError` from `run_scanner_subprocess` uncaught), non-zero exit (always a hard failure for nmap — deliberately stricter than nuclei's tolerant classification; see the adapter's own comment on why), empty output, and malformed/non-XML output. `tests/unit/test_nmap_adapter.py` — 9 new tests, fakes/spies only, no real `nmap` binary required. No ZAP/Burp/SQLMap/ReconX/BugHunter, no `ScannerPort` changes, no wiring into the API layer's scanner-name literal or the pipeline — explicitly out of scope, stopped after Nmap per instruction. |
| — | Phase 4 — Nmap adapter wiring into scan pipeline/API (not a milestone; second out-of-sequence explicit-instruction session, directly following the adapter-only one above) | Made `nmap` actually selectable/executable through the existing scan flow, without a registry/factory/`ScannerPort` redesign. `app/api/v1/schemas.py`: `scanner_name: Literal["nuclei", "nmap"]`. `app/api/dependencies.py`: `AppState.active_scanner` (singular) → `active_scanners: tuple[ActiveScanner, ...]`; `get_active_scanner` → `get_active_scanners`; `ScanDispatcher` widened to carry `scanner_name` alongside the ids. `app/main.py`: `_lifespan` constructs both `NucleiAdapter()` and `NmapAdapter()`. `app/api/v1/scans.py`: `run_scan` checks `scan.scanner_name` against the whole wired-names set (409 if unmatched) and passes `scan.scanner_name` through to the dispatcher. `app/workers/tasks.py`: new `_select_active_scanner(scanner_name)` helper — an explicit if/elif over exactly the two known adapters (not a registry — see §4), reusing `ScannerMismatchError` for an unrecognized name; `scanner_name` threaded through `_run_scan_workflow_from_settings`/`run_scan_workflow_task`. `RunScanWorkflowUseCase`/`execute_scan_workflow`/the 8-step pipeline/target validation/subprocess boundary/every other adapter — all untouched, confirmed by direct diff review. New `tests/unit/test_workers_tasks.py` (3 pure-function tests for `_select_active_scanner`); updated `test_api_dependencies.py`, `test_api_schemas.py`, `test_api_scans.py` (+3 new nmap-path tests), `test_main_lifespan.py`, `test_scan_worker_task.py` (+1 new integration test proving the worker selects `NmapAdapter`, not `NucleiAdapter`, for an nmap-scoped scan). |
| — | Phase 4 — Nmap XML normalizer (not a milestone; third out-of-sequence explicit-instruction session, resolving TD #16) | `app/application/scanning/normalization.py`: `normalize_scan_output` gained a second dispatch branch for `"nmap-xml"` (plain if/elif alongside the existing `"nuclei-jsonl"` branch — explicitly not a `NormalizerPort`, per instruction not to introduce new pipeline/scanner abstractions; mirrors the `_select_active_scanner` precedent). New `_parse_nmap_xml`/`_nmap_host_value`/`_nmap_port_to_normalized`/`_nmap_service_description` (stdlib `xml.etree.ElementTree`, no new dependency) turn nmap's `-oX` XML into one `NormalizedFinding` per **open** port across every **up** host — closed/filtered ports and down hosts contribute nothing. Every nmap-derived finding is `raw_severity="info"` with no CVE/CVSS candidate (an honest reflection of what a plain `-sT -Pn` TCP-connect scan with no NSE vulnerability scripts actually detects — see `NmapAdapter`'s own scope). `template_id="open-port-{protocol}-{portid}"` gives the same per-scan-recurring-finding fingerprint stability nuclei's `template-id` already provides. `host` prefers a resolved hostname over the raw IP, mirroring nuclei's own host/ip fallback. Ten new `test_normalization.py` cases (single/multiple open ports, closed/filtered-port exclusion, down-host exclusion, hostname-vs-address preference, missing-service handling, empty input, malformed XML, multi-host); the pre-existing `test_unsupported_output_format_raises` (previously asserting `"nmap-xml"` itself was unsupported) was updated to use `"burp-xml"` instead, since that claim is no longer true. `tests/integration/test_scan_worker_task.py`'s nmap-selection test (added by the wiring session immediately above) was upgraded in step: its fake scanner now emits real nmap-XML-shaped output for the `"nmap-xml"` persona (previously always emitted nuclei-JSON-shaped output regardless of persona, which this new normalizer would have flagged as malformed XML), and the test now asserts genuine full `Scan.status is COMPLETED` — matching its nuclei sibling test — rather than only the `EXECUTE_SCANNER` step's status, since normalization can now actually carry an nmap-scoped scan all the way through. |

Full per-milestone delivery detail, file lists, verification narrative:
`docs/implementation_progress.md`.

## 8. API contracts (current, `/api/v1`)

**Scanning** (`/organizations/{organization_id}/scans`, all routes
require `require_organization_member`):
- `POST /` — create scan (`TriggerScanUseCase`); `scanner_name:
  Literal["nuclei", "nmap"]` (widened Phase 4); also depends on
  `get_current_user`, passes `current_user.id` as `triggered_by_user_id`.
- `POST /{scan_id}/run` — dispatches Celery task, returns `202
  Accepted` with pre-execution state; 404 if missing, 409 if
  `scanner_name` doesn't match any wired adapter (a small set as of
  Phase 4, not a single adapter); no-op (still 202) if already `RUNNING`.
- `GET /{scan_id}` — direct repository read (`ScanDetailResponse`).

**Auth** (`/auth`, no membership required):
- `POST /register` — 201, `UserResponse`, no cookies (User only, not org).
- `POST /login` — 200, `UserResponse`, sets `access_token`/
  `refresh_token` httpOnly/Secure/SameSite=Lax cookies.
- `POST /refresh` — 200, `UserResponse`, reads refresh token from its
  cookie, sets fresh rotated cookies. Tokens never appear in a JSON body.
- `POST /logout` — 204, no body. Reads the refresh token from its
  cookie (not required, not an error if absent), revokes it
  (`LogoutUseCase`), clears both cookies. Does not require
  `get_current_user` — an expired access token must not block logout.

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
observability/RBAC). Findings/Assets/Reporting HTTP surface is not yet
built — see §13.

## 9. Authentication state

Fully wired on all Scanning routes. `get_current_user` reads the
httpOnly `access_token` cookie (401 on any failure).
`require_organization_member` additionally confirms ACTIVE membership
(403) — reuses the request's already-open `get_org_session` transaction
(a separate data-integrity 404, not itself an authorization control).
`POST /auth/logout` revokes the presented refresh token server-side
(`LogoutUseCase`) and clears both cookies — see §7/§8.
RBAC/OAuth/MFA/password-reset/email-verification/CSRF
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

30 unit test files, 14 integration test files (+ `support.py` fixture
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

**Auth — logout endpoint — full real verification (follow-up session).**
Same rigor as the Phase 3 backend preparation entry above: the full
`app/` package, `alembic upgrade head`, and a genuinely non-superuser
`app_user` role (`rolsuper=false`, `rolbypassrls=false`, explicitly
checked) were reconstructed fresh in Claude's sandbox. Ran together for
real — the new `test_logout_use_case.py` plus every directly-relevant
existing file (`test_login_user.py`, `test_register_user.py`,
`test_token_service.py`, `test_password_hashing.py`,
`test_create_organization.py`, `test_cors.py`, `test_config.py`,
`test_health.py`, `test_api_dependencies.py`, `test_main_lifespan.py`,
`test_health_ready.py`, `test_identity_repository.py`,
`test_api_auth.py` (extended with 7 new logout cases),
`test_api_scans.py`, `test_api_organizations.py`): **135 of 135
passed.** Ruff (lint + format) and MyPy strict both clean on every
new/modified file (`logout.py`, `dependencies.py`, `auth.py`, plus the
two touched test files) — including the pre-existing `E501` finding
noted above, confirmed still present and still untouched (reconstructed
verbatim from the real file, not reintroduced by this session).
Narrower scope than the org-bootstrap session's own full-package
verification in one respect: Scanning/AI/Assets/Findings-specific test
files (`test_run_scan_workflow.py`, `test_nuclei_adapter.py`, etc.) were
not re-run, since this change touches only `RefreshTokenRepositoryPort`
and the auth route layer — present in the sandbox for imports to
resolve, not exercised.

**Phase 3 MVP browser/integration verification pass (follow-up
session, after Step 4).** A different kind of verification than every
entry above: not `pytest` against mocked/fake ports, but a real
headless Chromium (Playwright) driving the actual `npm run dev`
frontend against the actual `uvicorn` backend, both over self-signed
HTTPS (mirroring the mkcert setup's shape), with real PostgreSQL 16
(genuine RLS, non-superuser role) and real Redis (genuine Celery task
dispatch) — all in Claude's own sandbox, explicitly **not** Madhav's
own machine/browser/mkcert trust chain, which this pass could not and
did not verify. **49 of 49 checks passed**, covering the full
register → login → session-restore → cookie-attribute → CORS flow
(17), organization bootstrap → scan creation → scan detail → bounded
polling → run dispatch → terminal-state-stops-polling (15), an
isolated real 401 → silent-refresh → retry cycle plus real
404/403/409/422 handling (9), and real server-side logout invalidation
(a raw `curl` reuse of the pre-logout refresh token got a genuine 401,
not just a cleared browser cookie) plus protected-route direct
navigation (8).

**Zero product code changes resulted.** Every issue hit during this
pass was in the verification environment or test scripts, not the
repository: (a) `CORS_ALLOWED_ORIGINS` in a `.env` file has no effect —
`get_cors_allowed_origins()` deliberately reads raw `os.environ`,
bypassing dotenv loading (already-documented, locked design, §10) —
this pass hit exactly that mistake, empirically confirming the
real-world impact of the not-yet-in-`docker-compose.yml` gap already
noted in §13; (b) Claude's own sandbox reconstruction of
`pyproject.toml` (an earlier session) had `celery>=5.4` instead of the
real repo's correct `celery[redis]>=5.4` — confirmed by direct
comparison against the real file, a sandbox-reconstruction error, not a
repository issue; (c) two test-script bugs (raw SQL using the
lowercase enum *value* where SQLAlchemy's `native_enum=False` columns
store the uppercase member *name* — confirmed correct, working ORM
behavior; a stale Playwright `storageState.json`/missing `localStorage`
seed). No regression test was added to the repository, since no actual
product bug was found to regress-test against.

**Explicitly not verified:** a real Nuclei scan actually executing (no
scanner binary, no Celery worker process, no MinIO in this pass —
"Run" was verified at the real HTTP-dispatch level only: a genuine 202
and a genuine Celery enqueue to Redis; the terminal-state/polling-stop
check used a direct DB write to simulate what a worker would eventually
write, not an actual completed scan).

**Phase 4 — Nmap scanner adapter.** Same reconstruct-and-run-in-sandbox
approach as every backend entry above (no command-execution tool exists
against the real repository — §15). `NmapAdapter` plus its four direct
dependencies (`ScannerPort`/`ScanOutput`, `validate_target`,
`run_scanner_subprocess`, `utcnow`) were reconstructed verbatim in
Claude's sandbox alongside the unmodified `NucleiAdapter` and
`base_scanner` test files, to check for regressions on code this session
did not touch. **`pytest tests/unit/ -q` → 20 passed, 0 failed** (9 new
`test_nmap_adapter.py` cases — argv construction with/without
`extra_args`, missing binary, timeout propagation, non-zero exit, empty
output, malformed output, target-validated-before-subprocess-call; 6
`test_base_scanner.py` + 5 `test_nuclei_adapter.py` cases, confirming
zero regression on the shared/untouched code this adapter builds on).
Ruff (lint + format) clean on `adapter.py` and `test_nmap_adapter.py`.
MyPy `--strict` clean, 0 issues, on both the new files and the full
reconstructed `app/` slice (17 source files). Byte-count integrity check
(`get_file_info` vs. sandbox `wc -c`) confirmed an exact-match transplant
for both new files (`adapter.py`: 5061 bytes; `test_nmap_adapter.py`:
7183 bytes). **Not verified:** a real `nmap` binary (same documented gap
as `NucleiAdapter` — see TD #7/#15 below); no integration-level test was
added, per instruction ("only if it fits the existing architecture" —
nothing in the existing integration suite exercises scanner adapters
directly; they're exercised indirectly via `RunScanWorkflowUseCase`,
which this session did not touch). No API-layer, pipeline, or
other-adapter files were changed — confirmed by reviewing the diff of
every file actually written.

**Phase 4 — Nmap adapter wiring into scan pipeline/API.** A much wider
reconstruction than the adapter-only session above, since this one
touches the composition root (`app/main.py`, `app/api/dependencies.py`,
`app/workers/tasks.py`) — files with a wide, transitive import graph.
The **entire** `backend/app/` package (all six bounded contexts,
infrastructure, workers, scanner_engine, ai_agents), the real Alembic
migration, and a genuine non-superuser `app_user` role (`rolsuper=false`,
`rolbypassrls=false`, explicitly checked, matching every prior
full-package session's own standard) were reconstructed fresh in
Claude's sandbox — not a partial slice. A real PostgreSQL 16 instance
was installed and the migration applied for real (19 tables + RLS
policies). Every existing test file the changed files' own imports
touch was reconstructed and run as a genuine regression baseline
**before** any edit was made (63 passed), then again after every edit
(**70 passed, 0 failed** — 61 pre-existing/regression cases across
`test_nuclei_adapter.py`/`test_nmap_adapter.py`/`test_base_scanner.py`/
`test_api_dependencies.py`/`test_api_schemas.py`/`test_api_scans.py`/
`test_main_lifespan.py`/`test_scan_worker_task.py`, plus 9 genuinely new
cases across the same files and the new `test_workers_tasks.py`). Ruff
(lint + format) and MyPy `--strict` both clean on all 11 changed/new
files — the only two remaining Ruff findings in the touched files are
both on lines this session did not edit (`app/api/dependencies.py`'s
already-documented pre-existing `E501` above, and one pre-existing
formatting choice in `app/main.py` outside this session's edit blocks,
confirmed by direct diff review). Byte-count integrity check
(`get_file_info` vs. sandbox `wc -c`) confirmed an exact-match transplant
for every one of the 11 files — including a real, caught-and-fixed
mismatch: an early transplant of one new test's `asyncio.to_thread(...)`
call used a different line-wrap than the sandbox's own `ruff format`
output; the byte-count check caught the 22-byte discrepancy immediately
and it was corrected before being reported done. New integration test
(`test_run_scan_workflow_task_selects_nmap_for_an_nmap_scoped_scan`)
asserts via `call_count` spies on both adapter fakes that `NmapAdapter`,
not `NucleiAdapter`, actually runs for an nmap-scoped scan — and via the
`EXECUTE_SCANNER` workflow step's own status, not the whole `Scan`'s,
since no nmap-xml normalizer exists yet and the scan's later `NORMALIZE`
step genuinely (and correctly) fails for that reason — see TD #16.
`RunScanWorkflowUseCase`, `execute_scan_workflow`, the 8-step pipeline,
`validate_target`, `run_scanner_subprocess`, `ScannerPort`, and every
other adapter were confirmed untouched by direct diff review, not just
by claim.

**Phase 4 — Nmap XML normalizer (TD #16).** Same reconstruct-in-sandbox
approach, reusing the still-intact full-package sandbox from the wiring
session immediately above (fresh-diffed against the real repo's current
`normalization.py`/`test_normalization.py` before editing, to rule out
drift). **`pytest tests/ -q` → 89 passed, 0 failed** (19
`test_normalization.py` cases — 9 pre-existing nuclei-path regression
cases plus 10 new nmap-xml cases; 70 pre-existing cases across every
other test file, confirming zero regression elsewhere — the composition
root/wiring files from the prior session were not touched this session).
Ruff (lint + format, including `--fix` for three auto-fixable
UP012/line-length findings in the new test fixture's XML-building code)
and MyPy `--strict` (one real finding: a list-comprehension type-narrowing
case mypy couldn't follow across two separate `.get()` calls, fixed with
a walrus-operator rewrite) both clean on all three changed files
(`normalization.py`, `test_normalization.py`, `test_scan_worker_task.py`)
and on the full package (102 source files, whole-package sweep). Byte-count
integrity check (`get_file_info` vs. sandbox `wc -c`) confirmed an
exact-match transplant for all three files
(`normalization.py`: 11424 bytes; `test_normalization.py`: 10419 bytes;
`test_scan_worker_task.py`: 18813 bytes) — clean on the first attempt
this time, no byte-count mismatch to correct. **A direct, in-scope
consequence this session's own verification caught, not initially
anticipated:** the wiring session's nmap-selection integration test had
a fake scanner that emitted nuclei-JSON-shaped output regardless of
which adapter persona it stood in for; with a real nmap-xml normalizer
now in place, that fake's XML claim would have been exposed as false
(a malformed-XML `NormalizationError`, not the old "unsupported format"
failure) the moment the test ran. Fixed by making the fake emit output
shaped like whichever `output_format` it is actually configured to
report, and upgrading the test's own assertion from "`EXECUTE_SCANNER`
step completed" to genuine `Scan.status is COMPLETED`, matching its
nuclei sibling test — the test now actually exercises the new
normalizer end-to-end rather than working around its prior absence.

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
13. **`frontend/`'s Next.js pinned to 15.5.25, not the current npm
    `latest` (16.3.4).** The initial 14.2.15 pin had 4 high + 1 critical
    CVE per `npm audit`; 15.5.25 clears all of them while staying on
    React 18 (a supported Next 15 peer, avoiding a forced React 19
    migration). One residual, accepted for now: a high-severity PostCSS
    advisory (XSS/path traversal via CSS source maps) is bundled inside
    Next 15's own internal build tooling
    (`node_modules/next/node_modules/postcss`, distinct from the
    project's own top-level `postcss`, already at latest) and is only
    resolved by the Next 16 jump. Not taken this session — Next 16's
    API surface (App Router/config/CLI changes) was not verified against
    this scaffold. Low practical exploitability here (the affected code
    path processes untrusted CSS source maps at build time, which this
    project's own pipeline never does). Evaluate the Next 16 upgrade in
    a dedicated session; see `frontend/README.md`'s "Dependency notes".
14. **`frontend/package-lock.json` not committed.** The Filesystem MCP
    connector's documented large-write instability (§15) makes a
    ~6,500-line lockfile unsafe to transplant whole this session.
    `frontend/package.json` pins exact top-level versions (no `^`/`~`),
    so reproducibility is largely preserved, but transitive-dependency
    drift is a residual risk until a real lockfile exists. Run
    `npm install` from `frontend/` locally and commit the generated
    `package-lock.json`.
15. **`NmapAdapter` verified only against a patched
    `run_scanner_subprocess`** — no real `nmap` binary reachable in the
    verification environment. Same shape as TD #7 (`NucleiAdapter`);
    resolved the same way when a real-binary CI/sandbox image exists.
16. ~~**No `nmap-xml` normalizer.**~~ — **resolved.**
    `normalize_scan_output` (`app/application/scanning/normalization.py`)
    now parses nmap's `-oX` XML (one `NormalizedFinding` per open port,
    across every up host — `raw_severity="info"`, no CVE/CVSS candidate,
    honestly reflecting what a plain `-sT -Pn` scan with no NSE scripts
    actually detects). An nmap-scoped scan now genuinely reaches
    `Scan.status is COMPLETED` end-to-end, confirmed by this session's
    own integration test (upgraded from the wiring session's narrower
    `EXECUTE_SCANNER`-only assertion — see §7/§11). Still a plain
    if/elif dispatch in `normalize_scan_output`, not a `NormalizerPort`
    — see §4.

## 13. Deferred / excluded work

- Findings/Assets/Reporting application-layer use cases and HTTP routes.
- `EventBusPort` and its implementation; `FindingCreated` publication
  (planned, no port yet).
- Remaining 13 scanner adapters (Phase 4 — burp, zap, reconx,
  bughunter, sqlmap, etc.; nmap now done and fully wired — selection,
  execution, and normalization — into the pipeline/API, see §7/§11/
  TD #15/TD #16).
- OpenAI/Ollama/OpenRouter `AIProviderPort` implementations (deferred
  until a second real provider shape is known).
- Network-isolated `scanner_worker` split (TD #12).
- RBAC, OAuth, MFA, password reset, email verification, CSRF
  double-submit token (Phase 3+/Phase 6).
- Full organization management (list/get/rename organizations, invite/
  remove members, role changes) — `POST /api/v1/organizations` (Phase 3
  backend preparation) only ever creates a new org and makes the caller
  its Owner; it is not that surface.
- `CORS_ALLOWED_ORIGINS` is not yet added to `docker-compose.yml`'s
  `backend` service environment — harmless by default (unset = CORS
  disabled, matching pre-existing behavior), but will need adding
  alongside real Phase 3 frontend deployment.
- ~~httpOnly+Secure cookies over plain local HTTP~~ (Phase 3 frontend
  blocker #2) — **resolved**: local dev runs both frontend and backend
  over HTTPS via mkcert, `secure=True` stays exactly as locked (§4). See
  §1/§16, `frontend/README.md`.
- ~~A logout endpoint~~ — **resolved**: `POST /api/v1/auth/logout`,
  see §7/§8/§9.
- **No "list my organizations" endpoint.** A returning person's
  frontend has no server-side way to rediscover an `organization_id`
  across browsers/devices/cleared storage beyond what it cached
  client-side at creation time (`frontend/src/lib/organization/
  use-selected-organization.ts`, §7 Step 4 — `localStorage`, not a
  credential). Not a Step 4 bug: there is nothing further Step 4 could
  have done without this backend endpoint existing. Add
  `GET /api/v1/organizations` (or `/me/organizations`) in a future
  session and `use-selected-organization.ts` is the one place that
  would change to source from it instead.
- Qdrant/RAG integration (Phase 5).
- The eight `docs/*.md` files named in §5 — described only in chat
  history, never written; this file is the interim substitute.
- Phase 3 (frontend) — in progress, Step 4 of 5 complete (§7).

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

Milestones 1–7, the Auth work, and all three Phase 3 frontend blockers
are complete. **Cookie blocker decision (made explicitly this session):
local HTTPS via mkcert** — no backend code change, `secure=True` stays
exactly as locked (§4). Setup: `frontend/README.md`. Backend runs
locally with `--ssl-keyfile`/`--ssl-certfile` pointed at the same mkcert
cert the frontend uses; `CORS_ALLOWED_ORIGINS` updated to
`https://localhost:3000` (root `.env.example`).

**Out-of-sequence session (explicit instruction): Phase 4 — Nmap
scanner adapter — complete, see §7/§11/TD #15.** Directly instructed
ahead of the Step 5 decision below, with an explicit stop-after-Nmap
scope: `NmapAdapter` only, no `ScannerPort`/pipeline/registry changes,
no other adapters. Nothing here changes Step 5's own status — it is
still awaiting a scope decision, unaffected by this session.

**Second out-of-sequence session (explicit instruction, directly
following the one above): Phase 4 — Nmap adapter wiring into the scan
pipeline/API — complete, see §7/§11/TD #16.** `nmap` is now genuinely
selectable (`scanner_name: Literal["nuclei", "nmap"]`) and executable
(the worker actually constructs and runs `NmapAdapter` for an
nmap-scoped scan) through the real, public scan flow — not just an
adapter class sitting unreferenced in `scanner_engine/adapters/nmap/`.
Still no registry/factory/`ScannerPort` redesign — selection is a plain
tuple (`AppState.active_scanners`) plus an explicit if/elif
(`_select_active_scanner`). This session's own verification surfaced a
real, then-currently-reachable gap (an nmap-scoped scan would genuinely
fail at `NORMALIZE`, no `nmap-xml` normalizer existing yet) — resolved
by the third session immediately below, not left open. Nothing here
changes Step 5's own status — still awaiting a scope decision,
unaffected by this session.

**Third out-of-sequence session (explicit instruction, directly
following the two above): Phase 4 — Nmap XML normalizer — complete,
resolving TD #16, see §7/§11/§12.** `normalize_scan_output` now parses
nmap's own `-oX` XML output into `NormalizedFinding`s (one per open
port, across every up host), via a second plain if/elif branch —
explicitly not a `NormalizerPort`, matching the instruction not to
introduce new pipeline/scanner abstractions and mirroring the
`_select_active_scanner` precedent from the session above. **An
nmap-scoped scan now genuinely completes end-to-end through the real,
public scan flow** — confirmed by an upgraded integration test
asserting true `Scan.status is COMPLETED`, not just adapter selection.
Nothing here changes Step 5's own status either — still awaiting a
scope decision, unaffected by this session.

**Phase 3 (frontend) implementation is approved and in progress**,
following the 5-step plan (scaffold → API client infra → auth/session
flow → organization + scan lifecycle UI → tests), one step at a time
with review between steps:
  - Step 1 (scaffold): **complete** — see §7.
  - Step 2 (API client infrastructure): **complete** — see §7.
    `frontend/src/lib/api/` covers every currently-implemented backend
    contract (auth, organizations, scanning) with typed requests/
    responses, a typed error hierarchy for 401/403/404/409/422, and the
    401-silent-refresh-retry-once behavior. Unit-tested (Vitest,
    22/22). No UI/pages/routing yet — that's Step 3+.
  - Step 3 (auth/session flow): **complete** — see §7.
    `frontend/src/lib/auth/` (`AuthProvider`/`useAuth`, `RequireAuth`),
    register/login/dashboard pages, basic nav state, `logout()` added
    to the API client. Route protection is client-side only (no
    `middleware.ts`) — a deliberate choice, not an oversight; see the
    §7 row for why. Component-tested (Vitest + jsdom + Testing Library,
    30/30 total). No organization/scan UI yet — that's Step 4.
  - Step 4 (organization + scan lifecycle UI): **complete** — see §7.
    Org bootstrap (`/dashboard`, create-if-none) → scan creation →
    `/scans/[scanId]` (status, workflow steps, bounded polling,
    run/retry). Selected org id persisted client-side (`localStorage`,
    not a credential — §13 documents the backend gap this works around).
    Hook/logic-level tested (Vitest + jsdom + fake timers, 47/47 total).
    No findings/assets/reporting UI, no RBAC/multi-org management.
  - Phase 3 MVP browser/integration verification pass (follow-up
    session, not one of the 5 numbered steps): **complete** — see §11.
    Real Playwright/Chromium against the real running frontend+backend
    over HTTPS, real Postgres/Redis. **49/49 checks passed, zero
    product code changes.** Confirms Steps 1-4's actual runtime
    behavior (cookies, CORS, 401 retry, polling, logout invalidation,
    route protection) matches what the unit/hook-level tests already
    implied, in a real browser rather than mocked `fetch()`.
  - Step 5 (tests): not yet started.

**Next immediate action:** decide Step 5's scope with the browser
verification pass's results in hand (§11) — since that pass found zero
code-level issues, Step 5 does not need to fix anything from Step 4; it
can focus on closing gaps between what the verification pass's
*scripts* exercised and what the *committed automated test suite*
covers (e.g., no existing Vitest file exercises the organization/scan
UI components the way `context.test.tsx` already does for auth).
**Await explicit approval before starting** — Step 4 was explicitly
scoped to stop here, and this verification pass was explicitly scoped
to stop before Step 5 too.

**Explicitly out of Phase 3 MVP frontend scope** (per standing
instruction, not a scope decision to revisit): RBAC, billing, additional
scanners, RAG, full dashboard, notifications, and any backend
refactoring beyond the config-only CORS-origin-scheme update, the
small, additive logout endpoint, and the Nmap adapter already made (all
explicitly approved/instructed before being built — see §7/§8/§9/§11).
Other backend-scope candidates, unrelated to Phase 3 frontend and still
awaiting a scope decision: OAuth/MFA/password reset/email verification,
a CSRF double-submit token, TD #12 (scanner_worker split),
Findings/Assets/Reporting HTTP surface, full organization management,
remaining Phase 4 scanner adapters (burp/zap/reconx/bughunter/sqlmap —
§13; explicitly not started per this session's stop-after-Nmap scope).
