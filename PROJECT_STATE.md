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
| Vector DB | Qdrant, behind `VectorStorePort` (Phase 5 Milestone 2 -- collection init/upsert/search only; wired into `ingestion_worker`'s composition root as of Milestone 5, conditional on `QDRANT_URL`) |
| Embeddings | `sentence-transformers` (local, CPU-only, `all-MiniLM-L6-v2` default) behind `EmbeddingPort` (Phase 5 Milestone 1; wired alongside Qdrant as of Milestone 5) |
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
- No `BaseAgent`/`NormalizerPort`/scanner registry yet — YAGNI. Three
  concrete `ActiveScanner` implementations exist as of Phase 4 (nuclei,
  nmap, sqlmap), selected via an explicit if/elif in
  `app/workers/tasks.py`'s `_select_active_scanner` and carried as a
  plain tuple in `AppState.active_scanners` (`app/api/dependencies.py`)
  — not a registry: no registration API, no dynamic/pluggable dispatch,
  just the known concrete classes hand-wired in source. `SqlmapAdapter`
  is not yet added to either (adapter-only session, mirroring nmap's own
  first session — see §7). Normalization (`normalize_scan_output`,
  `app/application/scanning/normalization.py`) follows the identical
  pattern for its own known output formats (`"nuclei-jsonl"`,
  `"nmap-xml"` — TD #16, resolved) — a plain if/elif, not an
  injectable `NormalizerPort`, for the same reason. `SqlmapAdapter`'s
  own `"sqlmap-stdout"` output format deliberately has no normalizer
  branch yet — sqlmap has no documented machine-readable contract for
  injection-point findings the way nuclei/nmap do, so
  `normalize_scan_output` currently raises
  `UnsupportedScanOutputFormatError` for it, honestly, rather than a
  fabricated parser (TD #18). `BaseAgent` still has exactly one concrete
  shape; build it only when a second real AI-agent shape exists.
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
- **Phase 5 planning decision: `EmbeddingPort` is a separate port from
  `AIProviderPort`, not an extension of it** — different capability
  shape (text → vector, not text → text), same reasoning that already
  split `ScannerPort` into `ActiveScanner`/`ImportScanner`. Concrete
  adapter: local `sentence-transformers` (`all-MiniLM-L6-v2`), CPU-only
  by design (no CUDA/GPU handling attempted). Belongs to
  `ingestion_worker`, the same process `AnthropicProvider`/
  `AnalysisService` already belong to — nothing constructs it from
  `backend` (see `app/config.py`'s `check_role_boundaries`). Initial
  knowledge corpus (also locked): CWE Top 25 (MITRE/CISA), one chunk
  per entry — ingestion is not yet built (Milestone 1 only implemented
  the port + adapter, see §7).
- **Phase 5 Milestone 2: `VectorStorePort` mirrors `StoragePort`'s own
  binding rule** — one instance bound to exactly one collection (and,
  for the concrete adapter, its vector size/distance) at construction,
  never a per-call parameter. Scoped to exactly three operations
  (`ensure_collection`, `upsert`, `search`) — no delete/update/filter/
  pagination, matching the planned Milestone 3/4 flow only. Concrete
  adapter (`QdrantVectorStorePort`) uses `qdrant-client`'s native
  `AsyncQdrantClient` directly, not `asyncio.to_thread` — unlike Minio/
  sentence-transformers, this SDK already ships a real async client,
  mirroring `AnthropicProvider`'s own precedent. Default distance
  metric: cosine (standard for sentence-transformers embeddings).
  Point ids must be UUID-formatted strings or unsigned integers — a
  Qdrant server-side constraint; a natural id that isn't already
  UUID-shaped (e.g. "CWE-79") needs a deterministic UUID derived from
  it before Milestone 3 can write it. Not wired into application
  startup; TD #19 (CPU-only PyTorch footprint) is unrelated to this
  milestone and remains deferred to Milestone 5.
- **Phase 5 Milestone 3: CWE Top 25 ingestion is a plain module of
  three pure functions + one constructor-injected use case, not a
  class hierarchy** -- `parse_cwe_top25_xml` (source parsing/
  filtering), `build_cwe_chunk` (chunk-text construction), and
  `cwe_point_id` (deterministic id) are independently testable module-
  level functions; `IngestCweTop25UseCase` orchestrates them plus
  `EmbeddingPort`/`VectorStorePort` (both constructor-injected,
  mirroring `TriggerScanUseCase`'s own convention). Deliberately kept
  independent of `AnalysisService` -- lives in the new
  `app/application/knowledge/` folder, not `app/ai_agents/`. Corpus
  source: `backend/data/cwe/2025_top25.xml`, the vendored official
  MITRE CWE View-1435 XML export for the 2025 Top 25 (Madhav placed
  the file directly; not downloaded or reconstructed from memory --
  see §16 for the blocker this resolved). Deterministic point ids:
  `uuid.uuid5(uuid.NAMESPACE_URL, f"https://cwe.mitre.org/data/
  definitions/{cwe_id}.html")` -- reproducible without this module
  inventing its own namespace UUID, and safe for `upsert`'s insert-or-
  replace semantics on a re-run. Parser validates defensively (root
  `Name` contains "2025"/"Top 25", exactly 25 `Weakness` elements,
  every entry has an id/name/description) and raises `CweSourceError`
  -- a new exception distinct from `EmbeddingError`/`VectorStoreError`
  -- rather than silently proceeding on a malformed or substituted
  source. No refresh/update mechanism -- static one-time ingestion is
  the locked MVP scope (a future year's list is a new corpus decision,
  not a parameter of this one).
- **Phase 5 Milestone 4: retrieval lives entirely inside
  `AnalysisService`, not in `RunScanWorkflowUseCase`.** `EmbeddingPort`/
  `VectorStorePort` are optional constructor-injected dependencies on
  `AnalysisService` (default `None`, mirroring `AIProviderPort`'s own
  DI convention) -- when both are supplied, `analyze()` embeds a query
  built from the finding's own title/description, retrieves a small
  top-k (default 3) set of CWE matches, and includes them in the
  prompt as clearly-labeled reference context, never as instructions.
  A retrieval-layer failure (`EmbeddingError`/`VectorStoreError` from
  either port) or zero matches degrades to exactly the pre-Milestone-4
  prompt/behavior -- never fails `analyze()`. `PROMPT_VERSION` bumped
  to `"v2"`. Provenance (retrieved CWE ids/scores, embedding model,
  corpus/version, top-k limit -- never a raw vector) is added to
  `FindingAnalysis.model_metadata`'s `"retrieval"` key only when there
  was at least one match. `run_scan_workflow.py` needed **zero**
  changes -- it only imports the `PROMPT_VERSION` symbol and
  constructs `AnalysisService(provider=...)`, both unchanged-compatible
  call shapes. **Note for a future session:** `FindingAnalysis` already
  has its own dedicated `kb_version: str | None` column (migrated since
  Milestone 2, docstring: "nullable -- no RAG until Phase 5"), clearly
  provisioned for exactly this purpose -- this milestone deliberately
  did not populate it, since the instruction named `model_metadata`
  specifically and populating `kb_version` too would have meant
  changing `FindingAnalysisResult`'s shape and `_persist`'s call, a
  larger footprint than asked for. Worth a explicit decision in a
  future session, not a silent choice either way.
- **Phase 5 Milestone 5: RAG wiring lives in `app/workers/tasks.py`'s
  new `_build_analysis_service` helper, never in `app/main.py`.**
  `_run_scan_workflow_from_settings` (ingestion_worker's composition
  root) calls it with an already-constructed `provider` (so the
  `assert settings.anthropic_api_key is not None` mypy-narrowing a few
  lines above stays effective -- mypy does not carry an assert's
  narrowing across a function call) and `settings.qdrant_url`. When
  `qdrant_url` is `None`, the call is `AnalysisService(provider=
  provider)` -- byte-identical to the pre-Milestone-5 shape. When set,
  `SentenceTransformerEmbeddingPort`/`QdrantVectorStorePort` are
  imported *lazily*, inside that helper's own RAG-configured branch,
  not at `tasks.py`'s top -- `app/api/dependencies.py` (and therefore
  `app/main.py`, the API process) imports this module purely for the
  `run_scan_workflow_task` Celery task object, never executing this
  composition root's own function bodies, so a top-level import would
  force `backend`'s image to have `sentence-transformers`/
  `qdrant-client` installed for no reason. `qdrant_url` itself stays
  unvalidated in `check_role_boundaries` (unlike `anthropic_api_key`)
  -- requiring it would be a bigger behavior change than Phase 5 has
  made: a scan can run, and always could, with no Qdrant configured,
  since `AnalysisService`'s retrieval is optional-by-design (Milestone
  4). Collection name: `IngestCweTop25UseCase`'s own `CORPUS_NAME`
  (`"cwe_top25"`), imported, not duplicated as a literal. Vector size:
  a new `EMBEDDING_VECTOR_SIZE = 384` constant on
  `SentenceTransformerEmbeddingPort` itself (co-located with
  `DEFAULT_MODEL_NAME`, since it's a fixed fact about that one model,
  not a general property this adapter could vary independently).
  **`FindingAnalysis.kb_version` is now populated** (resolving
  Milestone 4's own open note above): confirmed, by inspecting the ORM
  model and repository mapping, to be the intended persisted
  representation (an already-migrated, dedicated column whose own
  docstring reads "nullable -- no RAG until Phase 5") -- a new
  `_build_kb_version` helper in `analysis_service.py` sets it to
  `"<corpus>:<corpus_version>"` (e.g. `"cwe_top25:2025"`) when
  retrieval had at least one match, else `None`, threaded through
  `FindingAnalysisResult` and `_persist`. **TD #19 (CPU-only PyTorch)
  addressed in code/config this session, not build-verified** --
  `backend/pyproject.toml` gained a `rag` optional-dependency group
  (`sentence-transformers`/`qdrant-client`, split out of the base
  `dependencies` list; `dev` self-references it so `pip install -e
  ".[dev]"` keeps working unchanged), and `backend/Dockerfile` gained
  an `INSTALL_RAG_DEPENDENCIES` build arg (default `false`) that,
  when `"true"`, installs `torch` from PyTorch's own CPU wheel index
  before `pip install ".[rag]"`. `docker-compose.yml`'s `worker`
  service passes that arg; `backend` does not, so it gets the smaller,
  RAG-free image via the Dockerfile's own default. Neither the CPU-only
  torch install nor the image-size reduction could be verified by an
  actual Docker build this session -- see §11/TD #19.

## 5. Folder structure (current)

Root: `PROJECT_STATE.md`, `README.md`, `docker-compose.yml`,
`.env.example`, `AI_ENGINEERING_RULES.md`, `LICENSE`, `docs/`
(companions to this file; eight other planned `docs/*.md` files still
pending — §13), `frontend/` (Phase 3, scaffold + API client + auth flow
+ org/scan UI complete — §7).

`backend/`: `pyproject.toml` (base `dependencies` + `dev`/`rag`
optional groups as of Phase 5 Milestone 5, see §4/§10), `Dockerfile`
(`INSTALL_RAG_DEPENDENCIES` build arg as of Milestone 5, see §4),
`.dockerignore`,
`alembic.ini`, `alembic/env.py`,
`alembic/versions/6bdbf0ab25b0_initial_schema.py` (19 tables + RLS),
`data/cwe/2025_top25.xml` (vendored official MITRE CWE View-1435 XML
export, 2025 Top 25 — Phase 5 Milestone 3's corpus source of truth,
placed directly by Madhav, not downloaded by Claude — see §4/§16).

`backend/app/` (`main.py`, `config.py` at top level), mirroring the
bounded contexts in §2 under each layer:
- `domain/{shared,findings,scanning,assets,identity,reporting}/` —
  entities/value objects done for all six.
- `application/interfaces/` (all repository ports + `ScannerPort`,
  `StoragePort`, `AIProviderPort`, `RefreshTokenRepositoryPort`,
  `EmbeddingPort`, `VectorStorePort` (Phase 5 Milestones 1-2);
  `EventBusPort` pending),
  `application/knowledge/` (`IngestCweTop25UseCase`, Phase 5
  Milestone 3 — deliberately independent of `ai_agents/`),
  `application/scanning/` (done),
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
  `infrastructure/embeddings/` (`SentenceTransformerEmbeddingPort`,
  Phase 5 Milestone 1), `infrastructure/vector_store/`
  (`QdrantVectorStorePort`, Phase 5 Milestone 2),
  `infrastructure/{event_bus,observability}/` (empty).
- `scanner_engine/adapters/{nuclei,nmap,sqlmap}/` (done, `sqlmap`
  adapter-only — no pipeline/API wiring, no normalizer yet, see §7);
  `burp`/`zap` (empty Phase-4 `ImportScanner`-shaped stubs).
  `reconx`/`bughunter` — permanently excluded from this project's
  scanner roster by explicit decision (Madhav's own separate, earlier
  ReconX/BugHunter PRO projects; not reused, ported, reconstructed, or
  referenced going forward — see §13). Their now-orphaned stub folders
  were confirmed unused (no imports, no tests, no wiring anywhere in
  this repository) and safe to delete, but could not actually be
  removed this session — the Filesystem MCP connector exposes no
  delete/rmdir tool (see §15); flagged for manual deletion or a future
  session with delete capability.
- `ai_agents/analysis_service.py` (no `BaseAgent` yet; Phase 5
  Milestone 4 added optional CWE-retrieval, see §4; Milestone 5
  populated `kb_version`);
  `workers/{celery_app,tasks}.py` (`tasks.py`'s `_build_analysis_service`
  wires Phase 5 retrieval as of Milestone 5, see §4).

`backend/tests/`: `conftest.py`; `unit/` (36 files); `integration/`
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
| — | Phase 4 — Nmap scanner adapter (not a milestone; out-of-sequence explicit-instruction session, ahead of Phase 3 frontend Step 5) | `scanner_engine/adapters/nmap/adapter.py`: `NmapAdapter(ActiveScanner)`, following `NucleiAdapter`'s exact shape — routes through the existing `validate_target`/`run_scanner_subprocess`, returns the existing `ScanOutput` shape, no new abstractions/registry/pipeline changes. Scan type pinned to `-sT -Pn` (TCP connect + skip host discovery, both unprivileged) — the code-level "no privileged/raw-packet scanning" requirement, on top of (not instead of) `run_scanner_subprocess`'s own non-root guard. Target is always the single already-validated hostname appended last, never a range — no target-expansion surface. Output format tagged `nmap-xml` (nmap's own `-oX -`), unparsed beyond a shallow `<nmaprun` sanity check (real parsing stays normalization's job, per §1's Scanning/Findings boundary). Explicitly handles: missing binary (`FileNotFoundError` → `ScannerExecutionError`), timeout (propagates `ScannerTimeoutError` from `run_scanner_subprocess` uncaught), non-zero exit (always a hard failure for nmap — deliberately stricter than nuclei's tolerant classification; see the adapter's own comment on why), empty output, and malformed/non-XML output. `tests/unit/test_nmap_adapter.py` — 9 new tests, fakes/spies only, no real `nmap` binary required. No ZAP/Burp/SQLMap, no `ScannerPort` changes, no wiring into the API layer's scanner-name literal or the pipeline — explicitly out of scope, stopped after Nmap per instruction. |
| — | Phase 4 — Nmap adapter wiring into scan pipeline/API (not a milestone; second out-of-sequence explicit-instruction session, directly following the adapter-only one above) | Made `nmap` actually selectable/executable through the existing scan flow, without a registry/factory/`ScannerPort` redesign. `app/api/v1/schemas.py`: `scanner_name: Literal["nuclei", "nmap"]`. `app/api/dependencies.py`: `AppState.active_scanner` (singular) → `active_scanners: tuple[ActiveScanner, ...]`; `get_active_scanner` → `get_active_scanners`; `ScanDispatcher` widened to carry `scanner_name` alongside the ids. `app/main.py`: `_lifespan` constructs both `NucleiAdapter()` and `NmapAdapter()`. `app/api/v1/scans.py`: `run_scan` checks `scan.scanner_name` against the whole wired-names set (409 if unmatched) and passes `scan.scanner_name` through to the dispatcher. `app/workers/tasks.py`: new `_select_active_scanner(scanner_name)` helper — an explicit if/elif over exactly the two known adapters (not a registry — see §4), reusing `ScannerMismatchError` for an unrecognized name; `scanner_name` threaded through `_run_scan_workflow_from_settings`/`run_scan_workflow_task`. `RunScanWorkflowUseCase`/`execute_scan_workflow`/the 8-step pipeline/target validation/subprocess boundary/every other adapter — all untouched, confirmed by direct diff review. New `tests/unit/test_workers_tasks.py` (3 pure-function tests for `_select_active_scanner`); updated `test_api_dependencies.py`, `test_api_schemas.py`, `test_api_scans.py` (+3 new nmap-path tests), `test_main_lifespan.py`, `test_scan_worker_task.py` (+1 new integration test proving the worker selects `NmapAdapter`, not `NucleiAdapter`, for an nmap-scoped scan). |
| — | Phase 4 — Nmap XML normalizer (not a milestone; third out-of-sequence explicit-instruction session, resolving TD #16) | `app/application/scanning/normalization.py`: `normalize_scan_output` gained a second dispatch branch for `"nmap-xml"` (plain if/elif alongside the existing `"nuclei-jsonl"` branch — explicitly not a `NormalizerPort`, per instruction not to introduce new pipeline/scanner abstractions; mirrors the `_select_active_scanner` precedent). New `_parse_nmap_xml`/`_nmap_host_value`/`_nmap_port_to_normalized`/`_nmap_service_description` (stdlib `xml.etree.ElementTree`, no new dependency) turn nmap's `-oX` XML into one `NormalizedFinding` per **open** port across every **up** host — closed/filtered ports and down hosts contribute nothing. Every nmap-derived finding is `raw_severity="info"` with no CVE/CVSS candidate (an honest reflection of what a plain `-sT -Pn` TCP-connect scan with no NSE vulnerability scripts actually detects — see `NmapAdapter`'s own scope). `template_id="open-port-{protocol}-{portid}"` gives the same per-scan-recurring-finding fingerprint stability nuclei's `template-id` already provides. `host` prefers a resolved hostname over the raw IP, mirroring nuclei's own host/ip fallback. Ten new `test_normalization.py` cases (single/multiple open ports, closed/filtered-port exclusion, down-host exclusion, hostname-vs-address preference, missing-service handling, empty input, malformed XML, multi-host); the pre-existing `test_unsupported_output_format_raises` (previously asserting `"nmap-xml"` itself was unsupported) was updated to use `"burp-xml"` instead, since that claim is no longer true. `tests/integration/test_scan_worker_task.py`'s nmap-selection test (added by the wiring session immediately above) was upgraded in step: its fake scanner now emits real nmap-XML-shaped output for the `"nmap-xml"` persona (previously always emitted nuclei-JSON-shaped output regardless of persona, which this new normalizer would have flagged as malformed XML), and the test now asserts genuine full `Scan.status is COMPLETED` — matching its nuclei sibling test — rather than only the `EXECUTE_SCANNER` step's status, since normalization can now actually carry an nmap-scoped scan all the way through. |
| — | Phase 4 — SQLMap scanner adapter (not a milestone; fourth out-of-sequence explicit-instruction session, adapter-only — mirrors Nmap's own first session's scope exactly) | `scanner_engine/adapters/sqlmap/adapter.py`: `SqlmapAdapter(ActiveScanner)`, following `NmapAdapter`'s exact shape — routes through the existing `validate_target`/`run_scanner_subprocess`, returns the existing `ScanOutput` shape, no new abstractions/registry/pipeline changes, no wiring into `AppState.active_scanners`/`_select_active_scanner`/the `scanner_name` API literal (deferred to a future wiring session, exactly mirroring Nmap's own three-session arc). Target passed to sqlmap's `-u` flag is `validated.original` (the caller's exact target string, e.g. a full URL with a query parameter to actually test), not `validated.hostname` — SQL injection testing needs an endpoint/parameter, not just a bare host; `validate_target`'s SSRF checks already cover whatever host is embedded in it regardless of shape. Flags pinned explicitly rather than left to sqlmap's own defaults: `--batch` (required — without it sqlmap prompts interactively and the subprocess provides no stdin), `--risk=1 --level=1` (least-aggressive payload settings), `--technique=BEUT` (boolean-blind/error-based/UNION/time-blind — excludes `S`, stacked queries, which can run arbitrary additional destructive SQL, mirroring Nmap's own no-privileged/no-exploitative-behavior stance). Output tagged honestly as `"sqlmap-stdout"` — captured stdout text, not a documented machine-readable contract (sqlmap has no `-oX`/`--format=json` equivalent for injection-point findings) — **deliberately has no normalizer** (TD #18): fabricating a parser for sqlmap's human-readable progress/summary text was explicitly rejected as inventing an undocumented contract; `normalize_scan_output("sqlmap-stdout", ...)` currently raises `UnsupportedScanOutputFormatError`, verified by a new test, not just documented. Non-zero-exit/empty-output classification deliberately differs from both existing adapters (documented in the adapter's own code): unlike nmap, a non-zero exit alone is not an automatic hard failure (sqlmap's exit-code convention isn't confidently known); unlike nuclei, truly empty output is *always* a failure regardless of exit code (sqlmap always prints banner/status text on any real run, unlike nuclei's deliberately silent mode). `tests/unit/test_sqlmap_adapter.py` — 9 new tests, fakes/spies only, no real `sqlmap` binary required; 1 new `test_normalization.py` case confirming the unsupported-format claim. No ScannerPort/pipeline/registry changes, no other adapter touched — confirmed by direct diff review. |
| — | Phase 4 — `sqlmap-stdout` normalizer re-investigation (not a milestone; fifth out-of-sequence explicit-instruction session; TD #18 re-confirmed, not resolved — no code changed) | Explicitly instructed to attempt implementing the normalizer, with a directive to parse only what the repository/tests establish as reliable and to stop and document rather than fabricate if the available output is insufficient. Re-inspected `normalization.py`, both existing normalizers, `SqlmapAdapter`, and `test_sqlmap_adapter.py`, then searched the entire `tests/` tree for any real captured sqlmap output/fixture — found none; the only sqlmap output content anywhere in the repository is `test_sqlmap_adapter.py`'s own `_SAMPLE_OUTPUT`, which the repository itself documents as a fabricated stand-in, not a real reference. Per direct instruction, stopped rather than write a parser grounded in outside knowledge the repository doesn't establish as reliable. Zero files modified; TD #18 (§12) rewritten to record precisely what was re-checked this session, not just re-stated. Verification re-run anyway for a genuine current confirmation (not resting on the prior session's numbers) — see §11. |
| — | Documentation/roadmap cleanup (not a milestone; sixth out-of-sequence explicit-instruction session; no source code changed) | Two documentation-only corrections, see §16: (1) ReconX and BugHunter PRO permanently excluded from this project's scanner roster — removed from the roster/lists/counts/TD-references/roadmap text wherever they appeared as remaining/future work across this file, `docs/session_state.md`, and `docs/implementation_progress.md`; their two stub adapter folders confirmed unused (no imports, no tests, no wiring) but could not be deleted — no delete tool available via the Filesystem MCP, see §15. (2) The unsupported "14"/"12 remaining" scanner-adapter figures (no named source anywhere in the repository) replaced with an accurate statement naming only what the repository establishes: implemented (`nuclei`, `nmap`, `sqlmap`), scaffolded-but-unimplemented `ImportScanner`-shaped stubs (`burp`, `zap`), and an explicit note that the full Phase 4 roster is not enumerated here. No scanner code, registry, or factory added or changed. |
| — | Phase 5 Milestone 1 — `EmbeddingPort` + `sentence-transformers` adapter (not part of numbered Milestones 1–7 above; first Phase 5 session) | `app/application/interfaces/embedding_port.py`: `EmbeddingPort` (ABC, one method `embed(text) -> EmbeddingResult`), `EmbeddingResult`, `EmbeddingError` — a separate port from `AIProviderPort`, not an extension of it (see §4). `app/infrastructure/embeddings/sentence_transformer_provider.py`: `SentenceTransformerEmbeddingPort`, loads `all-MiniLM-L6-v2` once at construction (`device="cpu"` explicit), wraps the synchronous `.encode()` call in `asyncio.to_thread` (mirrors `MinioStoragePort`'s precedent). `pyproject.toml`: `sentence-transformers>=3.0` added to base `dependencies` (same shared-image placement as `anthropic`, matching precedent — see §12 TD #19 for the real image-size consequence this surfaced). No Qdrant/`VectorStorePort`, no ingestion, no `AnalysisService` change — explicitly out of scope, stopped after Milestone 1 per instruction. |
| — | Phase 5 Milestone 2 — `VectorStorePort` + Qdrant adapter (second Phase 5 session) | `app/application/interfaces/vector_store_port.py`: `VectorStorePort` (ABC, three methods — `ensure_collection()`, `upsert(points)`, `search(query_vector, limit=5)`), `VectorPoint`, `VectorMatch`, `VectorStoreError` — mirrors `StoragePort`'s one-instance-per-bucket binding rule (one instance per collection). `app/infrastructure/vector_store/qdrant_vector_store.py`: `QdrantVectorStorePort`, using `qdrant-client`'s native `AsyncQdrantClient` directly (no `asyncio.to_thread` — the SDK already ships a real async client, mirroring `AnthropicProvider`'s precedent). `ensure_collection()` is idempotent (checks `collection_exists` before `create_collection`); `upsert()` calls `ensure_collection()` first; `search()` does not. Default distance metric: cosine. `pyproject.toml`: `qdrant-client>=1.9` added to base `dependencies`. No ingestion, no CWE data, no `EmbeddingPort`/`AIProviderPort`/`AnalysisService` change, no retrieval wiring, no Docker/startup wiring — explicitly out of scope, stopped after Milestone 2 per instruction. |
| — | Phase 5 Milestone 3 — CWE Top 25 ingestion use case (third Phase 5 session) | `app/application/knowledge/ingest_cwe_top25.py`: `parse_cwe_top25_xml` (source parsing/filtering), `build_cwe_chunk` (chunk construction), `cwe_point_id` (deterministic `uuid5` id), `CweEntry`, `CweSourceError`, `IngestCweTop25UseCase` (constructor-injected `EmbeddingPort`/`VectorStorePort`, mirrors `TriggerScanUseCase`'s convention) — deliberately independent of `AnalysisService`. Corpus source: the vendored `backend/data/cwe/2025_top25.xml` (official MITRE CWE View-1435 export for the 2025 Top 25, placed directly by Madhav after an initial network-access blocker was reported and resolved — see §16). Parser validates the root view name and asserts exactly 25 `Weakness` elements, raising `CweSourceError` otherwise. No Qdrant search/retrieval, no `AnalysisService`/prompt change, no refresh/update scheduling, no Docker wiring — explicitly out of scope, stopped after Milestone 3 per instruction. |
| — | Phase 5 Milestone 4 — retrieval integrated into `AnalysisService` (fourth Phase 5 session) | `app/ai_agents/analysis_service.py`: `AnalysisService.__init__` gained optional `embedding_port`/`vector_store`/`retrieval_limit` (default `None`/`None`/3) — when configured, `analyze()` builds a retrieval query from the finding's title/description, embeds it, searches the vector store (`limit=retrieval_limit`), and includes matches as a clearly-labeled "reference context, not instruction" section in the prompt. `PROMPT_VERSION` bumped to `"v2"`. Retrieval failures (`EmbeddingError`/`VectorStoreError`) and zero matches both degrade to the exact pre-Milestone-4 prompt/behavior, never failing `analyze()`. Provenance (cwe_ids/scores/embedding_model/corpus/corpus_version/limit, never a raw vector) added to `FindingAnalysisResult.model_metadata["retrieval"]` only when there was at least one match. `run_scan_workflow.py` required zero changes. 20 new unit tests added to `test_analysis_service.py` (13 pre-existing, 33 total) (`EmbeddingPort`/`VectorStorePort` faked at the port level). No `AIProviderPort`/`EmbeddingPort`/`VectorStorePort` contract change, no CWE ingestion change, no Docker wiring, no `RAGService`/registry abstraction — explicitly out of scope, stopped after Milestone 4 per instruction. |

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
`celery`, `pyjwt`, `bcrypt`, `email-validator`. Optional `rag` group
(Phase 5 Milestone 5, split out of the base list above; self-referenced
from `dev` so local/CI runs still get it unconditionally — see §4):
`sentence-transformers` (Phase 5 Milestone 1 — CPU-only, see §12 TD
#19 for the real default-install image-size consequence and how
Milestone 5 addressed it in code/config), `qdrant-client` (Phase 5
Milestone 2). Dev: `pytest`,
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

**Phase 4 — SQLMap scanner adapter.** Same reconstruct-and-run-in-sandbox
approach as the Nmap adapter-only session (no command-execution tool
exists against the real repository — §15), reusing the still-intact
sandbox. `SqlmapAdapter` plus its direct dependencies
(`ScannerPort`/`ScanOutput`, `validate_target`, `run_scanner_subprocess`,
`utcnow`) were exercised alongside the unmodified `NmapAdapter`/
`NucleiAdapter`/`base_scanner` test files, to check for regressions on
code this session did not touch. **`pytest tests/ -q` → 99 passed, 0
failed** (9 new `test_sqlmap_adapter.py` cases — argv construction with/
without `extra_args`, missing binary, timeout propagation, non-zero
exit tolerated with output present, empty output always a failure
regardless of exit code, target-validated-before-subprocess-call; 1 new
`test_normalization.py` case confirming `"sqlmap-stdout"` currently
raises `UnsupportedScanOutputFormatError`; 89 pre-existing cases across
every other test file, confirming zero regression). Ruff (lint +
format, `--fix` applied for two auto-fixable line-length findings) and
MyPy `--strict` both clean on all three changed/new files
(`adapter.py`, `test_sqlmap_adapter.py`, `test_normalization.py`) and on
the full package (103 source files, whole-package sweep — up one file
from the new adapter). Byte-count integrity check (`get_file_info` vs.
sandbox `wc -c`) confirmed an exact-match transplant for all files
touched (`adapter.py`: 6660 bytes; `test_sqlmap_adapter.py`: 8458 bytes;
`test_normalization.py`: 11137 bytes), clean on the first attempt.
**Not verified:** a real `sqlmap` binary (same documented gap as
`NucleiAdapter`/`NmapAdapter` — TD #17); no pipeline/API wiring (not
requested this session — mirrors Nmap's own adapter-only first session
exactly, TD to be resolved in a future wiring session same as Nmap's
was). No `ScannerPort`, pipeline, other-adapter, or normalization-dispatch
file was changed beyond the one honest addition of a test confirming
the already-existing `UnsupportedScanOutputFormatError` path —
confirmed by direct diff review, not just by claim.

**TD #18 re-investigation (no code changed).** A follow-up session
explicitly instructed to attempt the `sqlmap-stdout` normalizer, with a
directive to stop and document rather than fabricate if the repository
doesn't establish a reliable format. It doesn't (re-confirmed by a fresh
search, not just citing the prior session's note — see TD #18's own
updated entry for exactly what was checked). Zero files were modified.
Still re-ran verification for a genuine, current confirmation rather
than resting on the prior session's numbers: **`pytest tests/ -q` → 99
passed, 0 failed** (unchanged from the SQLMap adapter session, as
expected — no code changed). Ruff (lint + format) and MyPy `--strict`
both still clean on `normalization.py`, `sqlmap/adapter.py`,
`test_sqlmap_adapter.py`, `test_normalization.py`.

**Phase 5 Milestone 1 — `EmbeddingPort` + `sentence-transformers`
adapter.** A narrower, isolated reconstruction than every backend entry
above — deliberately so: the two new files (`embedding_port.py`,
`sentence_transformer_provider.py`) have zero imports from the rest of
`app/`, so only a minimal package skeleton plus these two files and
their test were reconstructed in Claude's sandbox, not the full `app/`
tree. **`pytest tests/ -v` → 8 passed, 0 failed** (all new —
`test_sentence_transformer_provider.py`: default/configured model name,
constructor loads the model once with `device="cpu"`, `embed()` returns
the correct vector/model, non-native numeric values narrowed to plain
`float`, text passed through to `encode()` correctly, underlying
failures translated to `EmbeddingError` without losing the original
message), 100% coverage on both new modules. `SentenceTransformer` is
fully mocked in every test — no real model download/load ever happens
(confirmed indirectly: the run completed in ~9s). Ruff (lint + format)
and MyPy `--strict` both clean on all three new files. Byte-count
integrity check (`get_file_info` vs. sandbox `wc -c`) confirmed an
exact-match transplant for all three (`embedding_port.py`: 2787 bytes;
`sentence_transformer_provider.py`: 4207 bytes;
`test_sentence_transformer_provider.py`: 5449 bytes). **Not re-run:**
the rest of the suite — this session's own files are fully isolated
from every other module, per the standing per-session scope note above.
**A real, unanticipated finding surfaced during sandbox verification,
not from the design discussion:** installing `sentence-transformers`
via plain PyPI pulls the default CUDA-enabled `torch` build — 5.8GB
installed in the sandbox venv, 3.2GB of it pure NVIDIA/CUDA packages,
for a CPU-only adapter. See TD #19.

**Phase 5 Milestone 2 — `VectorStorePort` + Qdrant adapter.** Same
isolated-reconstruction approach as Milestone 1 — `vector_store_port.py`/
`qdrant_vector_store.py` have zero imports from the rest of `app/`
beyond `embedding_port.py`'s own sibling pattern, so the Milestone 1
sandbox skeleton was extended, not rebuilt. **`pytest tests/ -v` → 17
passed, 0 failed** (9 new — `test_qdrant_vector_store.py`:
`ensure_collection` creates when missing/skips when present/translates
failures, `upsert` ensures the collection first then writes points with
correct id/vector/payload shape and translates failures, `search`
returns matches with string-converted ids and `{}`-defaulted payload,
passes the query vector and `limit` through correctly, does *not* call
`ensure_collection`, and translates failures; plus the 8 pre-existing
Milestone 1 tests, re-run unchanged and still passing — confirming no
regression). 100% coverage on both new modules. `AsyncQdrantClient` is
fully mocked in every test (a fake class with async methods, mirroring
`test_anthropic_provider.py`'s own pattern for an SDK with a native
async client, rather than `test_minio_storage.py`'s sync-wrapped-client
pattern) — no running Qdrant container is required or contacted; the
docker-compose `qdrant` service has no host port mapping in any case
(only reachable from other containers on the `queue` network), so a
real-server test was never possible from this sandbox. Ruff (lint +
format) and MyPy `--strict` both clean on all three new/extended files.
Byte-count integrity check (`get_file_info` vs. sandbox `wc -c`)
confirmed an exact-match transplant for all three
(`vector_store_port.py`: 4442 bytes; `qdrant_vector_store.py`: 4012
bytes; `test_qdrant_vector_store.py`: 8288 bytes). **Not re-run:** the
rest of the suite, per the same standing per-session scope note. No new
image-size-class finding this session — `qdrant-client`'s own
dependency footprint is ordinary (no CUDA/GPU-adjacent packages
pulled). See TD #20 for this adapter's own mocked-only verification
gap, the same class of gap already recorded for `MinioStoragePort`/
`NucleiAdapter` (TD #6/#7).

**Phase 5 Milestone 3 — CWE Top 25 ingestion use case.** Same
isolated-reconstruction approach as Milestones 1-2 — the new module
imports only `EmbeddingPort`/`VectorStorePort` (both already in the
sandbox skeleton) plus stdlib, so no further package reconstruction was
needed. **`pytest tests/unit/test_ingest_cwe_top25.py -v` → 23 passed,
0 failed** (parsing/filtering: exact-25 extraction, wrong-count/
malformed-XML/wrong-view-name/missing-id-or-name/missing-description
all raise `CweSourceError`; id/name/description extraction; nested
`xhtml:p` markup flattened and whitespace collapsed; multiple
mitigations joined with phase labels; mitigations with no description
text skipped; no-mitigations case. Chunk construction: includes id/
name/description/mitigations, omits the mitigations section when
empty, deterministic across calls. Deterministic ids: valid UUID
string, stable for the same CWE id, distinct across different CWE ids.
Orchestration: embeds and upserts all 25 entries in one `upsert` call,
upserted point has the expected deterministic id/vector/payload
shape, missing/malformed source file raises `CweSourceError`,
`EmbeddingError`/`VectorStoreError` propagate unchanged — not wrapped
or swallowed). 100% coverage on the new module. Full sandbox suite
(`pytest tests/ -v`) → **40 passed, 0 failed** (23 new + 8 Milestone 1
+ 9 Milestone 2, re-run unchanged and still passing — confirming no
regression). Ruff (lint + format) and MyPy `--strict` both clean.
Byte-count integrity check (`get_file_info` vs. sandbox `wc -c`)
confirmed an exact-match transplant for both new files
(`ingest_cwe_top25.py`: 9566 bytes; `test_ingest_cwe_top25.py`: 14289
bytes). No real CWE source file, embedding model, or Qdrant server
was used in any test — XML fixtures are built programmatically,
mirroring the vendored source's structure as directly inspected, not
copied from it.

**A real, significant verification limitation, disclosed rather than
glossed over: the vendored 1.21MB source file's exact weakness count
was not confirmed by full manual enumeration this session.** The
Filesystem MCP's `read_text_file` has an effective per-call response
cap well under the file's own 1.21MB size (observed ceiling ~500-520KB
for both `head` and `tail`), and extensive `head`/`tail` bisection from
both ends of the file could not be made to meet in the middle —
roughly 250KB in the file's interior was never directly read this
session. What *was* directly confirmed: the root element's exact,
specific self-declaration (`Name="VIEW LIST: CWE-1435: Weaknesses in
the 2025 CWE Top 25 Most Dangerous Software Weaknesses"`, official
MITRE `cwe-7` XML namespace/schema), the file's OS modification
timestamp exactly matching its own internal `Date="2026-04-30"`
attribute, 19 distinct non-duplicate `Weakness` entries sampled with
zero structural anomalies, and a well-formed closing tag. See TD #21.

**Phase 5 Milestone 4 — retrieval integrated into `AnalysisService`.**
Narrower reconstruction than Milestone 3 — the changed module
(`analysis_service.py`) and its test now also need `ai_provider_port.py`
and `domain/shared/enums.py` (both already-stable dependencies,
reconstructed verbatim, zero changes) alongside the existing
`embedding_port.py`/`vector_store_port.py`. **`pytest tests/unit/
test_analysis_service.py -v` → 33 passed, 0 failed** (13 pre-existing
Milestone 6 tests, re-run unchanged and still passing — confirming no
regression — plus 20 new: retrieval query construction (title+
description, title-only, deterministic), context formatting (multi-
match join, missing-payload skip, empty case), embedding invocation
(query text passed to `embed()`), vector-store search invocation
(embedded vector + configured/default `limit` passed to `search()`),
prompt injection (retrieved text present and clearly labeled "not an
instruction"/"background information only", existing finding fields
still present alongside it, no context section when zero matches, an
exact byte-for-byte regression check that the unconfigured-ports prompt
is unchanged from pre-Milestone-4), provenance metadata (cwe_ids/
scores/embedding_model/corpus/corpus_version/limit present and
correct, provider/model provenance preserved alongside it, no raw
vector anywhere in the serialized metadata), empty-results behavior
(analysis still succeeds, no retrieval key, no context section), and
embedding/vector-store failure behavior (both `EmbeddingError` and
`VectorStoreError` caught internally, analysis still succeeds with the
pre-Milestone-4 prompt, the AI provider is still called exactly once).
100% coverage on `analysis_service.py`. Ruff (lint + format) and MyPy
`--strict` both clean on the changed module and its test (19 source
files in the reconstructed package). Byte-count integrity check
(`get_file_info` vs. sandbox `wc -c`) confirmed an exact-match
transplant for both files (`analysis_service.py`: 16275 bytes;
`test_analysis_service.py`: 21670 bytes). **Not re-run this session:**
`test_run_scan_workflow.py` itself — `run_scan_workflow.py` required
zero code changes (confirmed by direct review: it only imports the
`PROMPT_VERSION` symbol and constructs `AnalysisService(provider=...)`,
both fully backward-compatible with the new optional parameters), and
reconstructing its full transitive dependency tree (six domain
packages, scanner_engine, every repository port) for a file this
session did not touch was judged disproportionate to the change's own
isolated footprint. The new optional-parameter default-`None` path
that file's harness exercises is directly covered by
`test_analyze_does_not_retrieve_when_ports_are_not_configured` and
`test_prompt_is_unchanged_from_pre_milestone_4_shape_when_not_configured`
above. No new image-size or mocked-only-verification-class finding
this session — no new dependency was added, and both ports involved
are simple fakes at the same tier already used for the AI provider
itself in this same test file.

**Phase 5 Milestone 5 — RAG wiring, CPU-only PyTorch, `kb_version`.**
A wider reconstruction than Milestone 4's own, and one that surfaced
real memory-drift risk this session actively guarded against: an
initial sandbox reconstruction of `run_scan_workflow.py` (attempted
from recollection to save a read) turned out to not match the real
file's actual `_PipelineItem`/`_run_step` structure. Caught before any
production edit was made, by re-fetching every file actually being
edited fresh, immediately before constructing each edit, rather than
trusting the sandbox copy — every real transplant this session is
verified against a same-turn fresh read, not the sandbox. **Sandbox
`pytest tests/unit/ -q` → 82 passed, 0 failed** (73 pre-existing
across Milestones 1-4, re-run unchanged and still passing, plus 9 new:
4 `kb_version` cases in `test_analysis_service.py` — populated as
`"cwe_top25:2025"` when matches exist, `None` for no-matches/
not-configured/malformed-payload — and 5 in the new
`test_tasks_composition.py`, isolating `tasks.py`'s new
`_build_analysis_service` helper: unconfigured returns `provider`-only
(byte-identical call shape), configured wires both adapters with the
correct `collection_name`/`vector_size` sourced from `CORPUS_NAME`/
`EMBEDDING_VECTOR_SIZE` rather than duplicated literals, and the RAG
adapter classes are never even imported on the unconfigured path).
100% coverage on `analysis_service.py`; `tasks.py` itself only 56%
(this session's own `_build_analysis_service` addition, not the rest of
that file — see below). Ruff (lint + format) and MyPy `--strict` both
clean across the reconstructed package (71 files). Self-referential
`rag` extras (`pip install -e ".[dev]"` pulling in
`security-platform-backend[rag]`) confirmed working in the sandbox
before being written to `pyproject.toml`.

**Real, explicitly bounded verification gaps, not glossed over:**
(1) **`test_scan_worker_task.py` (the full Postgres-backed integration
harness for `tasks.py`) was not re-run this session** — given the
memory-drift risk just described, faithfully reconstructing that
harness (and the ~25-file domain/infrastructure tree it needs) was
judged too failure-prone to trust as a pass/fail signal; the new,
narrower `test_tasks_composition.py` covers the actual new logic
directly instead, and every other file that harness exercises was
confirmed unchanged by direct diff review during the real edits (only
`_build_analysis_service` and its call site changed in `tasks.py`).
(2) **No real Qdrant server was reachable this session** (TD #20
unchanged — same network/topology constraints as Milestone 2). (3) **No
actual Docker build was run** — the Filesystem MCP has no
command-execution tool (§15) and Claude's own sandbox cannot build
Docker images; the `INSTALL_RAG_DEPENDENCIES` conditional-install/
CPU-only-torch-index Dockerfile logic is standard, well-documented
technique, not something confirmed working end-to-end here — see TD
#19's updated entry. (4) **The full real-repository test suite** (all
~48 unit + integration files) **was not run against the actual repo**
— no execution access exists against
`C:\Users\gamer\Downloads\claudeOnly` directly (§15); verification is
sandbox-only, as it has been for every session, but this session's
sandbox coverage of the untouched surrounding system is narrower than
some prior sessions' (e.g. the Nmap-wiring session's full-package
reconstruction) for the reasons in (1). Recommend a real `docker build`
and a full `pytest tests/ -q` run in an environment with execution
access (e.g. Claude Code) for final confirmation of both.

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
17. **`SqlmapAdapter` verified only against a patched
    `run_scanner_subprocess`** — no real `sqlmap` binary reachable in
    the verification environment. Same shape as TD #7 (`NucleiAdapter`)/
    TD #15 (`NmapAdapter`); resolved the same way when a real-binary
    CI/sandbox image exists.
18. **No `sqlmap-stdout` normalizer, and not a placeholder pending more
    parsing effort — a deliberate, explained gap, re-investigated once
    and re-confirmed, not just carried forward unexamined.** Unlike
    nmap/nuclei, sqlmap has no official machine-readable output mode for
    its injection-detection results (no `-oX`/`--format=json`
    equivalent; its real structured artifacts — the per-target session
    SQLite database, the per-target log file — live under
    `--output-dir` on disk, not on the single subprocess's stdout this
    codebase's adapters capture). Writing a normalizer would mean
    parsing sqlmap's human-readable progress/summary text well enough to
    assert confidence in specific injection techniques/payloads
    detected — explicitly rejected as fabricating parsed vulnerability
    data from an undocumented contract, per direct instruction, twice
    now. **Re-investigation session:** explicitly instructed to attempt
    implementation, with a directive to parse only what the repository/
    tests establish as reliable and to stop and document if the
    available output is insufficient, rather than fabricate. Searched
    the entire `tests/` tree (`search_files`/`directory_tree`, not just
    the files already known) for any real captured sqlmap output,
    fixture, or sample file — found none. The *only* sqlmap output
    content anywhere in this repository is `test_sqlmap_adapter.py`'s
    own `_SAMPLE_OUTPUT` constant, which is itself explicitly commented
    as "a plausible stand-in... not a byte-perfect reproduction of real
    sqlmap output" — i.e. the repository itself documents that nothing
    reliable exists to parse. Per that explicit instruction, no
    normalizer was written; no code was changed this session at all
    (confirmed via a fresh, genuine pytest/Ruff/MyPy-strict re-run on
    the unmodified codebase, not just citing the prior session's
    numbers — see §11). `SqlmapAdapter` itself remains complete and
    fully tested; `normalize_scan_output("sqlmap-stdout", ...)` still
    raises `UnsupportedScanOutputFormatError`, verified by the existing
    test. Not yet reachable via the public API at all (no pipeline/API
    wiring exists for `sqlmap` yet). Revisit only if a reliable,
    sqlmap-documented structured output mode is identified, or if real
    captured sqlmap output is added to this repository as a genuine
    fixture a parser could be built and verified against, or if a
    deliberately conservative, explicitly-fragility-flagged text parser
    is later judged worth the risk by a human decision — not by
    guessing at stdout formatting from memory a third time.
19. ~~**`sentence-transformers`'s default PyPI install pulls the full
    CUDA-enabled `torch` build, not a CPU-only one.**~~ — **addressed in
    code/config, Phase 5 Milestone 5; not confirmed by an actual Docker
    build.** Originally confirmed in Claude's sandbox (Milestone 1):
    5.8GB installed, 3.2GB of it pure NVIDIA/CUDA packages, for an
    adapter that is CPU-only by design (§4) and never touches a GPU.
    Milestone 5's fix: `backend/pyproject.toml` moved
    `sentence-transformers`/`qdrant-client` into a new `rag`
    optional-dependency group (out of the base `dependencies` every
    image installs); `backend/Dockerfile` gained an
    `INSTALL_RAG_DEPENDENCIES` build arg (default `false`) that, when
    `"true"`, installs `torch` from `https://download.pytorch.org/whl/
    cpu` before `pip install ".[rag]"`; `docker-compose.yml`'s `worker`
    service passes that arg, `backend` does not — so `backend`'s image
    gets neither the RAG stack nor the CUDA bloat, and `worker`'s gets
    the RAG stack via the CPU-only wheel index specifically. Standard,
    well-documented PyTorch technique, not invented this session — but
    genuinely **not verified by an actual `docker build`**: the
    Filesystem MCP has no command-execution tool (§15), Claude's sandbox
    cannot build Docker images, and `download.pytorch.org` remains
    outside Claude's own sandbox network allowlist regardless (the same
    limitation noted when this item was first opened). Run a real
    `docker build --build-arg INSTALL_RAG_DEPENDENCIES=true` for
    `worker` and inspect the resulting image size/layers to close this
    out for real.
20. **`QdrantVectorStorePort` verified only against a mocked
    `AsyncQdrantClient` — no real Qdrant server reachable in the
    verification environment.** Same shape as TD #6 (`MinioStoragePort`)/
    TD #7 (`NucleiAdapter`); resolved the same way when a real-server
    CI/sandbox environment exists. Distinct from those two in one way:
    even a running local Qdrant wouldn't have helped here regardless —
    `docker-compose.yml`'s `qdrant` service has no host port mapping,
    so it is reachable only from other containers on the `queue`
    network, never from Claude's sandbox.
21. **The vendored `backend/data/cwe/2025_top25.xml`'s exact weakness
    count (25) was not confirmed by full manual enumeration — only by
    strong partial evidence plus the parser's own runtime assertion.**
    See §11 for exactly what was and wasn't directly read this session
    (19/25 distinct entries sampled, zero anomalies, correct root/
    schema/namespace/mtime). `parse_cwe_top25_xml` asserts
    `len(weaknesses) == 25` at parse time and raises `CweSourceError`
    otherwise — so a genuine mismatch will fail loudly the first time
    `IngestCweTop25UseCase` actually runs against the real file, not
    silently proceed with a wrong corpus. Resolved the moment Milestone
    3's use case is actually run once (in a real environment, or a
    future session with a way to read the whole file directly) — not a
    design gap, a one-time manual-verification gap under this session's
    own tool constraints.

## 13. Deferred / excluded work

- Findings/Assets/Reporting application-layer use cases and HTTP routes.
- `EventBusPort` and its implementation; `FindingCreated` publication
  (planned, no port yet).
- Remaining Phase 4 scanner adapters beyond Nuclei/Nmap/SQLMap: the
  repository does not establish an authoritative complete roster — the
  previously-cited "14 total" figure (and this file's own derived
  "12 remaining") had no named source anywhere in the repository and
  is no longer treated as reliable, corrected this session — see
  `docs/implementation_progress.md`'s dated correction and
  `docs/session_state.md`. Concretely scaffolded but unimplemented:
  `burp`/`zap` (`ImportScanner`-shaped stub folders, no `execute()`/no
  logic yet, see §5). Nmap now done and fully wired — selection,
  execution, and normalization — into the pipeline/API, see §7/§11/
  TD #15/TD #16; sqlmap's adapter is done but not yet wired into the
  pipeline/API and has no normalizer, see §7/§11/TD #17/TD #18. ReconX
  and BugHunter PRO (Madhav's own separate, earlier projects) are
  permanently excluded from this project's scanner roster by explicit
  decision — not reused, ported, reconstructed, or referenced going
  forward; see §5 for their stub folders' status.
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
- Qdrant/RAG integration (Phase 5) — `EmbeddingPort` +
  `sentence-transformers` adapter (Milestone 1), `VectorStorePort` +
  Qdrant adapter (Milestone 2), CWE Top 25 ingestion (Milestone 3),
  `AnalysisService` retrieval integration (Milestone 4), and
  application wiring + CPU-only-PyTorch image split + `kb_version`
  (Milestone 5) now implemented, see §7/§11/§12 TD #19/#20/#21. Wired
  into `ingestion_worker`'s composition root as of Milestone 5,
  conditional on `QDRANT_URL` being set (`app/main.py`/the API process
  still never constructs either adapter). Not yet confirmed against a
  real running Qdrant service or an actual Docker build — see §11.
  Nothing further is deferred for Phase 5 as originally scoped; any
  additional Phase 5 work (a real-environment verification pass, a
  second knowledge source, retrieval tuning, etc.) is new scope, not a
  carry-over.
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

**Fourth out-of-sequence session (explicit instruction, after a
clarifying question about which of the five remaining Phase 4 stubs to
build next — burp/zap turned out to be `ImportScanner`-shaped, a
different architecture; the other two then-remaining stub candidates
had no available spec in this repo (permanently excluded from this
project's scanner roster by a later explicit decision — see §13);
sqlmap was the only remaining stub matching Nmap/Nuclei's
`ActiveScanner` pattern): Phase 4 — SQLMap scanner adapter — complete,
adapter-only, see §7/§11/TD #17/TD #18.** Mirrors Nmap's own first
session's scope exactly: `SqlmapAdapter` only, no pipeline/API wiring,
no `ScannerPort`/registry/factory changes, no other adapter touched.
The one design question this session had to resolve carefully —
sqlmap has no documented machine-readable output contract the way
nuclei/nmap do — was resolved per explicit instruction: rather than
inventing an undocumented contract or fabricating a fragile text parser
from memory, the adapter captures sqlmap's stdout honestly (tagged
`"sqlmap-stdout"`) and **deliberately has no normalizer** (TD #18,
verified by a real test that `UnsupportedScanOutputFormatError` is
still raised, not just documented) — the same "preserve the existing
model rather than fabricate" choice the instructions explicitly
authorized. Nothing here changes Step 5's own status — still awaiting
a scope decision, unaffected by this session.

**Fifth out-of-sequence session (explicit instruction: attempt TD #18
directly, with the same "stop and document rather than fabricate"
safety valve made explicit up front): `sqlmap-stdout` normalizer —
re-investigated, re-confirmed unresolved, see §7/§11/TD #18.** Not a
repeat of the prior session's conclusion without checking — a fresh
search of the entire `tests/` tree for any real captured sqlmap output
or fixture, finding none; the only sqlmap output content anywhere in
this repository remains `test_sqlmap_adapter.py`'s own `_SAMPLE_OUTPUT`,
which the repository itself documents as fabricated, not real. Per
direct instruction to parse only what the repository/tests establish as
reliable, and given nothing is established as reliable, **zero files
were modified this session** — no normalizer, no adapter change, no
test change. TD #18's own entry (§12) was rewritten to record precisely
what this session checked, so a future session (or Madhav) can see this
was genuinely re-examined, not just left stale. Nothing here changes
Step 5's own status — still awaiting a scope decision, unaffected by
this session.

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
a future SQLMap pipeline/API wiring session (mirroring Nmap's own
second session — TD #18 would still block full completion even once
wired), remaining Phase 4 scanner adapters (burp/zap — §13; reconx/
bughunter are permanently excluded from this project's roster, not
remaining work).

**Sixth out-of-sequence session (explicit instruction): documentation/
roadmap cleanup only — no scanner implemented, no source-code change,
see §5/§7/§13.** Two corrections, both documentation-only: (1) ReconX
and BugHunter PRO (Madhav's own separate, earlier projects) permanently
excluded from this project's scanner roster — removed from the
roster/lists/counts/TD-references/roadmap text in this file,
`docs/session_state.md`, and `docs/implementation_progress.md`
wherever they appeared as remaining/future work. Their two now-orphaned
stub adapter folders (`scanner_engine/adapters/{reconx,bughunter}/`)
were confirmed unused — no imports, no tests, no wiring anywhere in
this repository — and safe to delete, but could not actually be removed
this session, since the Filesystem MCP connector exposes no
delete/rmdir tool (see §15); flagged for manual deletion or a future
session with delete capability. (2) The unsupported "remaining fourteen
scanner adapters" claim in `docs/implementation_progress.md` (and this
file's own derived "12 remaining") had no named source anywhere in the
repository; replaced throughout with an accurate statement naming only
what the repository actually establishes — implemented (`nuclei`,
`nmap`, `sqlmap`), scaffolded-but-unimplemented `ImportScanner`-shaped
stubs (`burp`, `zap`), and an explicit acknowledgment that no complete
Phase 4 roster is enumerated in this repository, rather than a
fabricated count. No scanner code, registry, or factory was added or
changed. Nothing here changes Step 5's own status, TD #18, or any other
open item — all remain exactly where the fifth session left them.

**Seventh out-of-sequence session (explicit instruction): Phase 5
planning + Milestone 1 — `EmbeddingPort` + `sentence-transformers`
adapter — complete, see §4/§7/§11/§12 TD #19.** A planning-only
recon session first resolved two locked decisions (§4): `EmbeddingPort`
is separate from `AIProviderPort`; concrete adapter is local
`sentence-transformers` (`all-MiniLM-L6-v2`, CPU-only), belonging to
`ingestion_worker`; initial knowledge corpus is CWE Top 25 (ingestion
deferred). This session then implemented Milestone 1 only: the port +
adapter + unit tests (8 new, `SentenceTransformer` fully mocked) +
the one new dependency. No Qdrant, no `VectorStorePort`, no ingestion,
no CWE data, no `AnalysisService`/retrieval-logic change — all
explicitly out of scope per instruction. One genuine, previously
unquantified consequence surfaced by sandbox verification, not fixed
this session: `sentence-transformers`'s default PyPI install pulls the
full CUDA-enabled `torch` build (5.8GB, 3.2GB pure CUDA) even though
this adapter is CPU-only — TD #19, needs a real decision before
Milestone 5 (Docker wiring). **Stopped after Milestone 1 per explicit
instruction — awaiting approval before Milestone 2**
(`VectorStorePort` + Qdrant adapter). Nothing here changes Step 5's own
status, TD #18, or any other open item.

**Eighth out-of-sequence session (explicit instruction): Phase 5
Milestone 2 — `VectorStorePort` + Qdrant adapter — complete, see
§4/§7/§11/§12 TD #20.** Read the current repo state fresh rather than
relying on the seventh session's own account (confirmed it matched
exactly — no drift). Implemented the port (`ensure_collection`,
`upsert`, `search` only — mirrors `StoragePort`'s one-instance-per-
bucket binding rule) and the Qdrant adapter (native `AsyncQdrantClient`,
cosine distance default, UUID/uint point-id constraint documented not
enforced pre-emptively) + 9 new unit tests (`AsyncQdrantClient` fully
mocked) + the one new dependency. Every design choice needed for the
port contract (async-native vs. `to_thread`, distance-metric default,
point-id format, ensure-before-upsert-but-not-before-search) was
derivable from existing convention or an SDK-imposed constraint — none
required stopping to ask. No CWE ingestion, no CWE data, no
`EmbeddingPort`/`AIProviderPort`/`AnalysisService` change, no retrieval
wiring, no Docker networking change, no refresh/update mechanism, no
registry/framework abstraction — all explicitly out of scope per
instruction. TD #19 (CPU-only PyTorch footprint) was left exactly as-is
per instruction — not addressed, not referenced as blocking this
milestone. One new, honest verification-tier gap recorded, not fixed:
TD #20 (`QdrantVectorStorePort` verified only against a mock — no real
Qdrant server reachable in this environment; the `docker-compose.yml`
`qdrant` service has no host port mapping in any case). **Stopped after
Milestone 2 per explicit instruction — awaiting approval before
Milestone 3** (CWE Top 25 ingestion). Nothing here changes Step 5's own
status, TD #18, or any other open item.

**Ninth out-of-sequence session (explicit instruction): Phase 5
Milestone 3 — CWE Top 25 ingestion use case — complete, see
§4/§5/§7/§11/§12 TD #21.** Began with a genuine blocker, reported
rather than routed around: no reachable, verifiable, current source
for the actual CWE Top 25 data existed from Claude's sandbox or this
repository (`cwe.mitre.org`/`cisa.gov` both hard-blocked by the
sandbox's network allowlist, `x-deny-reason: host_not_allowed`; the
only candidate PyPI package was a stale 2020 pickle with no Top-25
curation; nothing in the repository itself). Per explicit instruction
not to substitute or reconstruct from memory, implementation did not
begin until Madhav resolved the blocker by placing the vendored file
directly in the repository and locking the corpus to the 2025 edition/
View-1435. Located it (`backend/data/cwe/2025_top25.xml`), inspected
its structure directly (official MITRE `cwe-7` XML schema/namespace,
root self-declared as "VIEW LIST: CWE-1435: ... 2025 CWE Top 25",
mtime matching its own internal `Date` attribute, 19/25 distinct
entries sampled with zero anomalies — see §11/TD #21 for exactly what
could and couldn't be confirmed under this session's own tool
constraints), then implemented Milestone 3 only:
`app/application/knowledge/ingest_cwe_top25.py` — three independently-
tested pure functions (`parse_cwe_top25_xml`, `build_cwe_chunk`,
`cwe_point_id`) plus `IngestCweTop25UseCase` (constructor-injected
`EmbeddingPort`/`VectorStorePort`, mirrors `TriggerScanUseCase`'s
convention), 23 new unit tests (both ports faked directly, no real
model/file/server), no new dependency (stdlib `xml.etree.ElementTree`
only). Deliberately kept independent of `AnalysisService` — new
`app/application/knowledge/` folder, not `app/ai_agents/`. No CWE
ingestion of a wrong/substituted corpus, no retrieval, no Qdrant search
beyond what Milestone 2 already built, no `EmbeddingPort`/
`VectorStorePort` contract change (none was needed), no refresh/update
scheduling, no Docker wiring — all explicitly out of scope, stopped
after Milestone 3 per instruction. TD #19/#20 left exactly as-is per
instruction. One new, honestly-disclosed verification gap: TD #21 (the
real file's exact 25-entry count wasn't manually exhaustively counted
this session, due to a Filesystem MCP per-call response-size ceiling
well under the file's own size — closed defensively by the parser's
own `len(weaknesses) == 25` runtime assertion, not silently assumed).
**Stopped after Milestone 3 per explicit instruction — awaiting
approval before Milestone 4** (`AnalysisService` retrieval
integration). Nothing here changes Step 5's own status, TD #18, or any
other open item.

**Tenth out-of-sequence session (explicit instruction): Phase 5
Milestone 4 — retrieval integrated into `AnalysisService` — complete,
see §4/§5/§7/§11.** Inspected `AnalysisService`, `RunScanWorkflowUseCase`,
`FindingAnalysis`, `EmbeddingPort`, `VectorStorePort`, and their
existing tests fresh before editing, per instruction. A genuine gift
found during inspection, not invented: `FindingAnalysis` already has
its own dedicated `kb_version: str | None` column, migrated since
Milestone 2 with a docstring reading "nullable -- no RAG until Phase
5" -- confirming no new persistence model was needed, exactly as
instructed; `model_metadata` (the field the instruction specifically
named) carries the new `"retrieval"` provenance, `kb_version` was
deliberately left unpopulated this session (see §4's own note -- a
future-session decision, not a silent one). Every design choice needed
(optional-DI shape mirroring `AIProviderPort`'s own convention,
retrieval living inside `AnalysisService` rather than
`RunScanWorkflowUseCase`, retrieval failures degrading to pre-
Milestone-4 behavior rather than propagating) was derivable from
existing convention -- none required stopping to report a blocker.
Implemented: optional `embedding_port`/`vector_store`/`retrieval_limit`
on `AnalysisService.__init__`; a retrieval query built from the
finding's own title/description; embed → search → format → inject as
a clearly-labeled "reference context, not instruction" prompt section;
`PROMPT_VERSION` bumped to `"v2"`; provenance in `model_metadata
["retrieval"]` (cwe_ids/scores/embedding_model/corpus/corpus_version/
limit, never a raw vector) added only when there was at least one
match; 20 new unit tests. No `RAGService`/`RetrieverBase`/registry, no
`AIProviderPort` extension, no Qdrant adapter change, no new
persistence model, no scanner/CWE-ingestion/Docker/TD #19/TD #20
change -- all explicitly out of scope, stopped after Milestone 4 per
instruction. `run_scan_workflow.py` required zero changes -- confirmed
by direct review, not just by claim -- so its own test file was not
re-run this session (see §11 for exactly why that judgment call was
made and what still covers the relevant path). **Stopped after
Milestone 4 per explicit instruction -- awaiting approval before
Milestone 5** (wiring: constructing `AnalysisService` with real
retrieval ports in `app/main.py`/`app/workers/tasks.py`, and TD #19's
Docker-image decision). Nothing here changes Step 5's own status, TD
#18, or any other open item.

**Eleventh out-of-sequence session (explicit instruction): Phase 5
Milestone 5 — RAG wiring, CPU-only-PyTorch image split, `kb_version`
— complete (with explicitly bounded verification gaps), see
§4/§5/§7/§10/§11/§12 TD #19/#20.** Inspected `main.py`, worker
composition/Celery tasks, `Settings`/role-boundary validation,
Dockerfile/docker-compose, the existing Anthropic wiring, Qdrant
config, `AnalysisService` construction, and current Phase 5/
integration test conventions fresh before editing, per instruction.
Caught and corrected a real memory-drift risk mid-session: an initial
sandbox reconstruction of `run_scan_workflow.py`, attempted from
recollection, did not match the real file's actual structure. Every
subsequent production edit was made only after a same-turn fresh read
of the exact file being changed, never from the sandbox copy — see
§11 for the full account.

Implemented: `tasks.py`'s new `_build_analysis_service(*, provider,
qdrant_url)`, lazily importing `SentenceTransformerEmbeddingPort`/
`QdrantVectorStorePort` only when configured, so `backend`'s image
never needs to import either; unconfigured call shape byte-identical
to pre-Milestone-5. `qdrant_url` confirmed deliberately unvalidated in
`check_role_boundaries` (preserves Milestone 4's graceful-degradation
design) and usage-gated to `ingestion_worker` alone, documented in
place. `EMBEDDING_VECTOR_SIZE = 384` added to
`sentence_transformer_provider.py` as the single source of truth for
the vector store's dimensioning. `kb_version` inspected (ORM mapping,
repository round-trip) and confirmed as the intended persisted
representation, not assumed -- populated via a new `_build_kb_version`
helper, threaded through `FindingAnalysisResult` and `_persist`. TD
#19 addressed in code/config: `pyproject.toml`'s new `rag` optional
group (self-referenced from `dev`), `Dockerfile`'s
`INSTALL_RAG_DEPENDENCIES` arg installing CPU-only `torch` first,
`docker-compose.yml`'s `worker` (not `backend`) opting in plus gaining
`QDRANT_URL` and a `qdrant` `depends_on` entry -- determined the
existing single-Dockerfile-plus-optional-dependency-groups
architecture already supported this split cleanly, so no larger
deployment refactor was attempted, per instruction. 9 new unit tests.
No new knowledge source, no corpus/embedding-model change, no
`EmbeddingPort`/`VectorStorePort` redesign, no scanner behavior change,
no new retrieval features, no scheduled refresh, no security/network-
boundary weakening, no `secure=True` cookie change, no revived
scanners — all explicitly out of scope, stopped after Milestone 5 per
instruction.

**Explicitly not claimed as verified, per instruction:** real Qdrant
integration (TD #20, unchanged -- no reachable server this session
either) and an actual Docker build (TD #19's updated entry -- no
execution access to build one). Both are standard, well-reasoned
designs, not confirmed working end to end. **Stopped after Milestone 5
per explicit instruction -- no Phase 6 or further work begun.** Nothing
here changes Step 5's own status, TD #18, or any other open item.
