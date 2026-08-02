# PROJECT_STATE.md

The permanent memory of this project. Read this file first in any new
session, before reading chat history or asking the user to re-explain
anything already decided here. Update it whenever a decision changes --
it should always reflect the *current* state, not the history of how it
got there (that history lives in the Design decisions section as a dated
log, but the rest of this file describes "how things are," not "how they
became that way").

Companion files: `docs/implementation_progress.md` (cumulative build
status across sessions), `docs/session_state.md` (most recent session
only, overwritten each time).

---

## 1. Approved architecture summary

Clean Architecture, dependency rule enforced: `domain` has zero framework
or database imports; `application` holds use cases and defines *ports*
(interfaces) that outer layers implement; `infrastructure`,
`scanner_engine`, and `ai_agents` are outer-layer adapters implementing
those ports. `scanner_engine` and `ai_agents` are first-class top-level
modules, not buried inside `infrastructure/`, because they are this
product's core extensibility points.

Bounded contexts (Domain-Driven Design):

| Boundary | Owns | Reads from | Does not own |
|---|---|---|---|
| Identity & Access | orgs, users, roles, permissions, memberships, audit logs | -- | findings, scans |
| Asset Intelligence | assets, asset observations, dedup/confidence, relationships | org_id from Identity | findings |
| Scanning | scans, scan_scopes, workflow steps, adapter invocation | Identity, Asset Intelligence | findings |
| Findings & Analysis | findings, finding_occurrences, finding_analyses, triage state machine | scan_id, asset_id | AI provider calls themselves |
| AI Platform | agent orchestration, provider routing | Findings data via injected context | any entity -- a capability, not a domain |
| Reporting | reports | Findings, Scanning | findings themselves |

This is a modular monolith, not microservices -- deliberate, for personal
use and even early commercial use. Boundaries are enforced by import
discipline, not network calls. Scanning is the most likely candidate to
split into its own service later, precisely because the boundary is
already clean.

Event-driven processing: a lightweight in-process/Celery-backed domain
event dispatcher, not a new message broker (Kafka etc. would be
infrastructure the personal-use/Milestone-1 scale does not need).
Subscriptions are registered explicitly at startup in one file that reads
like a wiring diagram, not via decorator magic scattered across modules --
traceability ("what happens when a finding is created") wins over
convenience.

Ports defined so far: `ScannerPort` (ActiveScanner/ImportScanner split --
not every scanner is invokable; Burp/ZAP manual exports have no execute()
step), `AIProviderPort`, `EventBusPort`, `StoragePort`, repository ports
per bounded context.

## 2. Technology stack

| Layer | Choice |
|---|---|
| Frontend | Next.js, TypeScript, TailwindCSS, shadcn/ui (Phase 3, not started) |
| Backend | FastAPI, Python 3.12, SQLAlchemy (async), Alembic |
| Database | PostgreSQL with Row-Level Security |
| Cache / queue | Redis, Celery |
| Object storage | MinIO, behind StoragePort |
| Vector database | Qdrant (running from day one per requirement; unused until Phase 5 RAG) |
| AI providers | Anthropic, OpenAI, Ollama, OpenRouter, behind AIProviderPort |
| Auth | JWT (httpOnly cookies) + OAuth2 (OAuth2 deferred past Milestone 1) |
| IDs | ULID, generated in app code, stored as native Postgres UUID |
| Testing | pytest, pytest-cov, Ruff, MyPy (strict) |
| Containers | Docker Compose, with network segmentation (see Design decisions) |

## 3. Design decisions

Dated log, most consequential first within each session. Do not
relitigate these without a genuine implementation blocker -- see Rules.

- RLS for tenant isolation from day one, even in single-org personal use.
  Retrofitting tenant isolation into a system not built for it is how
  cross-tenant leaks happen; low current stakes make now the safe time to
  validate it. RLS policy folded together with soft-delete filtering:
  `USING (org_id = current_setting('app.current_org_id') AND deleted_at IS NULL)`.
- Scanner adapter interface split into `ActiveScanner` (has `execute()`)
  and `ImportScanner` (does not) -- Burp/ZAP manual exports have no "run"
  step; forcing one interface would fake an execute() on import-only tools.
- Event bus: lightweight dispatcher over existing Celery/Redis, not a
  dedicated broker. Accepted trade-off: no replay/backfill if a future
  consumer needs history that predates it existing -- would need a one-off
  backfill script, not built speculatively.
- `finding_analyses` is append-only (never overwritten), now with
  `prompt_version`, `kb_version` (nullable -- no RAG until Phase 5), and
  `model_metadata`, to support re-analysis history and "what changed."
- OAuth2 deferred past Milestone 1 -- schema (`oauth_accounts`) exists,
  flows do not, since personal use does not need multi-user login yet.
- `scan_scopes` table exists now (structure), target-ownership enforcement
  deferred (code) -- the concept costs one table today; retrofitting
  authorization gating into every scan-trigger path later would cost more.
- Scanner execution isolation: code-level protections now (argument-list
  subprocess calls, never shell interpolation; enforced timeouts;
  non-root; reject RFC1918/loopback/link-local/169.254.169.254 targets by
  default). Network-level isolation designed via Docker Compose network
  segmentation: a `scan-egress` network (scanner-worker only, has internet
  access) separate from `internal` (Postgres, API, ingestion worker -- no
  internet route), bridged only by Redis/MinIO on a shared `queue`
  network. True OS-level sandboxing (gVisor, Firecracker) explicitly
  deferred to Phase 9 -- Docker Compose alone cannot fully solve
  SSRF/egress control, and this is stated rather than implied to be solved.
- No graph database -- `asset_relationships` (many-to-many, typed) and
  `parent_asset_id` (simple hierarchy) cover relationship modeling in
  plain Postgres at current scale.
- ULIDs adopted, generated in app code (`domain/shared/ids.py`), stored in
  native `UUID` columns. No compatibility cost found: same 16 bytes, same
  index type; buys time-ordered inserts (helps B-tree index locality on
  high-insert tables) and natural sortability by creation time.
- `StrEnum` over free-text status/type columns, mapped via
  `native_enum=False` (VARCHAR + CHECK), not native Postgres `ENUM` --
  avoids `ALTER TYPE` migration pain as the value set evolves.
- Soft delete (`deleted_at`) on current-state tables (orgs, users, assets,
  findings, scans, reports) only -- explicitly not on append-only logs
  (`audit_logs`, `finding_status_history`, `asset_observations`,
  `finding_analyses`) or tables with existing lifecycle fields
  (`organization_members.status`, `refresh_tokens.revoked_at`).
- **Findings deduplication redesign** (discovered during implementation,
  not part of the original approved design): a fingerprint column alone
  let you *query* for recurrence but did not stop the same vulnerability
  creating a fresh row every scan. Fix: `findings` keyed by
  `(org_id, fingerprint)`; a recurrence updates the existing row's
  `last_seen_at` instead of inserting a new one. `finding_occurrences`
  (mirrors `asset_observations`) keeps full per-scan history underneath.
- Asset Intelligence: `assets` is a current-state cache (identity =
  `(org_id, asset_type, normalized value)`); `asset_observations` is the
  append-only history (every scan's sighting, with a metadata snapshot).
  `Asset.metadata`/`confidence` are a materialized view of the latest
  observation -- small write-time duplication for cheap reads on the
  common case. Confidence rule deliberately simple: most recent
  observation's confidence, not a weighted blend -- no data yet to justify
  anything fancier. No direct `finding -> asset_observation` foreign key:
  derivable via shared `scan_id` + `asset_id`.
- `domain/shared/enums.py` is a deliberate temporary staging area for
  enums whose owning bounded context (`scanning/`, `assets/`, `identity/`,
  `reporting/`) is not built yet. Move each enum into its owning module's
  own `enums.py` as that context gets implemented.
- AI: `AIProviderPort` built now (four named providers already in scope
  justifies the interface). `BaseAgent` deliberately *not* built --
  YAGNI. One concrete `AnalysisService` exists with no formal interface;
  extract `BaseAgent` when a second agent's real shape is known, not
  before.
- Scanner capability registry: code-level (adapter name -> capability
  metadata: active/import, output formats, asset types produced, egress
  required), not a database table -- describes what is deployed, not
  tenant data. Per-org enablement on top of it is a Phase 6 extension.
- Feature flags / system settings: `system_settings` (key/value) and
  `feature_flags` (key + nullable org override), Redis-cached since
  read-heavy and write-rare.
- Public/internal API split: two router modules (`/api/v1` public,
  `/internal` health/metrics/admin) in code now; restricting `/internal`
  at the network level is a Phase 10 concern.
- Processing pipeline adopted verbatim: `validate_target -> execute_scanner
  -> normalize -> deduplicate -> correlate -> enrich -> ai_analyze ->
  persist`, implemented as explicit `scan_workflow_steps` rows (status,
  retry count, timestamps per step) rather than one monolithic Celery
  task -- gives resumability (retry from the failed step, not from
  scratch) and near-free per-step timing metrics. AI Analysis is a direct
  call in this sequence, not event-triggered: with one analysis service,
  there is no second subscriber yet to justify decoupling it.
  `FindingCreated` still publishes after persist, for future consumers.
  No per-observation `AssetObserved` event -- too high-frequency, no
  current consumer; a future consumer queries `asset_observations WHERE
  scan_id = X` when it receives `ScanCompleted` instead. General rule for
  what becomes an event: publish when >= 2 independent things need to
  react, or a near-term low-frequency consumer is already known.
- `utcnow()` is a plain function, not an injectable `Clock` class -- YAGNI
  until something needs deterministic time in a test.
- `DomainEvent` base is not `slots=True` (unlike the value objects) --
  meant to be subclassed, and dataclass inheritance with `__slots__` has
  enough sharp edges that the small memory saving is not worth it for
  something created at "one per finding," not "millions per second."
- **RLS + soft-delete redesign** (discovered during Milestone 2
  implementation, not part of the original approved design): the
  original predicate -- `USING (org_id = current_setting('app.
  current_org_id') AND deleted_at IS NULL)` -- is unimplementable as a
  single policy. PostgreSQL checks a table's `SELECT`-relevant `USING`
  clause against the *new* row on every `UPDATE`, in addition to any
  `WITH CHECK` given (confirmed against real Postgres 16, and against
  the pgsql-hackers thread "Bug: RLS policy FOR SELECT is used to check
  new rows," Oct 2023, which describes this as long-standing, not a
  version-specific bug). With `deleted_at IS NULL` inside the policy,
  the very `UPDATE` that performs a soft delete produces a row that
  fails its own table's read policy -- Postgres rejects it with "new row
  violates row-level security policy," and no per-command policy split
  avoids this, since the `SELECT`-side check on the new row is
  unconditional. Fix: RLS now enforces tenant isolation only
  (`organization_id`, or `id` for `organizations` itself) --
  `deleted_at` filtering moved to explicit `WHERE deleted_at IS NULL` in
  the repository read methods for the five soft-delete tables
  (`get_by_id`, `get_by_slug`/`get_by_email`/`get_by_identity`/
  `get_by_fingerprint`, and `Report.list_by_scan`), the same way every
  other query-level filter in this codebase is expressed. Tenant
  isolation itself is unaffected -- still fully enforced by the
  database, not by application code remembering a WHERE clause; only the
  soft-delete display concern moved, and it never depended on RLS in the
  first place (`users` has always been soft-deleted with no RLS policy
  at all, since it has no `organization_id` column). Full account in the
  DESIGN NOTE at the top of the initial-schema Alembic migration.
- Every `datetime` column requires an explicit `DateTime(timezone=True)`
  in its `mapped_column(...)` call (discovered during Milestone 2): a
  bare `Mapped[datetime]` with no explicit column type silently produces
  a timezone-*naive* `TIMESTAMP` column, and asyncpg then rejects
  binding the timezone-aware values this codebase's `utcnow()` produces
  ("can't subtract offset-naive and offset-aware datetimes"). Every
  model in `app/infrastructure/db/models/` uses `DateTime(timezone=True)`
  explicitly for this reason, including columns outside the
  `TimestampMixin`/`SoftDeleteMixin` pair (which already did this
  correctly from the start).
- **Scan status derivation** (Milestone 4, filling the gap the Scan
  domain-model row in section 5 always flagged as deferred):
  `derive_scan_status(steps)` in `app/domain/scanning/entities.py` is a
  pure function, not a method on `Scan` -- `Scan` does not hold a live
  reference to its own `ScanWorkflowStep` rows, so a function taking
  them as an explicit argument is the correct shape. Rule: any step
  `FAILED` -> `FAILED`; every step `COMPLETED` or `SKIPPED` -> `COMPLETED`;
  otherwise `RUNNING`; no steps at all -> `QUEUED` (defensive only).
- **Processing pipeline orchestrator resumability, as actually
  implemented** (Milestone 4): the eight pipeline steps split into two
  groups. `EXECUTE_SCANNER` is the one step this codebase treats as
  expensive and non-idempotent -- once its `ScanWorkflowStep` row is
  `COMPLETED`, `RunScanWorkflowUseCase` never invokes the real scanner
  again for that scan; it reads the already-durably-stored raw output
  back from `StoragePort` instead (stored under a deterministic
  `{scan_id}/raw-output` key, so no new column was needed to remember
  where it went). Every other step (`normalize`, `deduplicate`,
  `correlate`, `enrich`, `persist`) is treated as safe to recompute on
  every invocation, because each is either a pure read (normalize,
  deduplicate, enrich) or is guarded against duplicate writes on a
  retried run: `correlate` checks for an existing `(scan_id, asset_id)`
  observation before calling `add_observation`, and `persist` checks for
  an existing `(scan_id, finding_id)` occurrence before calling
  `add_occurrence`, using each repository's own natural-key lookups
  (`get_by_identity`, `get_by_fingerprint`) to avoid re-creating an
  `Asset` or `Finding` that a prior attempt already created. This gives
  genuine resumability for the realistic case (a transient failure,
  fixed, then the same scan retried) without solving fully general
  exactly-once delivery -- see this milestone's technical debt entry in
  `docs/implementation_progress.md` for the one edge case this does not
  cover.
- **`AI_ANALYZE` is unconditionally `SKIPPED` in Milestone 4, not
  event-published, not backed by a scanner registry** -- three related,
  deliberate scope boundaries, not oversights: (1) `AIProviderPort` and
  an `AnalysisService` do not exist yet (Milestone 6), so
  `RunScanWorkflowUseCase` marks this step `SKIPPED` -- a real,
  already-modeled terminal state (PROJECT_STATE.md section 5), not a
  fabricated result -- rather than guessing at AI behavior; a `SKIPPED`
  step still counts as "done" for `derive_scan_status`, so scans
  complete today without AI commentary. (2) No `FindingCreated`
  event is published after persist, even though this file already named
  that as planned ("still publishes after persist, for future
  consumers") -- `EventBusPort` itself does not exist yet (section 4's
  folder structure), and this milestone does not invent one purely to
  satisfy that forward reference. (3) `RunScanWorkflowUseCase` is
  constructed with exactly one `ActiveScanner` instance and raises
  `ScannerMismatchError` if a `Scan`'s `scanner_name` does not match it,
  rather than building a multi-adapter registry for a codebase that
  still has exactly one adapter (`NucleiAdapter`) -- the same
  "don't build it until a second real shape exists" reasoning this file
  already applies to deferring `BaseAgent`.
- **CVSS/severity provenance kept strictly separate from scanner-native
  severity claims** (Milestone 4, discovered while implementing the
  `enrich` step -- a real design consideration, not a bug fix):
  `Finding.ai_severity_level` is named for what it is, an AI provider's
  own estimate (Milestone 6) -- `enrich` never writes a scanner's raw,
  self-reported severity string (e.g. nuclei's own `info.severity`) into
  that field, since doing so would misrepresent the data's provenance.
  A finding with no valid CVSS and no AI estimate yet legitimately has
  no `effective_severity` -- that is correct today, not a gap to patch
  around. The scanner's raw severity claim is not discarded, though: it
  travels in `FindingOccurrence.raw_evidence` (the full raw scanner match
  object), available to a human or a future AI process, just not
  asserted as authoritative by this milestone.
- **Normalization is nuclei-specific application-layer code, not a
  `NormalizerPort`** (Milestone 4, `app/application/scanning/
  normalization.py`): `ScanOutput`'s own docstring already commits
  Scanning to stop at "here is exactly what the scanner said" and defer
  interpretation to "the normalize pipeline step (Milestone 4)" -- so
  turning nuclei's raw JSONL into structured data, including deciding
  what a CVSS candidate or a severity string even means, could not live
  in `scanner_engine/` without Scanning starting to own Findings
  concepts. It lives in the application layer instead, dispatching on
  `output_format` via a plain `if`, not an injectable port -- with
  exactly one real output format wired (Milestone 3), a port with one
  implementation would be an untested abstraction, revisited when Phase
  4 adds a second scanner output format.

## 4. Folder structure

```
<project root>/
├── PROJECT_STATE.md
├── README.md, docker-compose.yml, .env.example,
│   AI_ENGINEERING_RULES.md, LICENSE   (root placeholders)
├── .gitignore
├── docs/
│   ├── implementation_progress.md, session_state.md
│   └── architecture.md, roadmap.md, decisions.md, database.md, api.md,
│       coding_standards.md, testing_strategy.md, security_model.md
│       (pending -- described in chat history, not yet written as files;
│        this document is the interim consolidated substitute)
├── backend/
│   ├── pyproject.toml, Dockerfile (Dockerfile pending)
│   ├── alembic.ini, alembic/env.py, alembic/script.py.mako,
│   │   alembic/versions/ (initial schema migration, with RLS policies)
│   ├── app/
│   │   ├── main.py (pending), config.py
│   │   ├── domain/
│   │   │   ├── shared/        (ids, clock, fingerprint, events, enums -- staging, see decisions)
│   │   │   ├── findings/      (value_objects.py, entities.py -- done)
│   │   │   ├── scanning/, assets/, identity/, reporting/   (entities.py done; behavior/state machines pending)
│   │   ├── application/
│   │   │   ├── interfaces/    (repository ports for all 5 bounded contexts -- done; ScannerPort/StoragePort done (Milestone 3); AIProviderPort/EventBusPort pending)
│   │   │   ├── scanning/      (done -- trigger_scan.py, run_scan_workflow.py, normalization.py, Milestone 4)
│   │   │   ├── identity/, assets/, findings/, reporting/  (use cases -- scaffolded, empty)
│   │   ├── infrastructure/
│   │   │   ├── db/{base.py, session.py, models/, repositories/}  (done -- all 19 tables, all 5 repositories)
│   │   │   ├── storage/  (done -- MinioStoragePort, Milestone 3)
│   │   │   ├── security/  (done -- target_validation.py, Milestone 3)
│   │   │   ├── ai_providers/, vector_store/, event_bus/,
│   │   │   │   observability/  (all scaffolded, empty)
│   │   ├── scanner_engine/
│   │   │   ├── base_scanner.py  (done -- run_scanner_subprocess, Milestone 3)
│   │   │   └── adapters/{nuclei,nmap,burp,zap,reconx,bughunter,sqlmap}/
│   │   │       (nuclei/ done -- NucleiAdapter, Milestone 3; the rest
│   │   │        remain scaffolded, empty Phase 4 stubs)
│   │   ├── ai_agents/   (scaffolded, empty; analysis_service.py pending)
│   │   ├── api/v1/      (scaffolded, empty)
│   │   └── workers/     (scaffolded, empty)
│   └── tests/
│       ├── conftest.py  (async Postgres fixtures -- rewritten in Milestone 2; see docs/implementation_progress.md for the discovered stray-file note)
│       ├── unit/    (config, ids, clock, fingerprint, events, value_objects,
│       │         domain_entities, base_scanner, target_validation,
│       │         minio_storage, nuclei_adapter, scan_status_derivation,
│       │         normalization, trigger_scan, run_scan_workflow -- all
│       │         fifteen done)
│       └── integration/  (support.py + repository/session integration suite,
│                 plus test_scan_pipeline_orchestrator.py (Milestone 4),
│                 all against real Postgres)
└── frontend/   (Phase 3, not started)
```

## 5. Domain model summary

| Entity | Lives in | Key invariant / behavior |
|---|---|---|
| Organization / Membership | identity/ | >= 1 Owner always; cannot remove the last one (rule not yet enforced -- see below) |
| Finding | findings/ | state machine `new -> triaged -> {confirmed, false_positive} -> {fixed, accepted_risk, wont_fix}`; `effective_severity` prefers CVSS over an AI estimate -- **implemented, tested** |
| Severity (value object) | findings/ | ordered: info < low < medium < high < critical -- **implemented, tested** |
| CVSS (value object) | findings/ | validates 0.0-10.0 + vector format; derives band from score -- **implemented, tested** |
| Asset | assets/ | current-state cache; identity = `(org_id, asset_type, normalized value)` -- **implemented, tested** |
| AssetObservation | assets/ | append-only; recording one updates the Asset cache -- **implemented, tested** |
| Scan | scanning/ | state machine `queued -> running -> {completed, failed, cancelled}`, derived from its workflow steps -- **implemented, tested** (derivation logic itself is Milestone 4; this row previously said "Milestone 3" -- a stale reference caught and corrected during Milestone 4, since Milestone 3's actual delivered scope, per section 6/7 below, never touched Scan status derivation) |
| WorkflowStep | scanning/ | `pending -> running -> {completed, failed, skipped}`, independently retryable -- **implemented, tested** |

As of this update, all entities listed above are implemented as plain
data-carrying dataclasses (Milestone 2) -- see
`app/domain/*/entities.py`. "Implemented, tested" here means the entity
shape and any already-decided computed properties (e.g.
`Finding.effective_severity`); it does **not** mean the state-machine
transition rules or the aggregate-level `>= 1 Owner` invariant are
enforced yet -- those require application-layer use cases (repository
ports and their SQLAlchemy implementations exist as of Milestone 2;
the use cases that call them do not, and are future-milestone work, per
the scope note in each `entities.py`).

## 6. Current implementation status

Milestone 1 (Foundation) is complete: all 16 expected files exist,
content audited (imports traced, naming/domain-purity/documentation
checked), and dynamically executed -- pytest, Ruff (lint + format), and
MyPy strict all run and passing against these exact file contents.

Milestone 2 (persistence layer) is complete as of this session: SQLAlchemy
async ORM models for all 19 tables across all six bounded contexts, plain
domain entities for all five bounded contexts, repository ports
(`*RepositoryPort` ABCs) and their SQLAlchemy-backed implementations for
all five aggregates, and a hand-written initial Alembic migration
including Row-Level Security policies. 102 tests (55 unit carried over
from Milestone 1 unchanged, 9 new unit tests for domain entities, 38
integration tests against a real PostgreSQL 16 instance), 100% coverage,
clean Ruff (lint + format), clean MyPy strict. The migration was applied
to a real Postgres instance, exercised by the full integration suite,
downgraded to nothing, and re-upgraded cleanly (`alembic downgrade base`
then `alembic upgrade head`), proving reversibility rather than assuming
it. RLS tenant isolation was proven both by raw SQL (two sessions scoped
to different orgs, each seeing only its own row) and by the automated
`test_rls_isolates_*_between_tenants` tests. Two genuine implementation
gaps were discovered and fixed during this milestone -- see section 3's
dated entries ("RLS + soft-delete redesign" and the `DateTime(timezone=
True)` note) for the full account of each, since both count as design
gaps under section 12's workflow rule, not ordinary bugs.

As with Milestone 1's verification, this execution ran in Claude's own
sandboxed environment (with a local PostgreSQL 16 instance installed for
the purpose), not on `C:\Users\gamer\Downloads\claudeOnly` directly --
this Filesystem MCP still has no command-execution tool (see section 13).
Every file was then written to this repository via the Filesystem MCP
file-by-file, matching the sandbox-verified content exactly (spot-checked
by byte count on at least one large file, the initial migration). Local
re-verification on the actual machine remains a one-time manual step if
bit-for-bit confirmation there matters -- see the self-verification
command in `docs/implementation_progress.md`.

Milestone 3 (Scanner engine -- Nuclei adapter -- + StoragePort + target
validation) is complete. Process note, recorded per section 12's
verification-honesty rule: the implementation -- `ScannerPort`
(`ActiveScanner`/`ImportScanner` split), `StoragePort`,
`run_scanner_subprocess` (argument-list-only, enforced timeout, non-root
guard), `validate_target` (DNS-rebinding-aware SSRF/private/loopback/
link-local/cloud-metadata guard), `MinioStoragePort` (official
synchronous `minio` SDK wrapped in `asyncio.to_thread`), and
`NucleiAdapter` -- was already present on disk, complete and correct, at
the start of the session that closed this milestone out, despite this
file and `docs/implementation_progress.md` both stating Milestone 3 as
"not started" going into that session. That session's actual work was:
discovering the discrepancy by inspecting the repository before writing
anything (per this file's own instruction that the repository is ground
truth over documents that may lag), auditing the existing code against
every relevant locked decision in section 3, dynamically verifying it in
a sandbox for the first time (105 unit tests total, 100% coverage on
every Milestone 3 module, clean Ruff, clean MyPy strict on all 74 source
files), fixing one stale docstring (`scanner_engine/adapters/__init__.py`
still read "Not yet implemented"), and this documentation update -- not
fresh implementation, since none was needed. Full account in
`docs/session_state.md`. The Postgres integration suite (38 tests) was
not re-executed in that session, since Milestone 3 touched no
persistence-layer code; `MinioStoragePort` and `NucleiAdapter` are
currently verified only against unit-level mocks of their external
dependency (no real MinIO server or `nuclei` binary was reachable in
that environment) -- see `docs/implementation_progress.md`'s Technical
debt list.

Milestone 4 (Processing pipeline orchestrator) is complete. Delivered:
`derive_scan_status()` and the `PIPELINE_STEP_ORDER` constant, added to
`app/domain/scanning/entities.py` (domain layer -- pure, zero framework
imports); `TriggerScanUseCase` (`app/application/scanning/
trigger_scan.py` -- creates a `Scan` plus its eight `ScanWorkflowStep`
rows, all `PENDING`, in the locked order); `normalize_scan_output()` and
`NormalizedFinding` (`app/application/scanning/normalization.py` -- the
nuclei-JSONL-specific normalize step, see section 3's dated entry for
why this is a plain function rather than a port); and
`RunScanWorkflowUseCase` (`app/application/scanning/
run_scan_workflow.py` -- the orchestrator itself, walking all eight
steps, wired to the Milestone 3 `ScannerPort`/`NucleiAdapter` and the
Milestone 2 repository layer exactly as section 15 called for). 44 new
tests (41 unit across four files, 3 integration against real Postgres),
100% coverage on every Milestone 4 module, clean Ruff (lint + format),
clean MyPy strict. Two design decisions worth flagging explicitly
because they resolve a genuine tension rather than following a locked
decision verbatim -- both recorded in section 3's dated log rather than
silently resolved: `AI_ANALYZE` is unconditionally `SKIPPED` (Milestone
6 builds the `AnalysisService` this step actually needs), and a
scanner's raw self-reported severity is never written into
`Finding.ai_severity_level` (a field name that specifically means an AI
provider's own estimate). One stale cross-reference was also found and
corrected: section 5's `Scan` row said its status-derivation logic was
"Milestone 3" -- it was never part of that milestone's actual delivered
scope (see the paragraph above), and is corrected to "Milestone 4" in
section 5 now that the function exists.

As with every previous milestone, execution ran in Claude's own sandbox,
not on `C:\Users\gamer\Downloads\claudeOnly` directly (section 13). The
sandbox reconstruction for this milestone was more extensive than usual
-- essentially the full backend, since `RunScanWorkflowUseCase` legitimately
depends on the domain entities, config, all six ORM model modules, and
all five repository implementations from Milestones 1-2 plus the
ScannerPort/StoragePort/target_validation/NucleiAdapter surface from
Milestone 3 -- reconstructed from this repository's own verbatim file
contents read through the Filesystem MCP, not from memory. Stated
plainly per the verification-honesty rule: this session did not re-run
the original Milestone 1-3 test files verbatim (they were not copied
into the sandbox; only their source modules were, for import purposes),
so this is not a renewed byte-for-byte re-verification of those
milestones' own test suites, which were already verified in their own
sessions. What it does newly confirm is that the real, unmodified
Milestone 2 repository implementations (`SqlAlchemyScanRepository`,
`SqlAlchemyAssetRepository`, `SqlAlchemyFindingRepository`,
`SqlAlchemyOrganizationRepository`) still work correctly end-to-end
against a real, RLS-enabled Postgres 16 instance when driven by this
milestone's new orchestrator -- exercised by the 3 new integration
tests, not merely assumed unchanged. Every new and modified file was
then written into this repository via the Filesystem MCP, verified
byte-count-identical against the sandbox-verified source for every new
file (not just one, this time) -- see `docs/session_state.md` for the
exact counts.

Milestones 5-7 and Phases 3-10 not started. See
`docs/implementation_progress.md` for the live version of this section.

## 7. Completed work

Milestone 1: config system (role-based, fail-fast), ULID id generation,
fingerprint hashing, DomainEvent base, domain enums (staged),
Severity/CVSS value objects with full test coverage, full repository
directory scaffold (37 directories, 34 `__init__.py` placeholders),
three permanent-memory documents, and a full static audit of the above
(imports, naming, architecture-layer purity, documentation) with zero
issues found.

Milestone 2: SQLAlchemy async ORM models for all 19 tables (identity.py,
assets.py, scanning.py, findings.py, reporting.py, platform.py), plain
domain entities for all five bounded contexts (identity, assets,
scanning, findings, reporting), repository ports and SQLAlchemy-backed
implementations for all five aggregates, Alembic migration infrastructure
(alembic.ini, async env.py, initial migration with hand-written RLS
policies), and a corrected `tests/conftest.py` (see
`docs/implementation_progress.md` for why the previous version was
replaced). 102 tests total (64 unit, 38 integration), 100% coverage,
clean Ruff, clean MyPy strict -- verified in the sandbox (with a local
PostgreSQL 16 instance) and transplanted file-by-file into this
repository via the Filesystem MCP; not re-executed in this location
since, per the same caveat that has applied since Milestone 1's
completion (no execution tool available through this connector).

Milestone 3: `ScannerPort` (`ActiveScanner`/`ImportScanner` split),
`StoragePort`, `run_scanner_subprocess` (argument-list-only subprocess
execution, enforced timeout, non-root guard), `validate_target`
(DNS-rebinding-aware SSRF/private/loopback/link-local/cloud-metadata
guard), `MinioStoragePort`, and `NucleiAdapter`. 105 unit tests total
(41 new this milestone), 100% coverage on every Milestone 3 module,
clean Ruff, clean MyPy strict. See section 6 for the process note on how
this milestone was closed out -- the implementation was found already
complete on disk and was audited and dynamically verified rather than
written from scratch.

Milestone 4: `derive_scan_status()`/`PIPELINE_STEP_ORDER`
(`app/domain/scanning/entities.py`), `TriggerScanUseCase`
(`trigger_scan.py`), `normalize_scan_output()`/`NormalizedFinding`
(`normalization.py`), and `RunScanWorkflowUseCase`
(`run_scan_workflow.py`) -- the last three all new files in
`app/application/scanning/`. 44 new tests (41 unit, 3 integration
against real Postgres), 100% coverage on every Milestone 4 module, clean
Ruff, clean MyPy strict. See section 6 for the full account, including
the two flagged design decisions (`AI_ANALYZE` unconditionally
`SKIPPED`; scanner-native severity never written to
`Finding.ai_severity_level`) and the stale Milestone-3 cross-reference
this session corrected in section 5.

## 8. Remaining work

Everything else -- see the milestone roadmap below and "Files pending" in
`docs/implementation_progress.md` for the exhaustive list.

## 9. Milestone roadmap

See `docs/implementation_progress.md`, section "Overall roadmap" -- kept
there rather than duplicated here since it changes every session and this
file should not need editing that often.

## 10. Coding standards

- Python: Ruff (lint + format) + MyPy strict; full type hints on every
  function; every module gets a docstring explaining *why*, not just
  *what*.
- Domain layer has zero framework imports (no SQLAlchemy, no FastAPI, no
  AI SDK inside `domain/`).
- Value objects validate on construction (dataclass `__post_init__` /
  `frozen=True`) -- an invalid instance should never be constructible.
- Repository pattern for all database access; no business logic inside
  FastAPI route handlers -- routes call application-layer use cases only.
- No dependency is added to `pyproject.toml` until code in the repo
  actually imports it.
- TypeScript (Phase 3, not yet exercised): ESLint + Prettier, strict mode.

## 11. Testing requirements

- Test pyramid: many unit tests, fewer integration tests, few end-to-end
  API tests.
- Unit tests for every new function/class in the same session it is
  written -- never deferred.
- AI outputs: schema-validate the response, use golden-set regression
  checks; never assert exact text match against an LLM's output.
- Scanner adapters: golden-file tests against real captured sample output.
- Fake/spy implementations of ports (`EventBusPort`, `AIProviderPort`) for
  unit tests; real implementations reserved for a smaller integration
  suite.
- Every test that touches configuration passes explicit values and
  disables real env-file loading, so the machine running the tests can
  never leak into an assertion.

## 12. Development workflow

- Vertical-slice first: one thin end-to-end path before breadth.
- One milestone per session -- do not start milestone N+1 in the same
  session that finishes milestone N without explicit approval.
- Fix lint/type issues the moment they are found, in the same session --
  do not carry them forward as "fix later."
- Document technical debt explicitly the moment it is introduced (see
  `docs/implementation_progress.md`, "Technical debt") -- never let it
  accumulate silently.
- When implementation reveals a real design gap, explain it before
  adopting the fix (see the findings-deduplication redesign above as the
  standing example of how this should look).
- Do not mark a milestone "complete" on a lesser standard of verification
  than is actually available -- state precisely what was and wasn't
  checked (this rule added after Milestone 1's completion session, where
  "content-complete and statically verified" was used deliberately
  instead of an unqualified "complete," since dynamic execution was not
  possible in this environment).

## 13. Filesystem workflow

Project root: `C:\Users\gamer\Downloads\claudeOnly` (no space; every
prior reference to "claude only" with a space was a documentation typo
from an earlier session, caught by comparing against
`list_allowed_directories`, which is the authoritative source), accessed
via the Filesystem MCP (not the sandbox -- the sandbox is a disposable
verification environment used per-session, never the source of truth).

Tools available: `list_allowed_directories`, `list_directory`,
`list_directory_with_sizes`, `directory_tree`, `get_file_info`,
`read_file`, `read_text_file`, `read_multiple_files`, `write_file`,
`create_directory`, `move_file`, `edit_file`, `copy_file_user_to_claude`
(one-directional: user's machine -> sandbox only, never the reverse).

Known quirks, confirmed empirically, not assumed:
- `create_directory` does **not** create a full nested path in one call
  despite its description implying otherwise -- every directory in this
  project was created one level at a time, shallowest first.
- There is **no command-execution tool** for this filesystem. Pytest,
  Ruff, and MyPy cannot be run against these files through this
  connector. "Testing status" in the progress doc reflects sandbox
  verification, transplanted, not re-run in place. Self-verify locally,
  or use an environment with execution access (e.g. Claude Code).
- No bulk write/create operation exists -- one file or directory per call.
- Re-confirmed during Milestone 1's completion session: still no
  execution tool, still no bulk operation. Not re-assumed each time --
  checked again and found unchanged.
- Confirmed this session: the lack of a command-execution tool here can
  be worked around by reading files verbatim through this connector and
  writing identical copies into Claude's own sandboxed bash environment,
  then running pytest/Ruff/MyPy there. This confirms the code executes
  correctly against these exact file contents, but is not the same as
  confirming it on `C:\Users\gamer\Downloads\claudeOnly` directly --
  state that distinction plainly whenever this workaround is used, per
  section 12's verification-honesty rule.

## 14. Rules that must never change during implementation

- Do not redesign approved architecture without a genuine implementation
  blocker, and explain any such blocker before adopting a fix.
- Bounded context boundaries (section 1) and the ports-and-adapters
  pattern are locked.
- RLS for tenant isolation, the ActiveScanner/ImportScanner split, the
  append-only-history pattern (`finding_analyses`, `finding_status_history`,
  `asset_observations`, `audit_logs`), fingerprint-based deduplication,
  scanner network segmentation, ULID-as-UUID, and StrEnum-over-free-text
  are all locked decisions -- revisit only with a genuine blocker, not a
  preference.
- Production-quality code only; never recreate completed work; extend
  rather than rewrite unless rewriting is unavoidable; never skip tests;
  never introduce undocumented technical debt; never modify more than one
  milestone per session.
- This file (`PROJECT_STATE.md`) is the source of truth over chat history.
  If a future session's instructions conflict with what is written here,
  flag the conflict explicitly rather than silently picking one.

## 15. Current Implementation Queue

Status: Milestone 4 complete -- the processing pipeline orchestrator.
`derive_scan_status()`/`PIPELINE_STEP_ORDER` (domain layer),
`TriggerScanUseCase`, `normalize_scan_output()`, and
`RunScanWorkflowUseCase` (application layer), wired to the Milestone 3
`ScannerPort`/`NucleiAdapter` and the Milestone 2 repository layer. 44
new tests (41 unit, 3 integration against real Postgres), 100% coverage
on every Milestone 4 module, clean Ruff (lint + format), clean MyPy
strict. See section 6 for the full account, including two design
decisions flagged rather than silently resolved (`AI_ANALYZE`
unconditionally `SKIPPED`; scanner-native severity claims never written
to `Finding.ai_severity_level`) and a stale Milestone-3 cross-reference
in section 5 corrected to Milestone 4.

Next Session Goal:
Begin Milestone 5 (API layer -- public + internal split), pending
explicit approval.

Files to create (Milestone 5, per the roadmap in
docs/implementation_progress.md): the FastAPI router modules
(`/api/v1` public, `/internal` health/metrics/admin -- PROJECT_STATE.md
section 3's "Public/internal API split"), request/response schemas, and
the route handlers that call `TriggerScanUseCase`/`RunScanWorkflowUseCase`
(Milestone 4) and the Milestone 2 repositories -- no business logic in
the route handlers themselves, per section 10's coding standards.
`app/main.py` (currently pending, per section 4's folder structure) is
also in scope, since an API layer needs an app to mount its routers on.
