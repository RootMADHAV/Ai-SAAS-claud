# Implementation Progress

Compact historical milestone record. Compacted 2026-10-02 (Phase 6
Milestone 1) from a ~138 KB session-by-session narrative, which is kept
as the owner's backup `docs/old historic implementation_progress.md` (never
edit, rename or delete it; also recoverable via git history). Purpose of the
three governing docs: `PROJECT_STATE.md` = compact authoritative current
state; `docs/session_state.md` = latest-session handoff; this file =
compact history (objective, decisions, files, verification, debt).
Test counts below are as recorded by each milestone's own session; they
were not re-run when this file was compacted. Every milestone's
verification ran in Claude's sandbox (no execution tool exists against
the real repository) unless stated otherwise.

## Roadmap status

| Phase | Focus | Status |
|---|---|---|
| 1 | Architecture, folder structure, DB schema, API design, docs | Approved (Phase 1.2) |
| 2 | MVP backend (vertical slice: scan -> AI analysis) | Complete: Milestones 1-7 (+ Auth work, TD #9) |
| 3 | MVP frontend | Steps 1-4 complete; Step 5 (tests) awaiting a scope decision |
| 4 | Scanner adapters beyond Nuclei | Partial: `nuclei`, `nmap` wired + normalized; `sqlmap` adapter-only; `burp`/`zap` stubs; no complete roster is enumerated anywhere in the repo |
| 5 | AI features + RAG | Milestones 1-5 complete; real Docker build and real Qdrant unverified |
| 6 | Multi-tenancy hardening, RBAC, orgs/teams, billing | In progress: **M1 (RBAC on scan routes) complete**; **M2 (member management + last-owner protection) and M3 (audit trail for successful membership changes): in the real working tree, uncommitted, verified by the owner on the real repo 2026-10-11/12** (762 passed / 0 skipped / 0 failed, mypy clean, live 18/18 walkthrough, exactly 6 audit rows); M4 not started and not defined; closing Phase 6 at M3 is an open owner decision |
| 7 | Full dashboard | Not started |
| 8 | DOCX/PDF polish, scheduling, notifications | Not started |
| 9 | Security hardening pass | Not started |
| 10 | Deployment | Not started |

## Architectural decisions future work depends on

Full current list: `PROJECT_STATE.md` section 3. Condensed:
- **RLS enforces tenant isolation only**; soft-delete visibility is an
  explicit `WHERE deleted_at IS NULL` in repository reads (Postgres checks
  `SELECT USING` against the new row of an `UPDATE`, so combining them
  makes soft delete reject itself). Every datetime column needs
  `DateTime(timezone=True)`. IDs are ULIDs stored as native UUID;
  `StrEnum` columns use `native_enum=False`.
- **Eight-stage pipeline in locked order** (validate_target, execute_scanner,
  normalize, deduplicate, correlate, enrich, ai_analyze, persist) as
  `scan_workflow_steps` rows. Only `EXECUTE_SCANNER` is exempt from retry
  recomputation (output re-read from `StoragePort`); `AI_ANALYZE` recomputes
  (cost only, TD #11). A step failure is recorded and reflected in
  `Scan.status`, not raised. `AI_ANALYZE` results are stashed in memory and
  written by `PERSIST` (no `Finding.id` exists earlier).
- **Scanner severity is never written to `Finding.ai_severity_level`** (that
  field means an AI estimate); raw severity travels in
  `FindingOccurrence.raw_evidence`.
- **No registry/`BaseAgent`/`NormalizerPort`**: adapters are a plain tuple
  (`AppState.active_scanners`) plus an explicit if/elif
  (`_select_active_scanner`, `normalize_scan_output`).
- `POST .../run` validates (404 missing scan, 409 unwired scanner), dispatches
  Celery, returns 202; an already-RUNNING scan is not re-dispatched.
- **Network segmentation**: `backend` (API) on `internal` only, no internet,
  no MinIO/Anthropic credentials; `worker` on `internal` + `queue` +
  `worker-egress`.
- **Auth**: JWT access token and opaque SHA-256-hashed rotating refresh token,
  delivered only as httpOnly/Secure/SameSite=Lax cookies, never in a body.
  Family-wide reuse detection is not built. `password_hashing.py` /
  `token_service.py` / `target_validation.py` are imported directly into the
  application layer (single implementation, no framework imports).
  `RegisterUserUseCase` creates only a `User`.
- **`require_organization_member`** reuses `get_org_session`'s already-open
  RLS-scoped transaction. **Phase 6 M1 layers `require_scan_write_access` on
  top** with the role matrix in `app/domain/identity/access.py`.
- **Member management (Phase 6 M2)**: pure rules per operation in `access.py`
  (ADMIN manages only MEMBER/VIEWER, never grants/touches ADMIN/OWNER); every
  mutation takes `lock_for_membership_change` (`SELECT ... FOR UPDATE` on the
  organization row) FIRST, re-reads the actor under the lock, then applies
  policy and the last-owner guard (`count_active_owners`, race-free only under
  the lock). Removal = status `REMOVED`; re-add reactivates the same row.
  Direct add-by-email, no invitations; mutations are POST-only (CORS allows
  only GET/POST and was not widened); M2 itself wrote no audit entries.
- **Membership audit (Phase 6 M3)**: successful add / reactivate / role change /
  remove write exactly one `audit_logs` row (`member.added`,
  `member.reactivated`, `member.role_changed`, `member.removed`; IDs-only
  metadata) through a REQUIRED `AuditLogRepositoryPort`, in the same
  organization-scoped transaction under the row lock; fail-closed (an audit
  error rolls back the membership change). Refusals and no-ops write nothing.
  Record-only: no read API.
- **Phase 5**: `EmbeddingPort` is separate from `AIProviderPort`;
  `VectorStorePort` binds one collection per instance; corpus is the vendored
  CWE Top 25 (2025, View-1435); retrieval lives inside `AnalysisService` and
  degrades to the pre-RAG prompt on any failure; RAG adapters are imported
  lazily in the worker composition root so the API image never needs them;
  `qdrant_url` is deliberately not validated by `check_role_boundaries`.

## Permanent scanner decisions and exclusions
- **ReconX and BugHunter PRO are permanently excluded** from this project's
  scanner roster (the owner's own separate, earlier projects): not reused,
  ported, reconstructed, or referenced. Their stub folders were unused; the
  2026-10-02 directory listing shows they are already gone.
- The "14 remaining adapters" figure had no named source and was removed.
  Established: implemented `nuclei`, `nmap`, `sqlmap`; unimplemented
  `ImportScanner`-shaped stubs `burp`, `zap`.
- **`sqlmap-stdout` has deliberately no normalizer** (TD #18): sqlmap has no
  documented machine-readable result mode and the repo holds no real captured
  output, so `normalize_scan_output` raises `UnsupportedScanOutputFormatError`
  rather than parse undocumented text. Revisit only with a documented format,
  a real fixture, or an explicit human decision.
- `nmap` runs `-sT -Pn` against a single already-validated hostname; `sqlmap`
  runs `--batch --risk=1 --level=1 --technique=BEUT` (no stacked queries).

## Milestone history

**Phase 2 M1 -- Foundation.** Config, ULIDs, fingerprint hashing,
`DomainEvent`, enums, Severity/CVSS value objects. Files: `app/config.py`,
`app/domain/shared/*`, `app/domain/findings/value_objects.py`. Verified: 55
unit tests. Debt: TD #1 (enums staging area).

**M2 -- Persistence.** 19 tables across six contexts (ORM models, domain
entities, repository ports + SQLAlchemy implementations), Alembic initial
migration with RLS, async-Postgres `conftest.py` (a stray conftest importing
nonexistent modules was replaced). Found/fixed: naive-datetime columns; the
RLS+soft-delete predicate was unimplementable. Verified: 102 tests (64 unit +
38 integration on real PG16), 100% coverage, RLS isolation proven. Debt: TD #5.

**M3 -- Scanner engine.** Found already implemented at session start; audited,
not rewritten. `ScannerPort` (`ActiveScanner`/`ImportScanner`), `StoragePort`,
`run_scanner_subprocess` (arg-list only, timeout, non-root guard),
`validate_target` (DNS-resolving SSRF guard, cloud-metadata rejected),
`MinioStoragePort`, `NucleiAdapter`. Verified: 105 unit tests. Debt: TD #6
(MinIO mocked), TD #7 (nuclei patched).

**M4 -- Pipeline orchestrator.** `derive_scan_status`, `TriggerScanUseCase`,
`normalize_scan_output` (nuclei JSONL), `RunScanWorkflowUseCase` with
resumability. Verified: 44 new tests (41 unit + 3 integration), 100% coverage.
Debt: TD #8 (concurrent-scan race).

**M5 -- API layer.** `api/dependencies.py`, scan routes (create/run/get),
`/internal/health/{live,ready}`, `create_app()` factory, global handlers
(`LookupError` 404, `ScannerMismatchError` 409). Verified: 28 new/updated
tests; that session's sandbox DB lacked RLS (`create_all` fallback) and a
coverage greenlet artifact was found (TD #24); `test_main_lifespan.py` added
to cover `_lifespan`. Debt then: TD #9, #10.

**M6 -- AI analysis.** `AIProviderPort`, `AnthropicProvider`, `AnalysisService`
(strict JSON, schema-validated, raises `AnalysisError` rather than fabricate),
`AI_ANALYZE` wired for real. Verified: 84 new/updated tests, 100% coverage on
the new modules; a sandbox-only migration transcription error was caught (the
real migration was never touched). Debt: TD #11.

**M7 -- Async dispatch + Docker Compose.** Celery app (queue `scans`),
`workers/tasks.py`, `run_scan` returns 202, API drops storage/AI credentials,
multi-stage non-root Dockerfile, full compose topology. Decision: no
`scanner_worker` split (TD #12). Verified: 12 net-new/rewritten tests;
Docker unavailable, so compose was checked as YAML/graph only.

**Auth / Identity (TD #9; not "Milestone 8").** bcrypt hashing, JWT +
rotating refresh tokens, `register`/`login`/`refresh` routes,
`get_current_user` + `require_organization_member` on all scan routes. A
review corrected an unapproved `Authorization: Bearer` implementation back to
the locked httpOnly-cookie transport. Verified: 62 new/updated tests; 91 of 92
(one RLS-sandbox artifact). TD #9 resolved.

**Phase 3 backend preparation (not a milestone).** CORS via
`get_cors_allowed_origins()` (reads `os.environ`, not `Settings`);
`CreateOrganizationUseCase` + `POST /api/v1/organizations` (caller becomes
OWNER/ACTIVE); local HTTPS via mkcert resolves the Secure-cookie blocker with
no backend change. Verified: 101/101 on a non-superuser role. Bug found: a
slug pre-check cannot work under the self-referential `organizations` RLS
policy, so uniqueness relies on the DB constraint.

**Auth logout.** `POST /auth/logout` (204): revokes the refresh token, clears
both cookies, tolerant of unknown/revoked tokens, does not require an access
token. Verified: 135/135.

**Phase 3 frontend, Steps 1-4.** Next.js 15.5.25 scaffold with HTTPS dev;
typed API client (typed errors, one silent refresh retry on 401); auth/session
flow (client-side `RequireAuth` guard, deliberately no `middleware.ts`);
organization + scan UI (selected org id in `localStorage`, not a credential;
bounded polling; `describeApiError`). Vitest 47/47. A real-browser pass
(Playwright/Chromium vs real uvicorn, PG16, Redis) passed 49/49 with zero
product-code changes. Debt: TD #13, #14. Step 5 pending.

**Phase 4 -- Nmap.** Three sessions: adapter (20 passed), wiring into
pipeline/API (`Literal["nuclei","nmap"]`, 70 passed), XML normalizer
(one `info` finding per open port on each up host, 89 passed; TD #16
resolved). TD #15 (no real nmap binary).

**Phase 4 -- SQLMap.** Adapter only, no wiring, no normalizer (99 passed). A
follow-up re-investigated TD #18, found no real fixtures, changed no code.
TD #17, #18. A documentation-only session recorded the ReconX/BugHunter
exclusion and the "14 adapters" correction.

**Phase 5 M1-M5 (RAG).** M1 `EmbeddingPort` + sentence-transformers adapter
(8 tests; surfaced TD #19). M2 `VectorStorePort` + Qdrant adapter (9 tests;
TD #20). M3 CWE Top 25 ingestion (23 tests; vendored
`backend/data/cwe/2025_top25.xml` placed by the owner after a network
blocker; parser asserts exactly 25 entries; TD #21). M4 retrieval in
`AnalysisService` (20 new tests, `PROMPT_VERSION` v2). M5 wiring
(`_build_analysis_service`, `kb_version` populated, `rag` extras + Dockerfile
`INSTALL_RAG_DEPENDENCIES`; 82 unit tests passed in sandbox;
`test_scan_worker_task.py` not re-run; no Docker build, no real Qdrant).

**Phase 6 M1 -- RBAC on the Scanning routes (2026-10-02).** Objective:
enforce roles on the existing scan routes. Decisions: role matrix chosen
explicitly (OWNER/ADMIN/MEMBER read+create+run, VIEWER read-only); policy is
pure domain logic; API layer only maps denial to 403; layered on
`require_organization_member` (no second query, no schema/migration/RLS
change); VIEWER gets 403 before any 404. Files: `domain/identity/access.py`
(new), `api/dependencies.py` (`require_scan_write_access`),
`api/v1/scans.py`, `test_identity_access.py` (7), `test_api_dependencies.py`
(+5), `test_api_scans.py` (`TestRoleBasedAccess`, 9; one pre-RBAC test
replaced). Verified (sandbox only): 52 passed, 0 failed, 0 skipped on PG16
with a non-superuser role; mutation check failed exactly the 4 role tests
when enforcement was removed; Ruff and MyPy strict clean except a
pre-existing `E501`. Debt introduced: TD #22, #23. Not run on the real repo.

**Phase 6 M2 -- Member management with last-owner protection.** Objective:
let OWNER/ADMIN list, add (existing registered user, by email), re-role and
remove members without ever leaving an organization with zero ACTIVE OWNERs.
Decisions: matrix in `PROJECT_STATE.md` section 3 (ADMIN limited to
MEMBER/VIEWER targets and roles); owner-row lock before any read-then-write;
actor re-read under the lock; authorization before the email lookup;
soft removal (`REMOVED`) with same-row reactivation; POST-only mutations;
record-only scope -- no audit logging, no invitations, no migration/RLS/
schema/cookie/CORS change; M1 scan policy unchanged. New files:
`application/identity/manage_members.py`, `api/v1/members.py`,
`api/v1/member_schemas.py`; tests `unit/test_member_access_policy.py`,
`unit/test_manage_members.py`, `unit/test_member_dependencies.py`,
`integration/test_api_members.py`, `integration/test_membership_concurrency.py`.
Modified: `domain/identity/access.py` (member rules added), `application/
identity/errors.py` (six errors), `application/interfaces/identity_repository.py`
and `infrastructure/db/repositories/identity_repository.py`
(`lock_for_membership_change`), `api/dependencies.py` (`require_member_manager`,
organization-scoped repository providers, four use-case providers),
`main.py` (router + 403/409 handlers), `tests/unit/test_create_organization.py`
(no-op lock on the fake). **Verification, labeled:** *sandbox only (sandbox
lost afterwards):* 292 passed / 0 skipped for a replica of the M2 state
(235 new tests), 57 passed for the M1 + create-organization regression,
mutation checks (last-owner guard off -> 15 failed; row lock removed -> 11
failed). *Real repo:* code and tests transferred and size/edit-diff checked;
the two integration test files also matched their recorded SHA256s in a
scratch rebuild; *at the time* no real-repo run had happened -- **superseded: see the "Phase 6 M2 + M3 real-repo verification" entry below.**
Debt introduced: TD #25-#27 (updates to #22, #23).

**Phase 6 M3 -- Audit trail for membership changes.** Objective: make every
SUCCESSFUL membership mutation leave one durable, tenant-isolated audit row.
Decisions (approved D1-D5): record-only (no audit read endpoint, pagination or
new RBAC policy); successful changes only (denied, failed, not-found,
conflict, last-owner and no-op requests write nothing); exactly four actions;
no hardening migration/trigger/RLS change (rows stay mutable at the DB level);
IDs-only metadata (never email or name); fail-closed in the SAME transaction
(no swallowing, no second transaction, no async/fire-and-forget). A separate
transaction would block forever on the FK key-share lock held by the
organization-row `FOR UPDATE`, so the audit repository shares the request's
session (`get_org_audit_log_repository`). Reused unchanged: `audit_logs` +
RLS, `AuditLogEntry`, `AuditLogRepositoryPort`, `SqlAlchemyAuditLogRepository`;
`event_bus` untouched. New: `domain/identity/audit.py`
(`MemberAuditAction`, `MEMBER_AUDIT_RESOURCE_TYPE`), `tests/unit/
test_member_audit.py`, `tests/integration/test_member_audit.py`. Modified:
`application/identity/manage_members.py` (`_record_audit`; required `audit`
argument on the add / change-role / remove use cases; the M2 module docstring
gained the audit contract), `api/dependencies.py` (provider +
three use-case providers), `tests/unit/test_manage_members.py` (audit fake),
`tests/integration/test_membership_concurrency.py` (audit argument; exactly
the winner is audited). **Verification, labeled:** *sandbox only (sandbox
lost; the real-repo files were rebuilt afterwards):* 343 passed / 0 skipped
(292 + 51 new) and mutation checks (all audit writes off -> 33 failed;
reactivation write only -> 6; reactivation mislabeled -> 4; errors swallowed
-> 5; audit moved outside the transaction -> exactly the 8 atomicity tests
failed). *Real repo:* files present and statically reviewed (constructor call
sites, imports, ordering, wiring); *at the time* not run -- **superseded: see
the verification entry below.** Debt introduced: TD #28-#31 (update to #23).

**Phase 6 M2 + M3 real-repo verification (owner-run, 2026-10-11/12).** The
owner ran everything on Windows (CPython 3.13.7, `.venv`, PostgreSQL 16 in
Docker on host port 5433, non-superuser `app_user`); results come from the
owner's pasted output. Backend full suite **762 passed, 0 skipped, 0 failed**
(3m40s; 98% coverage, `--cov` still under-reports async code, TD #24),
including `test_api_organizations.py`. A first run without
`TEST_DATABASE_URL` showed 556 passed / 206 skipped (every Postgres test
skipped) and was not counted. `mypy app` clean (113 files). Ruff: only the
known pre-existing `E501`; format: the known `test_create_organization.py`
wrap plus five files Phase 6 never touched. Frontend Vitest 47/47. Scope
check: migrations, frontend, Docker and both historic backups untouched.
**Live walkthrough** against a real `uvicorn` + PostgreSQL: 18/18 scenarios
(M1 scan gate, M2 role matrix, last-owner and already-active 409s, 401/403/404
paths) and exactly 6 `audit_logs` rows in the expected order with IDs-only
metadata; refused, no-op and conflicting requests wrote nothing. **Test-only
fixes (all pre-existing breakage, no production code changed):**
`test_api_organizations.py` (Phase 4 rename `get_active_scanner` ->
`get_active_scanners`, 3-argument dispatcher), `test_session.py` (required
`minio_bucket`), `test_main_lifespan.py` and `test_scan_worker_task.py`
(hard-coded port-5432 URL -> `TEST_DATABASE_URL`). Environment findings: the
owner's machine has another listener on port 5432 and a Docker `backend`
image built before M2 that also answers on port 8000 (it had no member
routes; the walkthrough used a fresh `uvicorn` on port 8001); the Postgres
tests need `TEST_DATABASE_URL` (port 5433) set and the `.venv` active in
each new shell. Not verified: Docker image build, real MinIO/Qdrant/scanner
binaries, browser/mkcert path. No new technical debt was added.

## Open technical debt

Authoritative list: `PROJECT_STATE.md` section 8.
- #1 enums staging area in `domain/shared/enums.py`.
- #5 `update()`/`soft_delete()` can touch soft-deleted rows.
- #6 MinIO, #7 nuclei, #15 nmap, #17 sqlmap: verified only against
  mocks/patches, no real server/binary.
- #8 concurrent scans of the same target can hit an unhandled unique-constraint
  error.
- #11 `AI_ANALYZE` re-calls the provider on every retry.
- #12 no network-isolated `scanner_worker` split.
- #13 Next.js pinned to 15.5.25 (residual build-time PostCSS advisory).
- #14 `frontend/package-lock.json` not committed.
- #18 no `sqlmap-stdout` normalizer (deliberate).
- #19 CPU-only PyTorch image split addressed in config, not build-verified.
- #20 Qdrant adapter verified only against a mock.
- #21 CWE file's 25-entry count not exhaustively enumerated (runtime assertion
  guards it).
- #22 no role awareness in the UI/API responses; non-OWNER roles are now
  reachable through the M2 member routes, but the frontend has no UI for them.
- #23 RBAC covers the three scan routes and the M2 member routes; ADMIN ==
  MEMBER for scans. Successful membership changes are audited (M3); denials,
  scan actions, auth events and organization creation are not.
- #24 `pytest --cov` under-reports async-DB code (no
  `concurrency = ["greenlet","thread"]` in `pyproject.toml`).
- #25 `ListMembersUseCase` does one user lookup per member (N+1), no
  pagination.
- #26 add-by-email reveals to an authorized actor whether an address is
  registered; no rate limiting exists in the codebase.
- #27 no invitation, leave-organization or ownership-transfer flow.
- #28 `audit_logs` rows are mutable at the DB level (UPDATE/DELETE/TRUNCATE
  by `app_user`); append-only is a convention only.
- #29 refused/failed membership attempts are not audited.
- #30 no audit read API/pagination; action names and metadata keys are a
  stored contract.
- #31 an organization-less audit event is rejected by the current RLS
  `WITH CHECK` (unrepresentable).
Resolved: #9, #10, #16. #2-#4 do not appear in `PROJECT_STATE.md`'s debt list
(numbering gap); their status is not recorded in the current docs.

## Verification limitations that still apply
- No command-execution tool exists against the real repository; every result
  above is from a sandbox rebuild, not a run on `C:\Users\gamer\Downloads\claudeOnly`,
  **except** the owner-run Phase 6 M2 + M3 verification entry. Sandbox rebuilds
  are usually partial slices; most test files are not re-run in any given
  session.
- Phase 6 M2 and M3: their sandbox pass counts (292, then 343) came from a
  sandbox that no longer exists and are historical; the real-repo evidence is
  the owner's 762 passed / 0 skipped run and live walkthrough (2026-10-11/12).
- No Docker: compose, Dockerfile and the CPU-only torch install are unbuilt.
- No real MinIO, Qdrant, nuclei, nmap or sqlmap in any session.
- The Filesystem MCP has no delete tool and caps large reads (~500 KB).
- Verify locally or in an environment with execution: `pip install -e
  ".[dev]"`, `pytest tests/ -q`, `ruff check .`, `ruff format --check .`,
  `mypy app`.

## Cross-check performed at compaction (2026-10-02)
Compared against the repository and the other two governing docs:
- Directory listing confirmed the modules and test files named for
  Milestones 1-7, Auth, logout, organization bootstrap, `nmap`/`sqlmap`
  adapters, and Phase 5 are present; `burp`/`zap` are stubs;
  `reconx`/`bughunter` folders are absent (the pre-2026-10-02 `PROJECT_STATE.md`
  said they still existed; the compact one records the observed state).
- Observed test files: 36 unit (37 with this milestone) and 14 integration
  plus `support.py`; the pre-2026-10-02 `PROJECT_STATE.md` counts were stale
  (the compact one states these).
- Carried forward only what is in the repo or in `PROJECT_STATE.md` /
  `session_state.md`; TD #13-#21 existed only in `PROJECT_STATE.md` and are
  included above. Nothing was invented to fill gaps; discrepancies not fixed
  are listed in `docs/session_state.md`.
- Discarded: per-session narration, duplicated explanations, abandoned
  approaches (Bearer-header auth, sandbox-only transcription slips),
  superseded intermediate states (single-adapter API, synchronous `run`,
  "Phase 2 current", "Step 1 only").

## Phase 6 result so far
Milestone 1 complete and committed. Milestones 2 (member management with
last-owner protection) and 3 (audit trail for successful membership
changes): in the real working tree, uncommitted, and verified by the owner
on the real repo (762 passed / 0 skipped / 0 failed; live walkthrough; see
the verification entry above). Next for the owner: review and commit. Not started:
M4 (not defined), teams, billing, invitations / leave-organization /
ownership transfer, organization list/get/rename, `/internal/admin`, an audit
read API, auditing of denied attempts, further multi-tenancy hardening. No
Phase 7 work has begun.
