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
│   ├── alembic/versions/
│   ├── app/
│   │   ├── main.py (pending), config.py
│   │   ├── domain/
│   │   │   ├── shared/        (ids, clock, fingerprint, events, enums -- staging, see decisions)
│   │   │   ├── findings/      (value_objects.py done; entities pending)
│   │   │   ├── scanning/, assets/, identity/, reporting/   (scaffolded, empty)
│   │   ├── application/
│   │   │   ├── interfaces/    (ports -- scaffolded, empty)
│   │   │   ├── identity/, assets/, scanning/, findings/, reporting/  (scaffolded, empty)
│   │   ├── infrastructure/
│   │   │   ├── db/{models,repositories}/, ai_providers/, storage/,
│   │   │   │   vector_store/, event_bus/, observability/, security/
│   │   │   │   (all scaffolded, empty)
│   │   ├── scanner_engine/
│   │   │   ├── base_scanner.py (pending)
│   │   │   └── adapters/{nuclei,nmap,burp,zap,reconx,bughunter,sqlmap}/
│   │   │       (scaffolded, empty; nuclei/ is the Milestone 3 target,
│   │   │        the rest are Milestone/Phase 4 stubs)
│   │   ├── ai_agents/   (scaffolded, empty; analysis_service.py pending)
│   │   ├── api/v1/      (scaffolded, empty)
│   │   └── workers/     (scaffolded, empty)
│   └── tests/unit/   (config, ids, clock, fingerprint, events,
│                       value_objects -- all six done)
└── frontend/   (Phase 3, not started)
```

## 5. Domain model summary

| Entity | Lives in | Key invariant / behavior |
|---|---|---|
| Organization / Membership | identity/ | >= 1 Owner always; cannot remove the last one |
| Finding | findings/ | state machine `new -> triaged -> {confirmed, false_positive} -> {fixed, accepted_risk, wont_fix}`; `effective_severity` prefers CVSS over an AI estimate |
| Severity (value object) | findings/ | ordered: info < low < medium < high < critical -- **implemented, tested** |
| CVSS (value object) | findings/ | validates 0.0-10.0 + vector format; derives band from score -- **implemented, tested** |
| Asset | assets/ | current-state cache; identity = `(org_id, asset_type, normalized value)` |
| AssetObservation | assets/ | append-only; recording one updates the Asset cache |
| Scan | scanning/ | state machine `queued -> running -> {completed, failed, cancelled}`, derived from its workflow steps |
| WorkflowStep | scanning/ | `pending -> running -> {completed, failed, skipped}`, independently retryable |

Only Severity and CVSS are implemented as of this update; the rest are
designed (schema in decisions above) but not yet coded.

## 6. Current implementation status

Milestone 1 (Foundation) is complete: all 16 expected files exist,
content audited (imports traced, naming/domain-purity/documentation
checked), and dynamically executed -- pytest, Ruff (lint + format), and
MyPy strict all run and passing against these exact file contents. That
execution ran in Claude's own sandboxed environment, not on this machine
directly (this Filesystem MCP still has no command-execution tool -- see
section 13), against files read byte-for-byte from this repository and
written unmodified into the sandbox. It is not confirmation that the
commands succeed on `C:\Users\gamer\Downloads\claudeOnly` itself; that
remains a one-time manual step if bit-for-bit local confirmation matters.
Directory scaffolding for the entire approved architecture is complete.
Milestones 2-7 and Phases 3-10 not started. See
`docs/implementation_progress.md` for the live version of this section.

## 7. Completed work

Config system (role-based, fail-fast), ULID id generation, fingerprint
hashing, DomainEvent base, domain enums (staged), Severity/CVSS value
objects with full test coverage, full repository directory scaffold (37
directories, 34 `__init__.py` placeholders), three permanent-memory
documents, and a full static audit of the above (imports, naming,
architecture-layer purity, documentation) with zero issues found. 55
tests total, 100% coverage, clean Ruff, clean MyPy strict -- verified in
the sandbox prior to the Filesystem MCP pivot; not re-executed in this
location since.

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

Project root: `C:\Users\gamer\Downloads\claudeOnly` (no space -- corrected
this session; every prior reference to "claude only" with a space was a
documentation typo, caught by comparing against `list_allowed_directories`,
which is the authoritative source), accessed via the Filesystem MCP (not
the sandbox -- the sandbox holds an earlier, now-superseded copy of
Milestone 1 that was transplanted here file by file).

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

Status: Milestone 1 complete -- statically audited and dynamically
executed (sandbox-confirmed against identical on-disk file content; see
section 13). 55/55 tests passed, 100% coverage, clean Ruff (lint +
format), clean MyPy strict.

Next Session Goal:
Begin Milestone 2 (SQLAlchemy models, Alembic migration, repository
layer), pending explicit approval.

Files to create (Milestone 2):
- SQLAlchemy models for the schema in `docs/database.md` (interim: see
  the DDL described across the architecture-review chat history)
- Alembic migration
- Repository implementations of the repository ports
