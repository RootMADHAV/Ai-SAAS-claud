"""The Celery task that actually runs the scan pipeline off the HTTP
request thread -- Milestone 7's core deliverable (PROJECT_STATE.md
section 15; Technical debt item #10 in docs/implementation_progress.md).

Three layers, deliberately kept separate so this module is testable at
the same tier every other use case in this codebase already is (fully
faked, against real Postgres, per PROJECT_STATE.md section 11):

  1. :func:`execute_scan_workflow` -- a plain, fully-injectable async
     function. Takes every dependency ``RunScanWorkflowUseCase`` needs as
     an explicit parameter (a session factory plus the three Milestone 3/6
     adapters) and does nothing more than open one RLS-scoped session
     (``session_scoped_to_org``, exactly as ``app/api/dependencies.py``'s
     ``get_org_session`` already does for the API process) and call
     ``RunScanWorkflowUseCase.execute()``. No ``Settings``, no Celery, no
     real adapter construction -- this is the function
     ``tests/integration/test_scan_worker_task.py`` calls directly with
     fakes, against a real test database, mirroring
     ``tests/integration/test_scan_pipeline_orchestrator.py``'s existing
     pattern for the use case itself.
  2. :func:`_run_scan_workflow_from_settings` -- the worker-specific
     composition root. Builds the real engine and the three adapters from
     ``Settings`` (mirroring ``app/main.py``'s ``_lifespan``, but for
     ``worker_role=ingestion_worker`` instead of ``worker_role=api`` --
     see that module's docstring for why the API process no longer builds
     these itself as of this milestone) and calls layer 1. This is the
     layer that is hard to unit-test in isolation (it needs real
     Settings/credentials) and is not unit-tested directly for that
     reason -- the same judgment call already made for ``_lifespan``
     itself, which ``tests/integration/test_main_lifespan.py`` verifies
     by actually running it, not by re-implementing its logic in a test.
  3. :data:`run_scan_workflow_task` -- the actual
     ``@celery_app.task``-decorated entrypoint. Celery tasks are
     ordinary synchronous callables; this one's entire body is
     ``asyncio.run(...)`` around layer 2.

A fresh ``AsyncEngine`` is constructed *inside* the same
``asyncio.run()`` call that drives one task invocation, and disposed
before that call returns -- never cached at module scope across
invocations. This is not a style preference:
``asyncio.run()`` creates a new event loop every call, and an asyncpg
connection pool is bound to the loop it was created in, so reusing one
engine across task invocations would break the moment two tasks ran in
the same worker process, one after another -- the exact
``another operation is in progress``-class failure this codebase already
documented and deliberately avoided for exactly this reason in
``tests/conftest.py``'s ``engine`` fixture (function-scoped there for the
same underlying cause). The accepted cost -- one new connection pool per
task invocation -- is deliberate, not an oversight: matching
``tests/conftest.py``'s own stated reasoning ("the cost is one new
connection pool per test, which is negligible at this suite's size"),
scaled up to "per scan run" here, where a fresh pool is a small, one-time
cost relative to the minutes-long scanner subprocess and AI-provider
calls the task spends most of its time waiting on.

Deliberately out of scope for this milestone -- see PROJECT_STATE.md
section 3's Milestone 7 entry and Technical debt item #12 in
docs/implementation_progress.md for the full account:
  - No separate, network-isolated ``scanner_worker`` process. This
    module wires exactly one worker role, ``ingestion_worker`` (already
    defined in ``app/config.py`` since Milestone 1), running the
    complete, unrestructured ``RunScanWorkflowUseCase`` -- including its
    own call to the real scanner adapter. Splitting ``EXECUTE_SCANNER``
    into its own DB-credential-free hop is real future work, not
    attempted here; see ``run_scan_workflow.py``'s own module docstring,
    which already anticipated exactly this deferral.
  - No Celery ``autoretry_for``/``max_retries`` policy. A failed task
    (a genuine bug, not an ordinary scan failure -- see below) is
    reported to Celery as a task failure and left there; automatic
    retry policy is a deliberate scope boundary for this milestone, not
    an oversight -- the resumability story this codebase already has
    (re-``POST .../run`` retries a failed scan from its failed step,
    per ``RunScanWorkflowUseCase``'s own docstring) already gives a
    human or a future scheduled job a correct way to retry, without
    needing Celery's own retry machinery layered on top of it.

Phase 5 Milestone 5: ``_run_scan_workflow_from_settings`` (layer 2)
conditionally constructs ``SentenceTransformerEmbeddingPort``/
``QdrantVectorStorePort`` and wires them into ``AnalysisService``,
alongside the existing ``AnthropicProvider`` -- via a small
``_build_analysis_service`` helper, so the ``assert settings.
anthropic_api_key is not None`` mypy-narrowing pattern below stays
effective (see that helper's own docstring). Both are imported
*lazily*, inside ``_build_analysis_service``'s own RAG-configured
branch, not at this module's own top -- this module is imported by
``app/api/dependencies.py`` (and therefore ``app/main.py``, the API
process) purely to get the ``run_scan_workflow_task`` Celery task
*object* so a route can call ``.delay(...)`` on it; the API process
never executes this module's own composition-root function bodies. A
top-level import here would force the API process's own image to have
``sentence-transformers``/``qdrant-client`` installed too, just to
import a task definition whose body it never runs (exactly the
"unnecessarily in the API process" outcome Milestone 5's own
instructions rule out). ``backend/pyproject.toml``'s new ``rag``
optional-dependency group and ``backend/Dockerfile``'s
``INSTALL_RAG_DEPENDENCIES`` build arg are what actually keep these
packages out of the ``backend`` image; this lazy import is necessary
but not sufficient on its own.

When ``settings.qdrant_url`` is ``None`` (unset), ``AnalysisService`` is
constructed exactly as it was before this milestone --
``AnalysisService(provider=provider)``, the identical call shape -- so
a deployment that has not configured Qdrant behaves exactly as it did
pre-Phase-5: retrieval was always optional-by-design (Milestone 4), and
this composition root preserves that all the way out to "not configured
at all is a fully supported, unchanged configuration."
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from app.ai_agents.analysis_service import AnalysisService
from app.application.interfaces.ai_provider_port import AIProviderPort
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.storage_port import StoragePort
from app.application.knowledge.ingest_cwe_top25 import CORPUS_NAME
from app.application.scanning.run_scan_workflow import RunScanWorkflowUseCase, ScannerMismatchError
from app.config import get_settings
from app.infrastructure.ai_providers.anthropic_provider import AnthropicProvider
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.infrastructure.db.session import (
    create_engine,
    create_session_factory,
    session_scoped_to_org,
)
from app.infrastructure.storage.minio_storage import MinioStoragePort
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter
from app.workers.celery_app import celery_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def execute_scan_workflow(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    active_scanner: ActiveScanner,
    storage: StoragePort,
    analysis_service: AnalysisService,
    organization_id: UUID,
    scan_id: UUID,
) -> None:
    """Open one RLS-scoped session for ``organization_id`` and run
    ``RunScanWorkflowUseCase.execute(scan_id)`` against it -- every
    dependency is passed in explicitly, so a test can supply fakes for
    ``active_scanner``/``storage``/``analysis_service`` and a real
    Postgres-backed ``session_factory``, exactly mirroring
    ``tests/integration/test_scan_pipeline_orchestrator.py``'s existing
    fixture for the use case this function wraps.
    """
    async with session_scoped_to_org(session_factory, organization_id) as session:
        use_case = RunScanWorkflowUseCase(
            scan_repository=SqlAlchemyScanRepository(session),
            asset_repository=SqlAlchemyAssetRepository(session),
            finding_repository=SqlAlchemyFindingRepository(session),
            active_scanner=active_scanner,
            storage=storage,
            analysis_service=analysis_service,
        )
        await use_case.execute(scan_id)


def _select_active_scanner(scanner_name: str) -> ActiveScanner:
    """Picks the one concrete ``ActiveScanner`` this worker constructs
    for ``scanner_name`` -- an explicit if/elif over the exactly two
    adapters this process wires (nuclei, nmap; Phase 4 added the
    second), not a scanner registry: there is no registration API, no
    pluggable/dynamic dispatch, and no abstraction beyond "which one of
    these two known, concrete classes do we build." Mirrors this
    module's own existing fail-fast style a few lines below (the
    ``ai_default_provider`` check) -- an unrecognized value is a
    genuine configuration/data problem (a ``Scan`` row naming a
    scanner_name this deployment has no adapter for -- normally already
    rejected at the API layer, see ``app/api/v1/schemas.py``'s
    ``Literal`` and ``app/api/v1/scans.py``'s own pre-dispatch check,
    but this worker does not re-trust that the caller was this
    codebase's own API process), not something to guess at.

    Reuses ``ScannerMismatchError`` for that unrecognized-name case
    (rather than a new exception type) since it is the same category of
    problem ``RunScanWorkflowUseCase`` already names that error for:
    "this deployment has no adapter for the scanner_name a Scan names."
    """
    if scanner_name == "nuclei":
        return NucleiAdapter()
    if scanner_name == "nmap":
        return NmapAdapter()
    raise ScannerMismatchError(
        f"no adapter wired for scanner_name={scanner_name!r} -- this worker is wired to "
        "'nuclei' and 'nmap' only"
    )


def _build_analysis_service(*, provider: AIProviderPort, qdrant_url: str | None) -> AnalysisService:
    """Constructs ``AnalysisService``, with CWE retrieval wired in only
    when ``qdrant_url`` is configured -- see this module's own docstring
    on the lazy imports below and why an unconfigured Qdrant URL must
    produce the exact same ``AnalysisService(provider=provider)`` call
    shape this composition root always made before Milestone 5.

    Takes an already-constructed ``provider`` rather than building
    ``AnthropicProvider`` itself, so the ``assert settings.
    anthropic_api_key is not None`` a few lines above in
    ``_run_scan_workflow_from_settings`` (mypy strict narrowing, per
    this module's own existing comment on that pattern) stays effective
    in the scope that needs it -- mypy does not carry an ``assert``'s
    narrowing across a function call.
    """
    if qdrant_url is None:
        return AnalysisService(provider=provider)

    from app.infrastructure.embeddings.sentence_transformer_provider import (
        EMBEDDING_VECTOR_SIZE,
        SentenceTransformerEmbeddingPort,
    )
    from app.infrastructure.vector_store.qdrant_vector_store import QdrantVectorStorePort

    return AnalysisService(
        provider=provider,
        embedding_port=SentenceTransformerEmbeddingPort(),
        vector_store=QdrantVectorStorePort(
            url=qdrant_url,
            collection_name=CORPUS_NAME,
            vector_size=EMBEDDING_VECTOR_SIZE,
        ),
    )


async def _run_scan_workflow_from_settings(
    organization_id: UUID, scan_id: UUID, scanner_name: str
) -> None:
    """The worker-specific composition root -- mirrors ``app/main.py``'s
    ``_lifespan`` exactly, but for ``worker_role=ingestion_worker``
    (this process's own role, not the API's) and built fresh per task
    invocation rather than once at process startup, per this module's
    docstring on why an engine/session factory cannot be cached across
    ``asyncio.run()`` calls.

    ``scanner_name`` (Phase 4 addition): selects which concrete
    ``ActiveScanner`` to construct via ``_select_active_scanner`` above
    -- passed in by the caller (ultimately ``app/api/v1/scans.py``'s
    ``run_scan`` route, which already has the ``Scan``'s own
    ``scanner_name`` in hand at dispatch time) rather than re-fetched
    from the database here, so this function's own shape -- and
    ``execute_scan_workflow``'s, which it calls -- stays exactly as
    fully-injectable/fakeable as before this change; only the *value*
    threaded through is new.
    """
    settings = get_settings()
    # Settings.check_role_boundaries (app/config.py) already guarantees
    # database_url/minio_*/anthropic_api_key (when ai_default_provider is
    # left at its "anthropic" default) are not None for
    # worker_role=ingestion_worker -- a genuinely missing value already
    # raised at Settings() construction, before this function ever runs.
    # These asserts exist for mypy strict's benefit, matching the
    # identical pattern already used in app/main.py's _lifespan.
    assert settings.database_url is not None
    assert settings.minio_endpoint is not None
    assert settings.minio_root_user is not None
    assert settings.minio_root_password is not None
    assert settings.minio_bucket is not None

    if settings.ai_default_provider != "anthropic":
        # Same fail-fast check app/main.py's _lifespan already makes for
        # the API process -- AnthropicProvider is the only AIProviderPort
        # implementation this codebase has (see run_scan_workflow.py's
        # module docstring), and this worker is the process that
        # actually constructs and calls it as of this milestone.
        raise ValueError(
            f"ai_default_provider={settings.ai_default_provider!r} has no adapter wired "
            "as of Milestone 6 -- only 'anthropic' does"
        )
    assert settings.anthropic_api_key is not None
    provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.ai_model)
    analysis_service = _build_analysis_service(provider=provider, qdrant_url=settings.qdrant_url)

    engine = create_engine(settings)
    try:
        await execute_scan_workflow(
            session_factory=create_session_factory(engine),
            active_scanner=_select_active_scanner(scanner_name),
            storage=MinioStoragePort(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_root_user,
                secret_key=settings.minio_root_password,
                bucket=settings.minio_bucket,
            ),
            analysis_service=analysis_service,
            organization_id=organization_id,
            scan_id=scan_id,
        )
    finally:
        await engine.dispose()


@celery_app.task(name="scanning.run_scan_workflow")
def run_scan_workflow_task(organization_id: str, scan_id: str, scanner_name: str) -> None:
    """The actual Celery entrypoint, dispatched by
    ``app/api/v1/scans.py``'s ``run_scan`` route via
    ``app/api/dependencies.py``'s ``get_scan_dispatcher`` seam.

    ``organization_id``/``scan_id`` are plain strings, not ``UUID``,
    because Celery task arguments must be JSON-serializable for the
    default broker transport -- parsed back into ``UUID`` immediately,
    so nothing past this one line ever handles a raw string where a
    ``UUID`` belongs, matching this codebase's existing convention
    everywhere else. ``scanner_name`` (Phase 4 addition) is already a
    plain string and needs no such conversion; see
    ``_run_scan_workflow_from_settings``/``_select_active_scanner``
    above for what it selects.

    Any exception raised here (``LookupError``/``ScannerMismatchError``
    from a scan or organization that no longer exists by the time this
    task actually runs, or a genuine bug) propagates out of this
    function and is recorded by Celery as a failed task -- it is not
    caught and swallowed here. This is different from, and does not
    conflict with, how ``RunScanWorkflowUseCase.execute()`` itself
    already handles an *ordinary* scan failure (an unreachable target, a
    scanner crash): that is recorded on the scan's own
    ``ScanWorkflowStep`` rows and reflected in ``Scan.status``, and
    ``execute()`` returns normally rather than raising -- so a normal
    scan failure never reaches this function as an exception at all,
    only genuine misuse does (see ``run_scan_workflow.py``'s own
    docstring on this distinction).
    """
    asyncio.run(
        _run_scan_workflow_from_settings(UUID(organization_id), UUID(scan_id), scanner_name)
    )
