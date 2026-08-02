# Session State

Overwritten at the end of every coding session. This file reflects the
single most recent session only -- for cumulative project history, see
docs/implementation_progress.md. For the permanent architecture/decisions
reference, see PROJECT_STATE.md.

## Date
2026-08-02

## Last completed task
Milestone 4 (Processing pipeline orchestrator). Read PROJECT_STATE.md,
implementation_progress.md, and session_state.md fresh from disk first,
per standing workflow, then inspected the actual repository via the
Filesystem MCP before writing anything, per this project's "repository
is ground truth over documents" rule. Confirmed the repository matched
what the documents said this time (Milestone 4 genuinely not started --
`app/application/scanning/` contained only a placeholder `__init__.py`,
matching both PROJECT_STATE.md section 15 and implementation_progress.md's
roadmap table) -- unlike the Milestone 2 (`conftest.py`) and Milestone 3
(whole-milestone) discoveries in prior sessions, this session's
consistency check found no discrepancy to flag before proceeding.

## Current milestone
Milestone 4 (Processing pipeline orchestrator): complete.
- `app/domain/scanning/entities.py` (extended, not rewritten) --
  `PIPELINE_STEP_ORDER` (the locked eight-step sequence as a typed
  tuple, single source of truth for `TriggerScanUseCase` and
  `RunScanWorkflowUseCase`) and `derive_scan_status(steps)` (pure
  function: any `FAILED` -> `FAILED`; all `COMPLETED`/`SKIPPED` ->
  `COMPLETED`; otherwise `RUNNING`; no steps -> `QUEUED`). `Scan`,
  `ScanWorkflowStep`, and `ScanScope` themselves are untouched.
- `app/application/scanning/trigger_scan.py` (new) --
  `TriggerScanUseCase`: creates a `Scan` (`QUEUED`) plus all eight
  `ScanWorkflowStep` rows (`PENDING`) in the locked order. Does not
  validate the target itself -- that's the pipeline's own first step.
- `app/application/scanning/normalization.py` (new) --
  `normalize_scan_output()`/`NormalizedFinding`: turns nuclei's raw
  JSONL into structured (but not yet validated) fields -- title, host,
  matched-at, raw severity string, CVE IDs, CVSS score/vector
  candidates, full raw match object. Nuclei-specific, in the
  application layer (not `scanner_engine/`), dispatching on
  `output_format` via a plain `if` rather than a port -- see design
  notes below.
- `app/application/scanning/run_scan_workflow.py` (new) --
  `RunScanWorkflowUseCase`, the orchestrator. `execute(scan_id)` walks
  all eight steps in the locked order, updating each `ScanWorkflowStep`
  row and setting `Scan.status` via `derive_scan_status` at the end.
  Wired to the Milestone 3 `ScannerPort`/`NucleiAdapter`
  (constructor-injected `ActiveScanner`) and the Milestone 2
  `ScanRepositoryPort`/`AssetRepositoryPort`/`FindingRepositoryPort`
  implementations.
- 44 new tests: `test_scan_status_derivation.py` (7),
  `test_normalization.py` (9), `test_trigger_scan.py` (4),
  `test_run_scan_workflow.py` (21, every port faked), and
  `test_scan_pipeline_orchestrator.py` (3, integration, real Postgres,
  scanner/storage faked). 100% coverage on every Milestone 4 module,
  clean Ruff (lint + format), clean MyPy strict.
- `app/application/scanning/__init__.py`: docstring corrected from
  "Not yet implemented" to describe the three files now present.

## Design decisions made this session (all recorded in PROJECT_STATE.md
section 3's dated log, not just here)

1. **Scan status derivation** lives as a pure function
   (`derive_scan_status`) in the domain layer, not a method on `Scan` --
   `Scan` doesn't hold a live reference to its own steps, so a function
   taking them as an argument is the correct shape. This also let me
   catch and fix a stale cross-reference: PROJECT_STATE.md section 5's
   `Scan` row said this derivation logic was "Milestone 3" -- checked
   against Milestone 3's actual delivered scope (Nuclei
   adapter/StoragePort/target validation only, confirmed via
   implementation_progress.md's own Milestone 3 write-up) and found
   that attribution was never true. Corrected to "Milestone 4."

2. **Resumability, as actually implemented, is narrower than "resume any
   step" and I want that stated plainly rather than overclaimed.**
   `EXECUTE_SCANNER` is the only step this codebase treats as
   expensive/non-idempotent -- once `COMPLETED`, the real scanner is
   never invoked again for that scan; raw output is read back from
   `StoragePort` under a deterministic `{scan_id}/raw-output` key
   instead (no new column needed). Every other step
   (normalize/deduplicate/correlate/enrich/persist) is simply
   recomputed on every invocation, safe because each is either a pure
   read or guarded against duplicate writes by checking for an existing
   `(scan_id, asset_id)` observation or `(scan_id, finding_id)`
   occurrence before inserting. This gives genuine "retry a scan after
   a transient failure" behavior without solving general concurrent-
   safety -- flagged explicitly as Technical debt item 8 (two different
   scans hitting the same new Asset's unique constraint concurrently
   isn't handled; two sequential attempts at the same scan are handled
   correctly). Deciding how much resumability to actually build, versus
   just letting `scan_workflow_steps` exist for observability alone,
   was the single biggest design judgment call this session -- writing
   it down precisely, rather than either overclaiming full crash-safety
   or underselling it as "just an audit log," seemed important enough
   to flag here explicitly.

3. **`AI_ANALYZE` is unconditionally `SKIPPED`, not stubbed, not faked.**
   `AIProviderPort`/`AnalysisService` are Milestone 6 scope and genuinely
   do not exist. `WorkflowStepStatus.SKIPPED` already exists in the
   domain model precisely for a legitimate non-execution, so using it
   here isn't inventing new domain concepts to route around a gap --
   it's using the state machine as designed. A `SKIPPED` step still
   counts as "done" for `derive_scan_status`, so scans complete today
   without AI commentary.

4. **No event-bus publication, no scanner registry -- both explicitly
   out of scope, not overlooked.** `EventBusPort` doesn't exist yet
   (PROJECT_STATE.md section 4 already lists it "pending"), so no
   `FindingCreated` event is published after persist even though
   section 3 named that as planned. `RunScanWorkflowUseCase` is
   constructed with exactly one `ActiveScanner` and raises
   `ScannerMismatchError` on a name mismatch rather than building a
   multi-adapter registry -- only one adapter (`NucleiAdapter`) exists,
   and PROJECT_STATE.md section 3 already applies this same "don't
   build it until a second real shape exists" reasoning to deferring
   `BaseAgent`.

5. **CVSS/severity provenance: the one non-obvious catch this session.**
   While implementing `enrich`, I noticed `Finding.ai_severity_level`'s
   name specifically means an AI provider's own estimate (Milestone 6),
   not a scanner's self-reported severity claim. It would have been easy
   to default a finding's severity from nuclei's own `info.severity`
   string when no CVSS was present, and it would have made every finding
   have a non-null `effective_severity` -- but doing so would silently
   mislabel scanner data as AI-derived data. `enrich` does not do this.
   Nuclei's raw severity claim is not discarded, though -- it travels in
   `FindingOccurrence.raw_evidence` (the full raw match object) --  just
   not asserted as authoritative by this milestone. A finding with
   neither valid CVSS nor an AI estimate legitimately has no
   `effective_severity` yet, and that's correct, not a gap.

6. **Normalization is a plain function, not a `NormalizerPort`.**
   `ScanOutput`'s own docstring (Milestone 3) already commits Scanning
   to stop at "here is exactly what the scanner said" and defer
   interpretation to "the normalize pipeline step (Milestone 4)" -- so
   this had to live in the application layer, not `scanner_engine/`.
   Dispatching on `output_format` via a port with exactly one
   implementation would be an untested abstraction (same YAGNI reasoning
   PROJECT_STATE.md section 3 already applies elsewhere); revisit when
   Phase 4 adds a second scanner output format.

## Verification method this session
Given `RunScanWorkflowUseCase` genuinely depends on essentially the
whole backend -- domain entities, config, all six ORM model modules, all
five repository implementations from Milestones 1-2, plus
`ScannerPort`/`StoragePort`/`target_validation`/`NucleiAdapter` from
Milestone 3 -- the sandbox reconstruction this session was the most
extensive yet. Every one of those files was read verbatim from
`C:\Users\gamer\Downloads\claudeOnly` through the Filesystem MCP this
session (not from memory) and reconstructed file-by-file in the sandbox,
including installing PostgreSQL 16 there and applying the real initial
migration, before any Milestone 4 code was written.

Stated plainly, per the verification-honesty rule: this is not a renewed
byte-for-byte re-verification of the Milestone 1-3 test suites
themselves -- those test files were not copied into the sandbox this
session, only their source modules were, for import purposes. Milestone
1-3's own "105 unit passed"/"38 integration passed" results are
unchanged from their own sessions and were not re-confirmed here. What
this session's own integration test (`test_scan_pipeline_orchestrator.py`,
3 tests) newly confirms is that the real, unmodified Milestone 2
repository implementations (`SqlAlchemyScanRepository`,
`SqlAlchemyAssetRepository`, `SqlAlchemyFindingRepository`,
`SqlAlchemyOrganizationRepository`) still behave correctly end-to-end --
create, natural-key lookup, RLS-scoped session usage via
`session_scoped_to_org` -- when driven by this milestone's new
orchestrator against a real Postgres 16 instance. The scanner and object
storage were faked in both the unit and integration suites -- no real
`nuclei` binary or MinIO server was available in this environment,
the same constraint already documented for Milestone 3.

In the sandbox:

    cd backend && pip install -e ".[dev]"
    export TEST_DATABASE_URL=postgresql+asyncpg://app_user:app_password@localhost/security_platform_test
    alembic upgrade head          # against both security_platform and security_platform_test
    pytest tests/ -v              # 44 passed (41 unit + 3 integration)
    ruff check .                  # All checks passed!
    ruff format --check .         # all files already formatted
    mypy app/application/scanning app/domain/scanning/entities.py   # Success: no issues found

Per-module coverage confirmed 100% on `normalization.py`,
`trigger_scan.py`, `run_scan_workflow.py`, and `entities.py`'s new
additions (the one pre-existing line left uncovered in `entities.py`,
`Scan.is_deleted`, is Milestone-2 code the original `test_domain_entities.py`
already covers in the real repository -- it shows as uncovered here only
because that test file wasn't part of this session's sandbox
reconstruction, not because of anything this session changed).

Every new file, and the one modified file, was then written into the
real repository via the Filesystem MCP and spot-checked by byte count
against the sandbox-verified source -- for every new file this time, not
just one:
- `app/application/scanning/normalization.py`: 5508 bytes, both sides.
- `app/application/scanning/trigger_scan.py`: 2166 bytes, both sides.
- `app/application/scanning/run_scan_workflow.py`: 22052 bytes, both
  sides.
- `tests/unit/test_run_scan_workflow.py`: 25678 bytes, both sides.
- `tests/integration/test_scan_pipeline_orchestrator.py`: 9979 bytes,
  both sides.
- `app/domain/scanning/entities.py`: applied via `edit_file` against the
  real file's own exact original text (not the sandbox reconstruction's
  paraphrase of one pre-existing docstring paragraph), so its resulting
  byte count (5094) differs by 11 bytes from the sandbox copy (5083) --
  expected, and traced to that one paraphrased sentence, not to any
  incorrect content in the applied edit.

As with every previous milestone, execution ran in Claude's own sandbox,
not on `C:\Users\gamer\Downloads\claudeOnly` directly -- this connector
still has no command-execution tool (unchanged since Milestone 1;
PROJECT_STATE.md section 13).

## Current implementation status
All eight Milestone 4 files (5 new source files, 4 new/1 modified test
files, plus one docstring-only `__init__.py` update and the surgical
`entities.py` edit) are now present on disk at
`C:\Users\gamer\Downloads\claudeOnly`, matching the sandbox-verified
content exactly (see byte counts above).

## Pending work
- Milestone 5: API layer (public + internal split) -- the next
  unfinished milestone. Not started this session, per the
  one-milestone-per-session rule.
- Real-server integration tests for `MinioStoragePort` (against a real
  MinIO instance) and `NucleiAdapter` (against a real `nuclei` binary) --
  unchanged from Milestone 3's own pending list; no such environment was
  available this session either.
- The eight `docs/*.md` files (architecture, roadmap, decisions,
  database, api, coding_standards, testing_strategy, security_model)
  still not written as standalone files. PROJECT_STATE.md remains the
  interim substitute.
- Technical debt item #5 (repository `update()`/`soft_delete()` fetch
  unfiltered by `deleted_at`) remains open -- confirmed this session it
  is still not triggered by anything Milestone 4 added (see
  implementation_progress.md's updated entry).
- New Technical debt item #8 this session: `RunScanWorkflowUseCase`'s
  correlate/persist idempotency guards handle sequential retries of the
  same scan correctly but not two different scans running truly
  concurrently against overlapping targets (a real, if currently
  unreachable, race condition -- nothing runs scans concurrently yet;
  no Celery/worker wiring exists before Milestone 7). See
  implementation_progress.md's Technical debt section for the full
  account and the likely fix shape when it becomes relevant.
- The user's Project may have attached copies of PROJECT_STATE.md /
  implementation_progress.md that are now stale relative to disk again.
  Re-syncing those attachments is the user's call, not something to do
  unilaterally.

## Next immediate task
Wait for explicit approval, then begin Milestone 5 (API layer -- public
+ internal split).

## Session notes
- Unlike Milestone 3's session, this one matched its own expectations
  going in: the repository genuinely had not started Milestone 4, so
  this was a normal implement -> verify -> document session, not a
  discovery-and-audit one.
- The single most consequential judgment call this session was scoping
  resumability honestly (see design decision #2 above) rather than
  either (a) building full exactly-once/concurrency-safe semantics,
  which would have meaningfully exceeded "the processing pipeline
  orchestrator" as this milestone's stated scope, or (b) quietly
  building something that only looks resumable without actually
  verifying which retry scenarios it covers. Landed on: genuinely
  correct for sequential retry of one scan (tested directly, including
  the two branches where a later-step failure must not re-invoke the
  scanner or re-enter `AI_ANALYZE`), explicitly flagged as not yet safe
  for concurrent scans (Technical debt #8), because that's what the
  evidence actually supports claiming.
- No Milestone 5+ functionality was implemented or scaffolded this
  session. `app/api/v1/`, `app/main.py`, `AIProviderPort`, `EventBusPort`,
  and a scanner registry were all considered during design and
  deliberately left untouched -- see design decisions above for why each
  one doesn't yet exist, rather than silently building toward them.
