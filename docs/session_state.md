# Session State

Overwritten at the end of every coding session. This file reflects the
single most recent session only -- for cumulative project history, see
docs/implementation_progress.md. For the permanent architecture/decisions
reference, see PROJECT_STATE.md.

## Date
2026-08-18

## Last completed task
Milestone 7 (Docker Compose wired end-to-end -- Celery/worker wiring).
Began with a full, fresh verification phase before writing anything:
read `PROJECT_STATE.md`, `docs/implementation_progress.md`, and this
file (finding the inconsistency above), then inspected the actual
repository via the Filesystem MCP -- directory trees of `backend/app`
and `backend/tests`, and verbatim reads of every file Milestone 7 would
build on or need to reconstruct (config.py, main.py,
api/dependencies.py, api/v1/scans.py, application/scanning/
run_scan_workflow.py, the three Milestone 6 AI modules, pyproject.toml,
.env.example, docker-compose.yml's existing placeholder,
docker-compose.dev.yml, the test suite) -- confirming the repository
matched what `PROJECT_STATE.md`/`docs/implementation_progress.md` said
going in, and that every Milestone 1-6 prerequisite Milestone 7 needed
was genuinely present, correct, and consistent. No blocker found;
proceeded to implementation with the plan and scope decisions confirmed
explicitly before writing code (see "Design decisions" below).

## Current milestone
Milestone 7 (Docker Compose wired end-to-end): complete.

Core deliverable -- `RunScanWorkflowUseCase.execute()`
(`app/application/scanning/run_scan_workflow.py`, **left completely
unmodified this session** except for one new module-docstring paragraph,
exactly as its own docstring already anticipated a milestone doing) no
longer runs synchronously inside the HTTP request handler for
`POST .../scans/{scan_id}/run`. It now runs inside a real Celery task,
dispatched by the API and executed by a dedicated `ingestion_worker`
process, with a full, working `docker-compose.yml` wiring the whole
stack together for the first time.

- `app/workers/celery_app.py` (new) -- the Celery application instance.
  Broker and result backend both point at `settings.redis_url` (required
  for every `worker_role` since Milestone 1, never actually consumed by
  any code until now). One named queue, `"scans"` -- not Celery's
  default queue -- so a future scanner-worker-specific queue (see
  Technical debt item #12) can be added later without renaming this one
  out from under an already-deployed worker.
- `app/workers/tasks.py` (new) -- three layers, deliberately kept
  separate for testability:
  - `execute_scan_workflow(...)`: a plain, fully-injectable async
    function. Takes a session factory and the three Milestone 3/6
    adapters as explicit parameters, opens one RLS-scoped session
    (`session_scoped_to_org`, exactly as `app/api/dependencies.py`'s
    `get_org_session` already does for the API process), and calls
    `RunScanWorkflowUseCase.execute()`. This is the tier
    `tests/integration/test_scan_worker_task.py` calls directly with
    fakes, against real Postgres -- mirroring
    `test_scan_pipeline_orchestrator.py`'s existing pattern for the use
    case underneath it, one layer up.
  - `_run_scan_workflow_from_settings(...)`: the worker-specific
    composition root, mirroring `app/main.py`'s `_lifespan` almost
    exactly but for `worker_role=ingestion_worker` and built fresh per
    task invocation (see below for why).
  - `run_scan_workflow_task` (the actual `@celery_app.task`): a thin
    `asyncio.run(...)` wrapper around the composition root above. A
    fresh `AsyncEngine` is constructed *inside* the same `asyncio.run()`
    call that drives one invocation and disposed before it returns --
    never cached across invocations, because `asyncio.run()` creates a
    new event loop every call and an asyncpg connection pool is bound to
    the loop that created it; reusing one across calls would reproduce
    the exact `another operation is in progress`-class failure
    `tests/conftest.py`'s own `engine` fixture already documented and
    avoided for the same underlying reason (function-scoped there, "one
    new connection pool per invocation" here). Accepted, not an
    oversight.
- `app/api/v1/scans.py::run_scan` (rewritten) -- the actual API-contract
  change (locked decision #1, confirmed explicitly before implementation
  began). No longer calls `RunScanWorkflowUseCase` at all. Instead:
  reads the scan (404 if missing, same check as before, just no longer
  routed through the use case's own `LookupError`), compares
  `scan.scanner_name` against the wired `ActiveScanner.name` (409 via
  the same `ScannerMismatchError` -> global-handler wiring, now raised
  directly from the route instead of from inside the use case), and --
  new -- guards against re-dispatching a scan whose status is already
  `RUNNING` (a new failure mode this change makes newly easy to trigger:
  since dispatch returns almost instantly, a client can call `run` twice
  in rapid succession, unlike before, when the second call would have
  queued behind the first one's synchronous execution). Returns `202
  Accepted` with the scan's current, pre-execution state either way. A
  client observes actual progress via `GET .../scans/{scan_id}`
  (unchanged).
- `app/api/dependencies.py` (rewritten) -- least-privilege, not
  unrelated cleanup, per locked decision #2 (confirmed explicitly before
  implementation began): `AppState` drops `storage`/`analysis_service`
  entirely -- this API process no longer touches `MinioStoragePort` or
  `AnalysisService`/`AnthropicProvider` at all, so it has no reason to
  hold their credentials. `get_storage`, `get_analysis_service`, and
  `get_run_scan_workflow_use_case` are removed (their only caller,
  `run_scan`, no longer exists in that form); `get_asset_repository`/
  `get_finding_repository` are removed too, since
  `get_run_scan_workflow_use_case` was their only caller and nothing
  else in this module needs them (Findings/Assets still have no HTTP
  surface -- unchanged, deferred scope). New: `get_scan_dispatcher`, the
  seam `run_scan` depends on -- a thin callable wrapping
  `run_scan_workflow_task.delay(...)`, overridden in tests with a spy
  the same way `get_active_scanner`/(the now-removed) `get_storage`
  always were.
- `app/main.py::_lifespan` (rewritten) -- stops constructing
  `MinioStoragePort`/`AnthropicProvider`/`AnalysisService`; only builds
  the session factory and `NucleiAdapter` now (the latter stays,
  credential-free, since `run_scan` still needs its `.name` for the
  cheap mismatch check). `_lookup_error_handler`'s docstring updated to
  record a genuine, notable side effect of the API-contract change --
  see "Design decisions" below.
- `app/config.py::check_role_boundaries` (edited) -- the
  `ANTHROPIC_API_KEY`-required-for-`worker_role=api` check moved to
  `worker_role=ingestion_worker`, following the process that actually
  constructs `AnalysisService` now. A direct, necessary consequence of
  the above, not a speculative change.
- `backend/Dockerfile` (new) -- multi-stage (builder installs into a
  venv; runtime copies only that venv + source, no compiler), non-root
  user, shared by both the `backend` and `worker` Compose services
  (differing only by `command:`). Verified by installing the package
  into a fresh venv via the exact same `pip install .` (no dev extras)
  the image's builder stage runs, and confirming
  `app.main`/`app.workers.tasks`/`app.workers.celery_app` all import
  cleanly from that install -- Docker itself is not available in this
  environment to build and run the actual image (see "Verification
  method" below).
- `backend/.dockerignore` (new).
- `docker-compose.yml` (root, replacing the placeholder) -- full
  topology: `postgres`, `redis`, `minio`, `qdrant`, `backend`, `worker`.
  See "Design decisions" below for the network-segmentation account,
  including the one genuine, deliberate divergence from the placeholder
  comment this file replaces.
- `.env.example`, `backend/pyproject.toml` -- `celery`/`celery-types`
  added; no new environment variable names needed (`REDIS_URL`, already
  required since Milestone 1, is what Celery's broker/backend now
  actually point at). `.env.example`'s existing Milestone 6 paragraph
  about `ANTHROPIC_API_KEY` also corrected in place -- it previously
  (accurately, at the time) said this variable was required for
  `worker_role=api`; now stale given the above, so corrected rather than
  left wrong.
- 4 new/updated test files: `tests/unit/test_celery_app.py` (3),
  `tests/unit/test_api_dependencies.py` (3, closing a real coverage gap
  -- see "Verification method"), `tests/integration/
  test_scan_worker_task.py` (3), plus `tests/unit/test_config.py` (3
  tests rewritten for the new `ANTHROPIC_API_KEY` scoping),
  `tests/integration/test_api_scans.py` (rewritten -- 13 tests, the
  contract change), `tests/integration/test_main_lifespan.py`
  (simplified -- 1 test, no longer needs MinIO/Anthropic-shaped
  credentials). 48 tests total in this session's sandbox (see
  "Verification method" for the scope of what was and wasn't
  reconstructed there), all passing; clean Ruff (lint + format), clean
  MyPy strict on every file touched.

## Design decisions made this session (all recorded in PROJECT_STATE.md
section 3's dated log, not just here)

1. **`run_scan`'s API contract changed from synchronous-200-complete to
   asynchronous-202-accepted-plus-poll**, per your explicit, locked
   decision #1, confirmed before implementation began. The cheap,
   synchronous checks (scan exists; scanner name matches) are preserved
   exactly -- same 404/409 outcomes as before, just performed by the
   route directly instead of by the use case it used to call. New: a
   scan already `RUNNING` is not re-dispatched (still returns 202) --
   a genuinely new failure mode this change makes easy to trigger (rapid
   double-`POST`), addressed with the smallest possible guard rather
   than left unhandled.
2. **The API process no longer holds MinIO or Anthropic credentials**,
   per your explicit, locked decision #2. It never touches either
   adapter as of this milestone (pipeline execution moved to the
   worker), so it has no further use for either credential --
   least-privilege, a direct and necessary consequence of the dispatch
   change, not unrelated cleanup. `ANTHROPIC_API_KEY`'s required-role
   moved from `api` to `ingestion_worker` in `app/config.py` to match.
3. **No network-isolated `scanner_worker` split was built**, per your
   explicit, locked decision #3. `RunScanWorkflowUseCase` runs
   completely unmodified inside one `ingestion_worker` Celery task --
   including its own `EXECUTE_SCANNER` step, unrestructured. This is the
   one deliberate, load-bearing scope boundary that shaped everything
   else this session, including the `docker-compose.yml` network design
   below.
4. **`docker-compose.yml`'s network topology diverges from the
   placeholder comment it replaces, and this divergence is stated
   explicitly in the compose file itself, not silently made.** The
   placeholder envisioned a `scanner-worker` on `scan-egress`,
   "deliberately NOT on internal, no route to postgres" -- i.e., a
   worker with internet access but no database access. Given decision
   #3 above (no split), the one worker this session actually builds
   (`ingestion_worker`, running the *whole*, unrestructured
   `RunScanWorkflowUseCase`, including `EXECUTE_SCANNER` -- which
   reaches arbitrary user-supplied scan targets over the network -- and
   `AI_ANALYZE` -- which reaches the Anthropic API) genuinely needs
   *both* database access *and* outbound internet at the same time. It
   cannot be given the placeholder's intended isolation without breaking
   its own required functionality. Discovered while writing the compose
   file itself, not before -- surfaced here explicitly rather than
   quietly resolved: `worker` is attached to `internal` (for Postgres),
   `queue` (for Redis/MinIO), and a new, honestly-named
   `worker-egress` network (plain, non-`internal` bridge, genuine
   internet access) -- deliberately named differently from the
   placeholder's future `scan-egress`, so the two are never confused as
   the same thing. What *is* fully achievable now, and delivered: the
   API process (`backend`) sits on `internal` (marked `internal: true`,
   genuinely no internet route) plus `queue` (to publish Celery task
   messages to Redis) -- a real, concrete security improvement this
   milestone's credential-narrowing (decision #2) made possible, since
   the API process no longer has anything it would need internet access
   *for*. Full account, including why a real scanner-worker split needs
   `RunScanWorkflowUseCase` restructured into two independently
   resumable phases (not attempted this session, per decision #3), is
   Technical debt item #12 below.
5. **No separate `minio-init`/`createbuckets` Compose service.**
   `MinioStoragePort.put_object` already creates its bucket lazily on
   first write (`_ensure_bucket`, Milestone 3, unchanged) -- a one-shot
   init container would duplicate logic that already exists and already
   runs correctly, not add a missing capability.
6. **`docker-compose.yml` reuses the existing
   `docker/dev-init/01-create-app-role-and-test-db.sql`** (already
   built for `docker-compose.dev.yml`) rather than a new, near-duplicate
   production-only init script. It creates an unused
   `security_platform_test` database alongside `security_platform` in
   this topology too -- harmless (same non-superuser `app_user` owns
   it, same RLS applies), and reusing the one already-reviewed script
   avoids two init scripts that could drift from each other for no real
   benefit. What this script's existing comment already established --
   bootstrapping via the default `postgres` superuser specifically so
   `app_user` is never itself a superuser (which would bypass Row-Level
   Security entirely) -- is unchanged and still the load-bearing reason
   it exists.
7. **`_lookup_error_handler` (the global `LookupError` -> 404 handler in
   `app/main.py`) is now unreachable from any currently-registered HTTP
   route, and is kept anyway rather than removed.** A side effect of
   decision #1, traced precisely while investigating a coverage gap (see
   "Verification method" below), not assumed: before this milestone,
   `run_scan` called `RunScanWorkflowUseCase.execute()` directly, whose
   own `LookupError` (missing scan) propagated up through this handler.
   As of this milestone, `run_scan` raises `HTTPException(404, ...)`
   itself instead, and `RunScanWorkflowUseCase.execute()` only ever runs
   inside the Celery task now, where a `LookupError` becomes a task
   failure, not an HTTP response. Every *other* `LookupError` raise site
   in the codebase (various repositories' `update()`/`soft_delete()`
   methods) was never reachable from any currently-mounted route either.
   Kept registered rather than removed: it implements a codebase-wide
   convention (every repository's mutation methods raise `LookupError`
   the same way), and Findings/Assets/Reporting's still-unbuilt HTTP
   routes (deferred scope, unchanged) are the more likely next caller of
   it -- removing genuinely useful, documented, cross-cutting
   infrastructure only to re-add it in the very next milestone that
   needs it would be net-negative churn, not a real simplification.
   Its own docstring in `app/main.py` now states this plainly, so a
   future reader (or coverage report) is not left to rediscover it from
   scratch.

## Verification method this session

Given the scope of this milestone (touching or depending on
essentially the whole backend, plus new Celery/Docker infrastructure),
the sandbox reconstruction was, again, the most extensive yet --
comparable in scope to Milestone 4's and Milestone 6's own. Every file
Milestone 7 depends on, or edits, was read verbatim through the
Filesystem MCP this session and reconstructed file-by-file in the
sandbox (including installing PostgreSQL 16 and Celery there, and
running the real Alembic migration -- with real RLS policies -- rather
than a `create_all()` substitute) before any Milestone 7 code was
written.

**A mid-session Filesystem MCP outage affected this session, stated
plainly per the verification-honesty rule rather than glossed over:**
partway through this session -- after all Milestone 7 implementation,
testing, and verification was already complete in the sandbox, while
updating a small documentation detail in `.env.example` -- the
Filesystem MCP connector stopped responding to every tool call
(`list_allowed_directories` included), with no further error detail
available, unlike Milestone 5's own outage (which at least returned a
timeout before advising against immediate retries). Handled per this
project's own standing guidance for exactly this situation: stopped
retrying immediately, continued with sandbox-only work that did not
depend on the connector (drafting documentation, running a final full
verification pass), and did not claim any file had been written to the
real repository until the connector was confirmed available again. The
connector recovered later in the session; confirmed via `get_file_info`
that no file had been left in a partially-written state by the outage
(nothing had actually been transplanted yet at the point the outage
began, so there was nothing to leave inconsistent), then proceeded with
the full transplant, byte-checking every new file and reviewing every
diff for every surgically-edited one.

**A real, substantive discovery during sandbox setup, confirmed against
the real file, not a transcription artifact:** `tests/integration/
test_api_scans.py`'s `_create_organization()` test helper, verbatim in
the real repository both before and independently re-confirmed after
this session's sandbox reconstruction, does not call `set_org_context()`
before inserting the test organization. A direct, empirical check
against a real Postgres 16 instance confirmed this genuinely fails, and
cannot be fixed by database-level provisioning alone:
`organizations`' own RLS policy (`id =
current_setting('app.current_org_id')::uuid`) requires that GUC set to
the *row's own id* before an `INSERT` can satisfy its `WITH CHECK`
clause -- registering the GUC at the database level (so
`current_setting` does not error on an unset custom parameter) still
leaves it defaulting to an empty string, which fails the `::uuid` cast
just the same. `tests/integration/test_scan_pipeline_orchestrator.py`'s
own fixture already calls `set_org_context()` first for exactly this
reason. Fixed in the version of `test_api_scans.py` written to the real
repository this session (already being rewritten for the Milestone 7
contract change regardless), called out explicitly in that file's own
updated module docstring rather than silently folded in. This is a
pre-existing defect independent of Milestone 7's own scope, not
introduced by it -- flagged here, and worth a future session checking
whether any other test file in this project uses the same
no-context-set organization-creation pattern this one did.

**A coverage investigation found one genuine, novel gap and closed it,
distinct from the previously-documented Milestone-5 measurement
artifact:** `app/api/dependencies.py`'s three small provider functions
(`get_session_factory`, `get_active_scanner`, `get_scan_dispatcher`)
showed as uncovered because `tests/integration/test_api_scans.py`
overrides all three wholesale via `app.dependency_overrides` (by
design), meaning their real bodies are never actually called by any
test -- only their replacements are. This is a different phenomenon from
the async-SQLAlchemy-greenlet coverage-tool blind spot Milestone 5
documented (which affects lines that *do* execute but are mismeasured):
this is code that genuinely does not run under the existing pattern.
Closed directly with a new, focused unit test file,
`tests/unit/test_api_dependencies.py`, calling all three functions
directly against a constructed `AppState`/mocked `Request` -- the same
"close a real gap the investigation found" move Milestone 5 made for
`_lifespan` via `test_main_lifespan.py`. The remaining uncovered lines in
`app/api/dependencies.py` (`get_org_session`'s own body) and
`app/api/v1/scans.py` (every route handler's body) were re-confirmed,
by hand, to be the same greenlet-measurement artifact Milestone 5
already documented -- `get_org_session` was called directly outside
pytest this session (mirroring Milestone 5's own verification method
exactly) and observed to execute both its existence-check and its yield
line correctly, and every route-handler branch scans.py's own uncovered
lines belong to has an explicit, passing assertion in
`tests/integration/test_api_scans.py`'s 13 tests covering it -- not
asserted without checking.

**Docker itself is not available in this environment** (no `docker`/
`docker-compose` binary), so `docker-compose.yml` and the `Dockerfile`
were verified by the strongest means actually available here, stated
precisely rather than overclaimed: the compose file was checked for
valid YAML and its service/network/dependency graph programmatically
inspected (`pyyaml`) to confirm the six services, three networks, and
`depends_on: condition: service_healthy` wiring match this session's
design exactly; the Dockerfile's `pip install .` step (no dev extras)
was run for real, into a fresh virtualenv, and every module the two
Compose services' `command:` entries need
(`app.main`, `app.workers.tasks`, `app.workers.celery_app`) was
confirmed to import cleanly from that install. Neither compose-file
`docker-compose config` validation nor an actual container build/run
was possible here -- a real Docker environment (e.g. the person's own
machine, or an environment with execution access) is required to
confirm that final tier, the same category of gap this project has
already stated plainly for other adapters with no reachable real server
in this sandbox (MinIO, `nuclei`).

In the sandbox:

    cd backend && pip install -e ".[dev]"
    export TEST_DATABASE_URL=postgresql+asyncpg://app_user:app_password@localhost/security_platform_test
    # schema created via `alembic upgrade head` against a real,
    # verbatim-reconstructed migration this session -- unlike
    # Milestone 5's own sandbox, no tool outage forced a create_all()
    # substitute this time; real RLS policies were present throughout
    pytest tests/ -v --cov=app --cov-report=term-missing   # 48 passed
    ruff check .                 # All checks passed!
    ruff format --check .        # all files already formatted
    mypy app                     # Success: no issues found in 87 source files
    mypy tests                   # Success: no issues found in 12 source files
                                  # (only the files this session touched/added,
                                  # plus every file needed to import them --
                                  # the full historical test suite (test_ids.py,
                                  # test_clock.py, the Milestone 2 repository
                                  # integration suite, etc.) was not
                                  # reconstructed in this session's sandbox,
                                  # only what Milestone 7 needed to verify --
                                  # see the note on this below)

**Scope of this session's sandbox test reconstruction, stated plainly:**
only the test files Milestone 7 actually touches, depends on, or adds
were reconstructed and run this session (`test_api_scans.py`,
`test_main_lifespan.py`, `test_scan_pipeline_orchestrator.py`,
`test_health_ready.py`, `test_config.py`, plus this session's four new
files) -- 48 tests total. The remaining ~20 test files this project's
real repository also contains (`test_ids.py`, `test_clock.py`,
`test_domain_entities.py`, `test_base_scanner.py`,
`test_target_validation.py`, `test_minio_storage.py`,
`test_nuclei_adapter.py`, the Milestone 2 repository integration suite,
etc.) were not copied into this session's sandbox and were not
re-executed here -- their own last-verified-passing state remains
whichever session actually wrote and verified them, unchanged and not
re-confirmed by this one, the same "not a renewed byte-for-byte
re-verification of every earlier file's own test suite" caveat Milestone
4's own session already stated for the identical situation. What this
session's 48-test run does newly confirm: every module Milestone 7 edits
or adds integrates correctly with the real, unmodified Milestone 1-6
source it depends on, against a real, RLS-enabled Postgres 16 instance.

Every new file, and every modified file, was written into the real
repository via the Filesystem MCP and spot-checked -- by byte count for
every new file (all matched exactly except one, `app/api/dependencies.py`,
off by 6 bytes for a reason not conclusively identified despite
investigation; a full content read-back confirmed the real file's text
is complete and correct, matching the sandbox-verified source exactly,
so this is noted rather than treated as a sign of missing/corrupted
content) -- and by reviewing the returned diff for every
surgically-edited file (`app/config.py`, `app/application/scanning/
run_scan_workflow.py`, `tests/unit/test_config.py`,
`backend/pyproject.toml`, `.env.example`), confirming each diff matched
exactly what was intended before moving to the next file.

## Current implementation status
All Milestone 7 files (2 new source files in `app/workers/`, 1 new
`Dockerfile`, 1 new `.dockerignore`, 3 new test files, 4 files rewritten
in full, 5 files surgically edited, plus `docker-compose.yml` replacing
the root placeholder) are now present on disk at
`C:\Users\gamer\Downloads\claudeOnly`, matching the sandbox-verified
content (see byte counts and diff review above). `docs/session_state.md`
(this file), `docs/implementation_progress.md`, and `PROJECT_STATE.md`
are being updated to match as the final step of this session.

## Pending work
- New Technical debt item #12 this session: **the network-isolated
  `scanner_worker` split named in `PROJECT_STATE.md`'s own placeholder
  Docker Compose comment (and in the roadmap leading into this
  milestone) was not built.** `RunScanWorkflowUseCase` still runs as one
  atomic, unrestructured call inside the `ingestion_worker` Celery task
  -- including its own `EXECUTE_SCANNER` step, which reaches arbitrary
  user-supplied scan targets over the network from the same process that
  holds database credentials. Building the split properly requires
  restructuring `RunScanWorkflowUseCase` into (at minimum) two
  independently-schedulable phases with a durable hand-off between them
  (mirroring how `EXECUTE_SCANNER`'s own raw output is already durably
  stashed in `StoragePort` under a deterministic key, rather than held
  only in memory) -- a genuine architectural change, not "more Celery
  wiring," and explicitly out of scope for this session per your locked
  decision #3. Code-level scan-target safety (`validate_target`'s
  SSRF/private-IP rejection, argument-list-only subprocess execution,
  enforced timeouts, non-root execution) is unchanged and fully
  enforced regardless -- this debt item is about network-level
  defense-in-depth on top of those checks, not a gap in them.
- The `_create_organization()` fix noted under "Verification method"
  above was applied only to `tests/integration/test_api_scans.py` (the
  one file this session was already rewriting). A future session should
  check whether the same no-context-set pattern exists in any other test
  file that creates an organization via a raw session, rather than
  assuming this was the only instance.
- Pre-existing pending items unchanged from Milestone 6: real-server
  integration tests for `MinioStoragePort`/`NucleiAdapter`; the eight
  `docs/*.md` files; Technical debt items #5 and #8.
- Findings/Assets/Reporting still have no HTTP surface -- unchanged,
  deliberately deferred scope (Milestone 5's own design decision,
  restated as still applicable in `docs/implementation_progress.md`'s
  "Files pending" every session since).
- Docker itself cannot be exercised in this environment -- a real
  `docker compose up` run (confirming the six services actually start,
  reach a healthy state, and a real end-to-end scan completes through
  the dispatched task) is a one-time manual verification step for the
  person, on their own machine or an environment with Docker access, the
  same category of gap already stated for MinIO/`nuclei` real-server
  testing.

## Next immediate task
Wait for explicit approval before beginning Milestone 8 or any Phase 3+
work.

## Session notes
- This session hit a real, external tool-availability interruption
  mid-session -- the Filesystem MCP connector stopped responding to
  every call, with less diagnostic information than Milestone 5's own
  outage (that one at least returned a timeout error before advising
  against retries; this one returned "tool not found" for every call,
  suggesting the connector itself, not just one in-flight request,
  became unavailable). Handled the same way Milestone 5's outage was:
  stopped retrying immediately, informed the person plainly, and
  continued exactly the sandbox-only work that did not depend on the
  connector (documentation drafting, final verification) rather than
  either guessing at file state or blocking entirely. The connector
  recovered later in the same session and the transplant proceeded
  normally once it did.
- The most consequential judgment calls this session were the three you
  locked explicitly before implementation began (API-contract change;
  credential narrowing; no scanner-worker split) plus one genuine design
  gap discovered only while actually writing `docker-compose.yml`
  itself: the placeholder's envisioned network isolation for a
  scanner-worker is not achievable for the *single, unsplit* worker this
  session actually builds, since that worker needs both database access
  and internet access simultaneously. Surfaced explicitly, in the
  compose file's own comments and in this document, rather than quietly
  built around -- see design decision #4 above.
- A genuine, pre-existing defect independent of this milestone's own
  scope was also found and fixed along the way (see "Verification
  method" above): a test helper that would fail against the real RLS
  policy it exercises, discovered only because this session's sandbox
  reconstruction ran the real Alembic migration (with real RLS) rather
  than a schema substitute. Fixed in the one file already being rewritten
  for Milestone 7's own reasons; flagged, not silently absorbed.
- No Milestone 8+ functionality was implemented or scaffolded this
  session. The network-isolated scanner-worker split, Celery
  `autoretry_for`/retry policy, and any Findings/Assets/Reporting HTTP
  surface were all considered during design and deliberately left
  untouched -- see design decisions and Technical debt item #12 above
  for why each doesn't yet exist, rather than silently building toward
  them.
