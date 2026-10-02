# Session State

Overwritten at the end of every session -- reflects the most recent
session only (handoff). Current project state: `PROJECT_STATE.md`.
Compact milestone history: `docs/implementation_progress.md`.

## Date
2026-10-02

## Last completed task
**Phase 6 Milestone 1 -- RBAC enforcement on the existing Scanning
routes.** Twelfth explicit-instruction session. Began with a planning-only
step (read the three governing docs, inspected the repo, proposed scope,
stopped for approval); implemented only after the plan and role matrix
were approved. Phase 6 had no milestone breakdown anywhere in the repo --
only the roadmap row "Multi-tenancy hardening, RBAC, orgs/teams,
billing" -- so the slice (RBAC on scan routes) and the matrix were
proposed by Claude and explicitly approved.

Role matrix enforced:

| Role | Read scans | Create / run scans |
|---|---|---|
| OWNER | yes | yes |
| ADMIN | yes | yes |
| MEMBER | yes | yes |
| VIEWER | yes | **no (403)** |

## Exact changes
- `backend/app/domain/identity/access.py` (new, 2652 B) --
  `SCAN_WRITE_ROLES`, `can_write_scans()`; pure domain logic.
- `backend/app/api/dependencies.py` (edited) -- new
  `require_scan_write_access`, layered on `require_organization_member`
  (which is unchanged, as is its RLS transaction reuse).
- `backend/app/api/v1/scans.py` (edited) -- `create_scan` and `run_scan`
  depend on it; `get_scan` unchanged; docstrings updated.
- `backend/tests/unit/test_identity_access.py` (new) -- 7 tests.
- `backend/tests/unit/test_api_dependencies.py` (edited) -- +5 tests.
- `backend/tests/integration/test_api_scans.py` (edited) -- replaced the
  old `test_viewer_role_member_can_still_read_and_create` (asserted
  pre-RBAC behavior) with `TestRoleBasedAccess` (9 tests).
- No test-helper change: `make_member`/`_create_authenticated_member`
  already accepted a `role`. No migration, schema, RLS, cookie, Docker,
  scanner, AI/RAG, or frontend change (the frontend already maps 403).
- Docs: `PROJECT_STATE.md` (targeted edits only -- header, sections 1, 4,
  5, 6, 7, 8, 9, 11, 12 [TD #22-24], 13, 16), this file, and
  `docs/implementation_progress.md` (replaced with a compact file).

## Verification -- exact results (SANDBOX ONLY)
- Sandbox: Python 3.12.3, PostgreSQL 16, non-superuser `app_user`
  (`rolsuper=f`, `rolbypassrls=f`), schema via Alembic, 15 RLS policies.
- Narrow first: unit files -> 21 passed. Integration `test_api_scans.py`
  -> 31 passed. Whole reconstructed slice -> **52 passed, 0 failed, 0
  skipped.** (A first integration run was 31 *skipped* because Postgres
  had stopped between tool calls; not counted, restarted and re-run.)
- Mutation check: routes reverted to the old gate -> exactly 4 role tests
  fail; restored -> 31/31 pass.
- Ruff + `ruff format --check` clean on touched files except the
  pre-existing `E501` on `dependencies.py` (left untouched); MyPy strict
  clean (70 files). One MyPy finding and one Ruff `SIM300` in new tests
  were fixed. `access.py`/`scans.py` 100% covered (greenlet-aware
  measurement; see TD #24).
- **NOT verified / boundary:** nothing was run on the real repository (no
  execution tool). The sandbox rebuilt only the dependency slice of
  `app.main`: untouched files re-typed with docstrings elided; Celery
  task module, both scanner adapters, `config.py`'s validator, and
  `run_scan_workflow.py` were stubs; the migration was a hand-condensed
  copy. `access.py` is byte-identical to the repo; the four edited files
  are not byte-comparable. ~50 other test files were not re-run. No
  Docker, no real MinIO/Qdrant/scanner binaries.

## Pending work
- **Run the real suite** where execution exists (e.g. Claude Code):
  `pytest tests/ -q`, `ruff check .`, `ruff format --check .`,
  `mypy app`. No real-repo run exists for this milestone.
- **Commit before further work.** `docs/implementation_progress.md` was
  replaced (138,022 B -> compact). Its prior content is recoverable only
  from git history; its last modification (2026-09-30) predates the last
  commit ("phase 5 qdrant/RAG implemented", 2026-10-01), which suggests
  it is in `HEAD` -- not confirmed (no git access).
- Remaining Phase 6 (not started, no milestone breakdown): teams,
  billing, member management/invitations, organization list/get/rename,
  `/internal/admin`, audit-log writes, other multi-tenancy hardening.
- New debt TD #22-24 (`PROJECT_STATE.md` sections 12); all earlier open
  items unchanged (TD #18, TD #19/#20/#21, Step 5, sqlmap wiring).

## Documentation inconsistencies observed, deliberately NOT fixed
(per instruction not to spend this milestone on historical cleanup)
1. `PROJECT_STATE.md` sections 5/13 say the `reconx`/`bughunter` stub
   folders still exist and could not be deleted; the directory listing
   this session shows only `burp`, `nmap`, `nuclei`, `sqlmap`, `zap`
   under `scanner_engine/adapters/` -- they are already gone.
2. Test-file counts: section 11 still says "30 unit, 14 integration";
   the directory listing showed 36 unit (now 37) and 14 integration
   test files plus `support.py` (section 5 says 13 integration).
3. Section 7's table has no Phase 5 Milestone 5 row (it is covered in
   sections 4/11/16).
4. Section 16 and parts of section 11 still carry long per-session
   narrative; `PROJECT_STATE.md` is ~119 KB, not compact.
5. `.kilo/worktrees/brassy-plane/` is a second copy of the repo (with its
   own `PROJECT_STATE.md`) inside the project directory -- not
   authoritative; ignored.
6. `backend/.venv` appears to be CPython 3.13 (from `__pycache__`
   names) while docs say Python 3.12; `requires-python >= 3.12`, so
   compatible, but worth knowing when comparing local vs. sandbox runs.

## Next immediate task
None defined. Stopped after Phase 6 M1 per instruction -- no Phase 6 M2
and no Phase 7 begun. Phase 6 M2's scope must be chosen and approved
before any work starts.
