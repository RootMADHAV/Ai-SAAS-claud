# Session State

Overwritten each session -- latest session only (handoff). Current project
state: `PROJECT_STATE.md`. Compact history: `docs/implementation_progress.md`.
Never edit, rename or delete the two backups: `old historic project state.md`
(repo root) and `docs/old historic implementation_progress.md`.

## Date
2026-10-12 (Phase 6 M2 + M3 verified by the owner on the real repo)

## Workflow rule (owner)
Implement and verify in the sandbox where possible; sync to the real repo in
controlled, verifiable steps; **never commit or push** -- the owner reviews
and commits. Work one milestone at a time and stop for review.

## Last completed task
Real-repo verification of Phase 6 M2 and M3, run by the owner on Windows and
recorded from the owner's pasted output. HEAD is still `8935da9`
("compacted project state") on `main`; M2 and M3 are uncommitted.

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
- **Owner:** review the diff and commit. Suggested split: (1) M2 + M3
  together -- they share files -- with the three docs; (2) the four test-only
  fixes above as a separate commit. Do **not** include the `old historic ...`
  backups (there should be no change to them). Claude does not commit or push.
- **Owner decision:** close Phase 6 at M3 with teams and billing deferred, or
  define a Phase 6 M4. No required M4 was identified. M4 is **not started and
  not defined**; Phase 7 not started. Do not plan or begin either unprompted.
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
