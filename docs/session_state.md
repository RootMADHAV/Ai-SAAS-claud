# Session State

Overwritten each session -- latest session only (handoff). Current project
state: `PROJECT_STATE.md`. Compact history: `docs/implementation_progress.md`.
Never edit, rename or delete the two backups: `old historic project state.md`
(repo root) and `docs/old historic implementation_progress.md`.

## Date
2026-10-12 (Phase 7 M1 implemented; Phase 6 M2 + M3 verified earlier the same day by the owner)

## Workflow rule (owner)
Implement and verify in the sandbox where possible; sync to the real repo in
controlled, verifiable steps; **never commit or push** -- the owner reviews
and commits. Work one milestone at a time and stop for review.

## Last completed task -- Phase 7 M1 (frontend scan-flow polish + component tests)
Frontend only. No backend, database/RLS, API, dependency, lockfile, config or
git change; nothing staged or committed.

**Changed (`frontend/`):**
- `src/lib/api/types.ts` -- `SCANNER_NAMES = ["nuclei", "nmap"]`, `ScannerName`;
  `ScanCreateRequest.scanner_name?: ScannerName` (was `"nuclei"` only; the
  backend contract is `Literal["nuclei", "nmap"]`).
- `src/components/scan-detail.tsx` -- shows `Scanner: <scanner_name>`; each
  workflow step now also shows `Retries: N` (only when N > 0), `Started:` /
  `Completed:` (fixed UTC text via the new `src/lib/format-timestamp.ts`) and
  `Error: <error_message>` when present.
- `src/components/new-scan-form.tsx` -- comment only. The form is unchanged:
  target only, scanner label "nuclei", scanner not sent (backend default). A
  scanner selector was **not** added (not asked for; owner decision if wanted).
- New tests: `src/components/new-scan-form.test.tsx` (4),
  `src/components/scan-detail.test.tsx` (12), `src/app/dashboard/page.test.tsx`
  (5), `src/lib/format-timestamp.test.ts` (3); one test added to
  `src/lib/api/endpoints.test.ts` (nmap `scanner_name` is sent). The new
  component tests mock `useSelectedOrganizationId`, so they do not depend on
  `localStorage`.
- New doc: `docs/frontend_manual_checklist.md` (auth -> scan workflow).

**Evidence:**
| Check | Result |
|---|---|
| Owner baseline (real repo, before M1; owner-pasted) `npm test` | **43 passed, 4 FAILED** -- all four in `use-selected-organization.test.ts`: `window.localStorage` is `undefined` (Node printed `ExperimentalWarning: localStorage is not available because --localstorage-file was not provided`). Pre-existing, not caused by M1. Phase 6 recorded 47/47 on the same machine earlier. Owner re-ran after M1: the same 4 still failed. **Diagnosed and fixed in the follow-up below.** |
| Owner baseline `npm run typecheck`, `npm run lint` | both exit clean (no output) |
| Sandbox (Linux, Node 22.22, `npm install` from package.json only -- no lockfile), before M1 | vitest 47/47 (6 files), `tsc --noEmit` 0, `eslint` 0 |
| Sandbox, after M1 | vitest **72/72 (10 files)**, `tsc --noEmit` 0, `eslint` 0. Mutation check: removing the step `Error:` line fails the scan-detail test |
| Files copied to the real repo | `get_file_info` byte size equals the sandbox size for every changed/new source and test file |

**NOT run on the real repo after M1:** `npm test`, `npm run typecheck`, `npm run lint`
(the owner runs them). If the 4 pre-existing localStorage failures persist,
expect 68 passed + 4 failed (72 total): 43 baseline passes + 25 new tests.
The manual checklist has not been executed against a live stack, and no real
scan has been exercised from the UI.

**Follow-up -- the 4 `use-selected-organization` failures (root cause + fix):**
Node >= 25 ships built-in Web Storage, so `localStorage` is already a global
(on Node 26 a getter that warns and returns `undefined` unless
`--localstorage-file` is given). Vitest 4.1.11's jsdom environment
(`populateGlobal`/`getWindowKeys`) copies a jsdom window property onto the
global only if that name is not already a global or is on its fixed `KEYS`
list; `localStorage` is not on that list, so jsdom's `localStorage` is skipped
and the tests read Node's. Not a hook bug; hook and tests unchanged.
Fix: `frontend/vitest.config.ts` now sets `test.execArgv` to
`["--no-experimental-webstorage"]`, only when `process.allowedNodeEnvironmentFlags`
recognises it (an unknown flag kills the worker on Node 20). Sandbox evidence
(same package versions, vitest 4.1.11 / jsdom 30.0.1): the 4 tests fail on
Node 25.9.0 and 26.11.1 and pass on 22.22.0 and 24.21.0; with the fix the full
suite is 72/72 on Node 22.22.0, 24.21.0, 25.9.0 and 26.11.1; `tsc` and `eslint`
clean. Node 26.11.1 reproduces the owner's exact error text. **Owner-run
confirmation (real repo, Windows, Node v26.7.0, 2026-10-13): `npm test` =
10 files / 72 tests passed.** The owner's post-fix `npm run typecheck` and
`npm run lint` output was not reported. Node 20.20.2
fails for an unrelated pre-existing reason (jsdom 30 needs Node ^22.22.2 ||
^24.15.0 || >=26; `webidl.util.markAsUncloneable` error) with or without the
fix.

**Waiting:** owner approval before M2. Roadmap (recommendation only, nothing else
approved): M2 scan history, M3 findings read API, M4 findings/AI UI, M5 org
listing/switching (needs an RLS-safe design first), M6 optional member UI.

## Previous task -- Phase 6 M2 + M3 verification (earlier 2026-10-12)
Run by the owner on Windows and recorded from the owner's pasted output. At the
time HEAD was `8935da9`; the owner's later `git show --stat HEAD` shows
`bc45cd9` ("phase 6 teams and billing completed", on `main` and `origin/main`)
containing M2 + M3 and the test-only fixes (25 files). The commit message is a
label, not proof that Phase 6 is closed -- see Pending.

**Result (owner-run, real repo, PostgreSQL 16 on host port 5433 as
non-superuser `app_user`):**
| Check | Result |
|---|---|
| Backend full suite (`pytest tests -q -rs`, `TEST_DATABASE_URL` set) | **762 passed, 0 skipped, 0 failed** (3m40s, 98% coverage) |
| An earlier run without `TEST_DATABASE_URL` | 556 passed / 206 skipped -- every Postgres test skipped; NOT counted |
| `mypy app` | clean (113 files) |
| `ruff check` | only the known pre-existing `E501` (`app/api/dependencies.py`); the four edited test files pass |
| `ruff format --check` | the known `test_create_organization.py` wrap plus five files Phase 6 never touched |
| Frontend `npm test` | 47/47 |
| Scope check | migrations, frontend, Docker files and both historic backups untouched |
| Live walkthrough (fresh `uvicorn` on port 8001 + real PostgreSQL) | 18/18 scenarios pass; `audit_logs` holds exactly 6 rows: added, added, added-by-admin, role_changed, removed, reactivated; IDs and roles only; refused / no-op / conflict requests wrote nothing |

**Test-only fixes made this session (pre-existing breakage, not M1-M3; no
production code changed):**
- `backend/tests/integration/test_api_organizations.py` -- imports
  `get_active_scanners` (Phase 4 rename), 3-argument `_noop_dispatcher`
  (`organization_id`, `scan_id`, `scanner_name`), `get_active_scanners`
  override returns a tuple.
- `backend/tests/integration/test_session.py` -- `minio_bucket="test-bucket"`
  added to the `Settings(...)` call (field is now required).
- `backend/tests/integration/test_main_lifespan.py` and
  `test_scan_worker_task.py` -- hard-coded port-5432 database URL replaced by
  `os.environ.get("TEST_DATABASE_URL", <old default>)`. One test in
  `test_scan_worker_task.py` (`test_composition_root_rejects_an_unimplemented_ai_provider`)
  keeps the old URL on purpose; it passes because the error fires before any
  DB connect.

## What is still NOT verified
Docker image build (compose, Dockerfile, CPU-only torch), real MinIO / Qdrant /
nuclei / nmap / sqlmap, the mkcert/browser path, and anything beyond the
tests, type check and live walkthrough listed above. The audit table is still
mutable at the DB level (TD #28) -- by design for M3.

## Environment findings (owner's machine)
- Another listener answers on host port 5432; use port **5433** (compose
  Postgres) via `TEST_DATABASE_URL` in **every new PowerShell window**, with
  `backend\.venv` activated (otherwise Postgres tests skip, or collection
  fails on `qdrant_client` / `sentence_transformers`).
- The Docker `claudeonly-backend-1` image predates M2 and also answers on port
  8000 (no member routes). For live testing run `uvicorn app.main:app --port
  8001` from `backend` with the dev env vars, or rebuild the image
  (`docker compose build backend`; compose needs `ANTHROPIC_API_KEY` set to any
  value just to parse the file).
- Windows PowerShell 5.1: hand-set `Cookie:` headers are dropped by
  `Invoke-WebRequest`; send the access token through a `WebRequestSession`
  cookie jar instead (the Secure flag stops the jar resending it over HTTP).
- The walkthrough's test users and organizations now live in the
  `security_platform_test` database; the next full `pytest` run truncates them.

## Pending
- **Owner:** review and commit the Phase 7 M1 working-tree changes (listed
  above). Claude does not stage, commit or push. Do not include the
  `old historic ...` backups.
- **Owner:** (optional) re-run `npm run typecheck` and `npm run lint` in
  `frontend/` and report them; only `npm test` was reported after the fix.
- **Owner decision:** two small unrequested fixes found while debugging local
  startup, NOT made: (1) `frontend/README.md` says to put `CORS_ALLOWED_ORIGINS`
  in the backend `.env`, but `get_cors_allowed_origins()` reads `os.environ`
  directly, so it must be a real environment variable in the shell that starts
  uvicorn (same for `DATABASE_URL` with `alembic`); (2) `register/page.tsx` maps
  errors itself, so a down backend shows "Something went wrong" instead of
  `describeApiError`'s "Could not reach the server" (login page not checked).
- **Owner decision:** close Phase 6 at M3 with teams and billing deferred, or
  define a Phase 6 M4. No required M4 was identified. M4 is **not started and
  not defined**. Phase 7: only M1 is approved and done; do not begin M2+
  unprompted.
- Open owner decision: Phase 3 Step 5 scope.
- Open debt: `PROJECT_STATE.md` section 8 (TD #25-#27 from M2; #28-#31 from M3:
  mutable audit rows, refused attempts unaudited, no audit read API, org-less
  audit events unrepresentable under RLS; #22-#23 updated). No new debt this
  session.

## Observations carried forward (not fixed)
- `.kilo/worktrees/brassy-plane/` is a second repo copy inside the project
  directory; not authoritative.
- `backend/.venv` is CPython 3.13.7 vs documented 3.12 (compatible).
- `docker-compose.dev.yml` is referenced by comments in
  `docker/dev-init/01-create-app-role-and-test-db.sql` but does not exist in
  the repo; the main `docker-compose.yml` (postgres on host port 5433) uses
  the same init script.
- PyJWT emits `InsecureKeyLengthWarning` for secrets under 32 bytes (the unit
  and integration tests use short ones; production must set a long `JWT_SECRET`).
