"""Composition root: builds the FastAPI application, wires the
process-lifetime objects every request's dependency chain needs
(``app/api/dependencies.py``'s ``AppState``), and mounts the public
(``/api/v1``) and internal (``/internal``) routers -- the "Public/
internal API split" named in PROJECT_STATE.md sections 1 and 3.

This module, and everything under ``app/api/``, is Milestone 5's actual
deliverable (PROJECT_STATE.md section 15). It introduces no new
business logic of its own -- it wires together use cases and adapters
that already existed at the start of this milestone (Milestones 2-4),
plus the two request/response schemas and three routes that translate
HTTP into calls against them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.api.dependencies import AppState
from app.api.internal.health import router as health_router
from app.api.v1.scans import router as scans_router
from app.application.scanning.run_scan_workflow import ScannerMismatchError
from app.config import get_settings
from app.infrastructure.db.session import create_engine, create_session_factory
from app.infrastructure.storage.minio_storage import MinioStoragePort
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Builds every process-lifetime object exactly once, at startup, and
    disposes the database engine at shutdown. Only ``create_app()``
    itself is called at import time (including by uvicorn's
    ``app.main:app``); this function only runs once the app actually
    starts serving, which is what lets tests build an app via
    ``create_app()`` and override its dependencies without this function
    ever needing to run against a real database/MinIO connection -- see
    ``tests/integration/test_api_scans.py``.
    """
    settings = get_settings()
    # Settings.check_role_boundaries (app/config.py) already guarantees
    # database_url/minio_* are not None for worker_role=api -- a
    # genuinely missing value already raised at Settings() construction,
    # before this function ever runs. These asserts exist for mypy
    # strict's benefit, not because any of them could meaningfully fail
    # here.
    assert settings.database_url is not None
    assert settings.minio_endpoint is not None
    assert settings.minio_root_user is not None
    assert settings.minio_root_password is not None
    assert settings.minio_bucket is not None

    engine = create_engine(settings)
    app.state.wired = AppState(
        session_factory=create_session_factory(engine),
        active_scanner=NucleiAdapter(),
        storage=MinioStoragePort(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            bucket=settings.minio_bucket,
        ),
    )
    try:
        yield
    finally:
        await engine.dispose()


async def _lookup_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """``LookupError`` is this codebase's existing convention for "the
    thing you asked for by identity does not exist" (every repository's
    ``get_by_id``-adjacent methods, and ``RunScanWorkflowUseCase.execute``
    itself, already raise it for exactly this reason -- see Milestone 2's
    repositories and Milestone 4's orchestrator). One handler here maps
    that convention to 404 for every route, rather than every route
    catching it individually.

    Typed as ``Exception`` (not ``LookupError``) only because
    ``Starlette.add_exception_handler``'s own type signature requires an
    exact ``Exception`` parameter type for every registered handler,
    regardless of which specific exception class it is registered
    against (a mypy contravariance rule, not a runtime concern) --
    Starlette itself only ever calls this function with a ``LookupError``
    instance, matching how it is registered in ``create_app()`` below.
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def _scanner_mismatch_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A ``Scan`` whose recorded ``scanner_name`` does not match the one
    ``ActiveScanner`` this process is wired to (see
    ``RunScanWorkflowUseCase``'s module docstring on why no multi-adapter
    registry exists yet) is a state conflict, not a missing resource or
    a malformed request -- 409, not 404 or 422.

    Typed as ``Exception`` for the same mypy-contravariance reason given
    in ``_lookup_error_handler`` above -- Starlette only ever calls this
    with a ``ScannerMismatchError`` instance.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def create_app() -> FastAPI:
    """Factory, not a bare module-level ``FastAPI()`` -- tests call this
    directly and override ``app/api/dependencies.py``'s low-level
    providers rather than ever running ``_lifespan`` for real, so
    building the app itself must not require a real database/MinIO
    connection to succeed. ``app`` below is the module-level instance
    uvicorn's ``app.main:app`` points at; every test builds its own via
    this function instead, so no test shares mutable state with another
    test or with a real deployment.
    """
    app = FastAPI(title="Security Platform API", lifespan=_lifespan)
    app.include_router(scans_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/internal")
    app.add_exception_handler(LookupError, _lookup_error_handler)
    app.add_exception_handler(ScannerMismatchError, _scanner_mismatch_error_handler)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 -- dev convenience entrypoint;
    # the real deployment target is the Docker Compose `backend` service
    # (Milestone 7), which will run this via `uvicorn app.main:app` with
    # its own host/port/worker configuration, not this __main__ block.
