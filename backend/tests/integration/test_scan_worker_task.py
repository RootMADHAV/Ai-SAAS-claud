"""Integration tests for ``app/workers/tasks.py`` -- the Celery task
that runs ``RunScanWorkflowUseCase`` off the HTTP request thread
(Milestone 7; Technical debt item #10 in
docs/implementation_progress.md, now resolved).

Two tiers, mirroring the split already established between
``tests/integration/test_api_scans.py`` (the API layer, dispatch only)
and ``tests/integration/test_scan_pipeline_orchestrator.py`` (the use
case itself, against real Postgres):

  - :func:`test_execute_scan_workflow_...` calls
    :func:`app.workers.tasks.execute_scan_workflow` directly -- the
    plain, fully-injectable async function, with fake
    scanner/storage/AI-provider adapters and a real Postgres-backed
    session factory, exactly mirroring
    ``test_scan_pipeline_orchestrator.py``'s own fixture for
    ``RunScanWorkflowUseCase`` (unsurprising, since this function is a
    thin wrapper that opens one RLS-scoped session and constructs that
    exact use case -- see ``app/workers/tasks.py``'s module docstring).
    This is the tier that proves the *worker-side wiring* -- opening a
    session via ``session_scoped_to_org``, constructing the three
    SQLAlchemy repositories, constructing the use case -- is correct
    against a real database, not just correct in shape.
  - :func:`test_run_scan_workflow_task_...` and
    :func:`test_lifespan_rejects_an_unimplemented_ai_provider` exercise
    the worker's composition root
    (``_run_scan_workflow_from_settings``) and the Celery task itself
    (``run_scan_workflow_task``), the same tier
    ``tests/integration/test_main_lifespan.py`` already established for
    the API process's own composition root (``app/main.py``'s
    ``_lifespan``) -- needing real-shaped (if not real) credentials for
    the same reason that module's tests do.

No real Redis broker or Celery worker process is started anywhere in
this module: ``run_scan_workflow_task`` is called as a plain Python
function (Celery tasks remain ordinary callables outside of
``.delay()``/``.apply_async()``), and ``asyncio.run()`` inside it drives
the same real-Postgres-backed logic ``execute_scan_workflow`` already
proves correct -- calling the task function directly, rather than
through Celery's dispatch machinery, verifies this module's own code,
not Celery's, which is not this project's code to test.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.ai_agents.analysis_service import AnalysisService
from app.application.interfaces.ai_provider_port import AICompletionResult, AIProviderPort
from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.application.interfaces.storage_port import StorageObjectNotFoundError, StoragePort
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.config import get_settings
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import ScanStatus, WorkflowStepStatus
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.workers.tasks import (
    _run_scan_workflow_from_settings,
    execute_scan_workflow,
    run_scan_workflow_task,
)
from tests.integration.support import make_organization, set_org_context

pytestmark = pytest.mark.integration


class _FakeStorage(StoragePort):
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self._objects[key] = data

    async def get_object(self, key: str) -> bytes:
        if key not in self._objects:
            raise StorageObjectNotFoundError(key)
        return self._objects[key]

    async def delete_object(self, key: str) -> None:
        if key not in self._objects:
            raise StorageObjectNotFoundError(key)
        del self._objects[key]

    async def object_exists(self, key: str) -> bool:
        return key in self._objects


@dataclass
class _FakeActiveScanner(ActiveScanner):
    call_count: int = 0
    # Phase 4: overridable so this one fake class can stand in for
    # either wired adapter in a test (see
    # test_run_scan_workflow_task_selects_nmap_for_an_nmap_scoped_scan
    # below) -- defaults preserve every existing call site's behavior
    # unchanged (a nuclei-shaped fake, same as before this field
    # existed).
    name_override: str = "nuclei"
    output_format_override: str = "nuclei-jsonl"

    @property
    def name(self) -> str:
        return self.name_override

    @property
    def output_format(self) -> str:
        return self.output_format_override

    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        self.call_count += 1
        now = utcnow()
        # TD #16: produce output shaped like whatever output_format this
        # fake is configured to report, so a scan using the "nmap"
        # persona can now genuinely complete end-to-end through the real
        # nmap-xml normalizer, the same as the "nuclei" persona already
        # could through the real nuclei-jsonl one -- not just get far
        # enough to prove adapter selection and then fail at NORMALIZE.
        if self.output_format_override == "nmap-xml":
            raw = (
                '<?xml version="1.0"?>'
                "<nmaprun>"
                "<host>"
                '<status state="up"/>'
                '<address addr="93.184.216.34" addrtype="ipv4"/>'
                f'<hostnames><hostname name="{target}" type="user"/></hostnames>'
                "<ports>"
                '<port protocol="tcp" portid="80">'
                '<state state="open"/>'
                '<service name="http"/>'
                "</port>"
                "</ports>"
                "</host>"
                "</nmaprun>"
            ).encode()
        else:
            line = {
                "template-id": "worker-task-test",
                "info": {"name": "Worker Task Test Finding", "severity": "medium"},
                "host": target,
                "matched-at": target,
            }
            raw = json.dumps(line).encode("utf-8")
        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=raw,
            started_at=now,
            completed_at=now,
        )


@dataclass
class _FakeAIProviderPort(AIProviderPort):
    call_count: int = 0

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(self, *, system_prompt: str, user_prompt: str) -> AICompletionResult:
        self.call_count += 1
        return AICompletionResult(
            text='{"summary": "s", "severity": "medium", "remediation": "r"}',
            model="fake-model",
        )


async def _create_organization(engine: AsyncEngine) -> UUID:
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        organization = make_organization()
        await set_org_context(session, organization.id)
        await SqlAlchemyOrganizationRepository(session).add(organization)
        await session.commit()
    return organization.id


async def _create_scan(
    engine: AsyncEngine, organization_id: UUID, *, scanner_name: str = "nuclei"
) -> UUID:
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        await set_org_context(session, organization_id)
        scan = await TriggerScanUseCase(SqlAlchemyScanRepository(session)).execute(
            organization_id=organization_id, target="example.com", scanner_name=scanner_name
        )
        await session.commit()
    return scan.id


async def test_execute_scan_workflow_runs_the_pipeline_against_real_postgres(
    engine: AsyncEngine,
) -> None:
    """Proves the worker-side wiring in execute_scan_workflow (open one
    RLS-scoped session, construct the three real repositories, construct
    and call RunScanWorkflowUseCase) is correct against a real database
    -- mirroring test_scan_pipeline_orchestrator.py's own verification
    tier for the use case underneath, one layer up."""
    organization_id = await _create_organization(engine)
    scan_id = await _create_scan(engine, organization_id)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    scanner = _FakeActiveScanner()
    ai_provider = _FakeAIProviderPort()

    await execute_scan_workflow(
        session_factory=session_factory,
        active_scanner=scanner,
        storage=_FakeStorage(),
        analysis_service=AnalysisService(provider=ai_provider),
        organization_id=organization_id,
        scan_id=scan_id,
    )

    assert scanner.call_count == 1
    assert ai_provider.call_count == 1

    async with session_factory() as session:
        await set_org_context(session, organization_id)
        scan = await SqlAlchemyScanRepository(session).get_by_id(scan_id)
        assert scan is not None
        assert scan.status is ScanStatus.COMPLETED
        steps = await SqlAlchemyScanRepository(session).list_workflow_steps(scan_id)
        assert all(step.status is WorkflowStepStatus.COMPLETED for step in steps)


async def test_run_scan_workflow_task_completes_a_real_scan_end_to_end(
    postgres_available: bool,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the actual Celery-task entrypoint (called directly, not
    through Celery's own dispatch machinery -- see module docstring),
    including its composition root
    (_run_scan_workflow_from_settings), against real Postgres. The
    scanner/AI-provider adapters this composition root would normally
    construct for real (NucleiAdapter, AnthropicProvider) are not
    reachable in this environment (no nuclei binary, no real Anthropic
    credential -- the same constraint already documented for every
    other adapter-touching test in this project), so this test
    monkeypatches the two real-adapter classes _run_scan_workflow_from_settings
    imports with fakes, the same "fake exactly the thing with a real
    external dependency, nothing else" principle test_api_scans.py's own
    wired_app fixture already applies -- MinioStoragePort's real
    behavior is not faked here (it only constructs a client object and
    stores config -- see test_main_lifespan.py's own precedent for this
    exact reasoning -- so it does not need patching for this test to
    complete without a real MinIO server, only for the eventual
    put_object/get_object calls to work, which is why the *pipeline's*
    storage really does need a fake -- provided instead by patching
    MinioStoragePort itself to construct our in-memory fake).
    """
    if not postgres_available:
        pytest.skip("No PostgreSQL instance reachable for this task test")

    monkeypatch.setenv("WORKER_ROLE", "ingestion_worker")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "a-real-minio-password")
    monkeypatch.setenv("MINIO_BUCKET", "scan-raw-output")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-real-anthropic-key")
    get_settings.cache_clear()

    scanner = _FakeActiveScanner()
    ai_provider = _FakeAIProviderPort()
    storage = _FakeStorage()

    import app.workers.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "NucleiAdapter", lambda: scanner)
    monkeypatch.setattr(tasks_module, "MinioStoragePort", lambda **kwargs: storage)
    monkeypatch.setattr(
        tasks_module,
        "AnthropicProvider",
        lambda **kwargs: ai_provider,
    )
    monkeypatch.setattr(
        tasks_module,
        "AnalysisService",
        lambda provider: AnalysisService(provider=provider),
    )

    organization_id = await _create_organization(engine)
    scan_id = await _create_scan(engine, organization_id)

    try:
        # run_scan_workflow_task's own body is asyncio.run(...) --
        # correctly unable to nest inside this test's own already-running
        # event loop (pytest-asyncio wraps every `async def test_...` in
        # one), the same way it would correctly refuse in production if
        # ever called from an async context. Celery itself always calls
        # a task from a plain synchronous worker context with no loop
        # already running, so asyncio.to_thread here faithfully
        # reproduces that -- a fresh thread has no running loop of its
        # own for asyncio.run() to collide with.
        await asyncio.to_thread(
            run_scan_workflow_task, str(organization_id), str(scan_id), "nuclei"
        )
    finally:
        get_settings.cache_clear()

    assert scanner.call_count == 1
    assert ai_provider.call_count == 1

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        await set_org_context(session, organization_id)
        scan = await SqlAlchemyScanRepository(session).get_by_id(scan_id)
        assert scan is not None
        assert scan.status is ScanStatus.COMPLETED


async def test_run_scan_workflow_task_selects_nmap_for_an_nmap_scoped_scan(
    postgres_available: bool,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4: the worker-side counterpart to
    test_run_scan_workflow_task_completes_a_real_scan_end_to_end above,
    but for scanner_name="nmap" -- proves
    _run_scan_workflow_from_settings's _select_active_scanner actually
    picks NmapAdapter, not NucleiAdapter, when a scan asks for it.
    Monkeypatches both real-adapter classes with distinguishable fakes
    (call_count each) so a wrong selection is caught directly (the
    "wrong" fake's call_count would stay 0, the "right" one's would
    never increment) rather than only inferred from the scan completing
    -- either adapter's fake would let the scan complete on its own, so
    completion alone would not prove which one actually ran."""
    if not postgres_available:
        pytest.skip("No PostgreSQL instance reachable for this task test")

    monkeypatch.setenv("WORKER_ROLE", "ingestion_worker")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "a-real-minio-password")
    monkeypatch.setenv("MINIO_BUCKET", "scan-raw-output")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-real-anthropic-key")
    get_settings.cache_clear()

    nuclei_scanner = _FakeActiveScanner()
    nmap_scanner = _FakeActiveScanner(name_override="nmap", output_format_override="nmap-xml")
    ai_provider = _FakeAIProviderPort()
    storage = _FakeStorage()

    import app.workers.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "NucleiAdapter", lambda: nuclei_scanner)
    monkeypatch.setattr(tasks_module, "NmapAdapter", lambda: nmap_scanner)
    monkeypatch.setattr(tasks_module, "MinioStoragePort", lambda **kwargs: storage)
    monkeypatch.setattr(tasks_module, "AnthropicProvider", lambda **kwargs: ai_provider)
    monkeypatch.setattr(
        tasks_module,
        "AnalysisService",
        lambda provider: AnalysisService(provider=provider),
    )

    organization_id = await _create_organization(engine)
    scan_id = await _create_scan(engine, organization_id, scanner_name="nmap")

    try:
        await asyncio.to_thread(run_scan_workflow_task, str(organization_id), str(scan_id), "nmap")
    finally:
        get_settings.cache_clear()

    assert nmap_scanner.call_count == 1
    assert nuclei_scanner.call_count == 0

    # TD #16 (resolved): a real nmap-xml normalizer now exists
    # (app/application/scanning/normalization.py), so this fake's XML
    # output (see _FakeActiveScanner.execute()) carries the pipeline all
    # the way to a genuinely COMPLETED scan -- the same outcome
    # test_run_scan_workflow_task_completes_a_real_scan_end_to_end above
    # already asserts for the nuclei path. Asserting full completion
    # here (not just the EXECUTE_SCANNER step) is what actually exercises
    # the new normalizer in its real pipeline context, not just the
    # adapter-selection logic this test also covers via call_count.
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        await set_org_context(session, organization_id)
        scan = await SqlAlchemyScanRepository(session).get_by_id(scan_id)
        assert scan is not None
        assert scan.status is ScanStatus.COMPLETED


async def test_composition_root_rejects_an_unimplemented_ai_provider(
    postgres_available: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AnthropicProvider is the only AIProviderPort implementation this
    codebase has (see run_scan_workflow.py's module docstring) --
    pointing AI_DEFAULT_PROVIDER at anything else must fail loudly at
    the worker's own composition root, mirroring the equivalent check
    app/main.py's _lifespan makes for the API process before Milestone 7
    moved this specific check here (this worker is now the process that
    actually constructs AnalysisService)."""
    if not postgres_available:
        pytest.skip("No PostgreSQL instance reachable for this task test")

    monkeypatch.setenv("WORKER_ROLE", "ingestion_worker")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://app_user:app_password@localhost/security_platform_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "a-real-minio-password")
    monkeypatch.setenv("MINIO_BUCKET", "scan-raw-output")
    monkeypatch.setenv("AI_DEFAULT_PROVIDER", "openai")
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="has no adapter wired"):
            await _run_scan_workflow_from_settings(UUID(int=0), UUID(int=0), "nuclei")
    finally:
        get_settings.cache_clear()
