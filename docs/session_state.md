# Session State

Overwritten at the end of every coding session — reflects the most
recent session only. Cumulative history: `docs/implementation_progress.md`.
Permanent architecture/decisions reference: `PROJECT_STATE.md`.

## Date
2026-08-30

## Last completed task
Phase 3 backend preparation, full session (two parts: initial
implementation, then a full real-verification follow-up in the same
session). Resolved 2 of the 3 frontend-handoff blockers identified in
the prior planning session — CORS and organization bootstrap — with the
third (Secure cookies over local HTTP) deliberately left unresolved
because relaxing it conflicts with a locked decision.

1. **CORS** — `app/config.py`'s `get_cors_allowed_origins()` (reads
   `CORS_ALLOWED_ORIGINS` directly, not through `Settings` — see its own
   docstring) plus `app/main.py`'s `create_app()`, which now optionally
   takes `cors_allowed_origins` and registers `CORSMiddleware` only when
   configured. Default behavior (unset) is unchanged.
2. **Organization bootstrap** — `CreateOrganizationUseCase`
   (`app/application/identity/create_organization.py`) +
   `POST /api/v1/organizations` (`organizations.py` +
   `organization_schemas.py`), wired via four new dependencies in
   `app/api/dependencies.py`. Creates an `Organization` and an
   OWNER/ACTIVE `OrganizationMember` for the caller in one call.
3. **Secure cookie blocker** — deliberately not touched (conflicts with
   the locked `secure=True`-unconditional decision). Local frontend dev
   needs either HTTPS or a same-origin proxy arrangement — see
   PROJECT_STATE.md §1/§16 for the two real options; needs Madhav's
   decision, not a silent code change.

## The important part of this session: verification found and fixed a real bug
The first pass of this work (previous session) validated
`CreateOrganizationUseCase` only with a Postgres role that happened to
be a superuser. This session redid verification properly — full `app/`
package reconstructed, real Postgres, migration applied, **and the test
role's superuser flag explicitly removed** — and that surfaced a real
defect superuser status had been silently masking:

`organizations`' RLS policy is self-referential
(`id = current_setting('app.current_org_id')::uuid`), and
`CreateOrganizationUseCase` runs inside a session scoped to the *new*,
not-yet-existing organization's own id. A query against `organizations`
from inside that session can therefore only ever see a row whose id
equals the new org's id — never any *other* organization's row. The
original `get_by_slug` pre-check (mirroring `RegisterUserUseCase`'s
duplicate-email check) relied on exactly the query this can't do: it
silently returned "not found" for every real duplicate slug, letting the
INSERT reach the database and fail with a raw, unhandled `IntegrityError`
(an opaque 500) instead of the intended clean 409. A superuser role
bypasses RLS entirely, so this was invisible until verification used a
properly-privileged role — the exact same class of masking this
project's own `PROJECT_STATE.md` §14 already documents for the
`organizations`-RLS-self-blindness bug found during the original
Milestone 2 work.

**Fix:** removed the non-functional pre-check; `add()` is now wrapped in
a `try`/`except IntegrityError`, translating a real duplicate-key
violation (the database's own `UNIQUE(slug)` constraint, which — unlike
RLS — is enforced globally, not row-visibility-filtered) into
`OrganizationSlugAlreadyTakenError`. This is the identical fix Technical
Debt #8 already prescribes for an analogous RLS/uniqueness interaction
("catch the unique-constraint violation"), applied for real here instead
of left as documented future work.

A second, smaller bug (test-only): `test_api_organizations.py`'s
`test_create_organization_makes_the_caller_an_active_owner` read
`organization_members` directly via `db_session` without first calling
`set_org_context` — `organization_members` carries its own RLS policy
too, so this raised `current_setting(...)` errors under a real
(non-superuser) role. Fixed by adding the missing `set_org_context`
call, matching `test_identity_repository.py`'s existing convention.

## Files changed
New: `app/application/identity/create_organization.py`,
`app/api/v1/organizations.py`, `app/api/v1/organization_schemas.py`,
`tests/unit/test_create_organization.py`, `tests/unit/test_cors.py`,
`tests/integration/test_api_organizations.py`.
Modified: `app/application/identity/errors.py`, `app/api/dependencies.py`,
`app/config.py`, `app/main.py`, `app/api/v1/__init__.py` (docstring),
`.env.example`, `tests/unit/test_config.py` (5 new tests appended),
`PROJECT_STATE.md`.

## Verification — exact results (this follow-up pass)
- Full `app/` package (all six bounded contexts, infrastructure,
  workers, scanner_engine, ai_agents) plus `tests/conftest.py` and
  `tests/integration/support.py` reconstructed verbatim from the real
  repository into Claude's sandbox; `import app.main` succeeded.
- Real PostgreSQL 16 installed in-sandbox; `alembic upgrade head`
  applied the actual `6bdbf0ab25b0_initial_schema` migration
  (19 tables + RLS policies) against a **non-superuser** `app_user`
  role (`rolsuper=false`, `rolbypassrls=false` — explicitly checked).
- **`pytest tests/ -q` → 101 passed, 0 failed** — covering every new
  test file plus the directly-relevant existing regression set
  (`test_api_scans.py`, `test_api_auth.py`, `test_identity_repository.py`,
  `test_api_dependencies.py`, `test_main_lifespan.py`, `test_health.py`,
  `test_register_user.py`).
- Ruff on all new/modified files: clean except one pre-existing,
  untouched line in `app/api/dependencies.py` (`require_organization_member`,
  103 chars against the 100-char limit) — not introduced by this
  session, not fixed (out of scope), flagged for a future session.
- MyPy `--strict` on all new/modified source files
  (`create_organization.py`, `errors.py`, `config.py`, `dependencies.py`,
  `main.py`, `organizations.py`, `organization_schemas.py`) and all
  new/modified test files: **clean, 0 issues**.
- The two `I001` import-sort findings flagged as "likely sandbox
  artifacts" in the prior session's partial verification are confirmed
  as such — they disappear entirely once the full package tree is
  present, and this session's Ruff run (full tree) reports zero import
  issues on any changed file.

## Pending work
Blocker 3 (Secure cookies over local HTTP) — needs Madhav's explicit
decision (local HTTPS dev setup vs. an approved, documented
environment-conditional relaxation) before Phase 3 frontend
implementation begins. `CORS_ALLOWED_ORIGINS` not yet added to
`docker-compose.yml`'s `backend` service (harmless — unset default is
identical to pre-existing no-CORS behavior). No "list my organizations"
endpoint — a returning user's frontend has no way to rediscover an
`organization_id` across sessions beyond what it cached at creation
time; flagged as a known Phase-3-relevant gap, not built (out of this
session's literal minimum-mechanism scope). Pre-existing items
unchanged: Technical Debt #5, #8 (note: #8's prescribed fix was just
applied for real in an analogous case, but #8 itself, the concurrent-scan
asset race, remains unfixed), #11, #12; Findings/Assets/Reporting HTTP
surface; the eight `docs/*.md` files; Docker cannot be exercised here;
the `E501` finding above. The ~75 other existing test files in the
suite were not re-run this session (out of scope — this session's
regression set was the directly-relevant subset, not the full suite).

## Next immediate task
Get Madhav's decision on blocker 3. Phase 3 frontend implementation can
begin once that decision is made — backend preparation itself is now
complete and verified (101/101, real Postgres, real RLS enforcement,
Ruff/MyPy clean). Wait for explicit approval before starting any further
backend work — RBAC, OAuth, MFA, password reset, email verification, a
logout endpoint, a CSRF double-submit token, Technical Debt #12, full
organization management, or Phase 3 frontend work itself.
