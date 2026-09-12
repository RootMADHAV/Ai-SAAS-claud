"""Composition root: builds the FastAPI application, wires the
process-lifetime objects every request's dependency chain needs
(``app/api/dependencies.py``'s ``AppState``), and mounts the public
(``/api/v1``) and internal (``/internal``) routers -- the "Public/
internal API split" named in PROJECT_STATE.md sections 1 and 3.

This module, and everything under ``app/api/``, was Milestone 5's
actual deliverable (PROJECT_STATE.md section 15). It introduced no new
business logic of its own -- it wires together use cases and adapters
that already existed at the start of that milestone (Milestones 2-4),
plus the two request/response schemas and three routes that translate
HTTP into calls against them.

Milestone 7 update: this process's own responsibility narrowed rather
than grew. ``POST .../scans/{scan_id}/run`` (app/api/v1/scans.py) no
longer runs ``RunScanWorkflowUseCase`` inside the HTTP request handler
-- it validates a scan's existence/scanner-name (a cheap, synchronous
database read) and dispatches a Celery task
(``app/workers/tasks.py``'s ``run_scan_workflow_task``) instead,
returning ``202 Accepted``. That task builds its own composition root
(``app/workers/tasks.py``'s ``_run_scan_workflow_from_settings``,
mirroring this module's own ``_lifespan`` almost exactly, but for
``worker_role=ingestion_worker``), so this API process's ``_lifespan``
no longer needs to construct ``MinioStoragePort`` or
``AnthropicProvider``/``AnalysisService`` at all -- it never touches
either one anymore. Least-privilege, not an oversight: a process that
never uses a credential should not hold it (see
``app/config.py``'s ``check_role_boundaries`` for where the
``ANTHROPIC_API_KEY`` requirement moved to instead). ``NucleiAdapter``
is unaffected and still constructed here -- it is credential-free and
``run_scan`` still needs its ``.name`` for the cheap scanner-mismatch
check, unchanged from Milestone 5.

Update: ``_lifespan`` now also builds an ``AuthConfig``
(app/api/dependencies.py) from ``Settings`` and adds it to ``AppState``
-- ``jwt_secret`` has been a required ``Settings`` field for
``worker_role=api`` since Milestone 1 (forward-looking, unused until
now); this work is what actually consumes it.
``app/api/v1/auth.py``'s router is mounted at ``/api/v1/auth``, and two
new global exception handlers are registered (``AuthenticationError``
-> 401, ``EmailAlreadyRegisteredError`` -> 409), the same codebase-wide
convention this module already established for ``LookupError`` -> 404
and ``ScannerMismatchError`` -> 409.

Update (Phase 3 backend preparation, PROJECT_STATE.md): two additions,
neither a new milestone --
  - ``app/api/v1/organizations.py``'s router is mounted at
    ``/api/v1/organizations``, plus a fourth exception handler
    (``OrganizationSlugAlreadyTakenError`` -> 409), the same pattern as
    ``EmailAlreadyRegisteredError`` immediately above it.
  - ``create_app()`` now optionally takes ``cors_allowed_origins`` and
    registers ``CORSMiddleware`` when any are configured -- see that
    parameter's own docstring for why it defaults to
    ``get_cors_allowed_origins()`` (app/config.py) rather than
    ``Settings``.

Update (Phase 4, Nmap adapter wiring): ``_lifespan`` now constructs
``NmapAdapter`` alongside ``NucleiAdapter`` -- both credential-free, same
as before -- and ``AppState.active_scanner`` (singular) became
``active_scanners`` (plural, a tuple of both). ``run_scan``
(app/api/v1/scans.py) now checks a scan's ``scanner_name`` against the
whole tuple rather than a single adapter's ``.name``; the actual
per-scan adapter *selection* for execution happens one layer down, in
``app/workers/tasks.py``'s composition root, which is the only place
that ever needs a single concrete ``ActiveScanner`` instance to hand to
``RunScanWorkflowUseCase`` (unchanged -- still exactly one adapter per
use-case construction, per that module's own docstring). See
``AppState.active_scanners``'s own docstring (app/api/dependencies.py)
for why this is a plain tuple, not a scanner registry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.api.dependencies import AppState, AuthConfig
from app.api.internal.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.scans import router as scans_router
from app.application.identity.errors import (
    AuthenticationError,
    EmailAlreadyRegisteredError,
    OrganizationSlugAlreadyTakenError,
)
from app.application.scanning.run_scan_workflow import ScannerMismatchError
from app.config import Settings, get_cors_allowed_origins, get_settings
from app.infrastructure.db.session import create_engine, create_session_factory
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter


def _build_auth_config(settings: Settings) -> AuthConfig:
    """``settings.jwt_secret`` is guaranteed not-None by
    ``check_role_boundaries`` (app/config.py) for ``worker_role=api`` --
    the assert below exists for mypy strict's benefit, not because it
    could meaningfully fail here, the same pattern already used for
    ``settings.database_url`` in ``_lifespan`` below."""
    assert settings.jwt_secret is not None
    return AuthConfig(
        jwt_secret=settings.jwt_secret,
        access_token_expire_minutes=settings.access_token_expire_minutes,
        refresh_token_expire_days=settings.refresh_token_expire_days,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Builds every process-lifetime object exactly once, at startup, and
    disposes the database engine at shutdown. Only ``create_app()``
    itself is called at import time (including by uvicorn's
    ``app.main:app``); this function only runs once the app actually
    starts serving, which is what lets tests build an app via
    ``create_app()`` and override its dependencies without this function
    ever needing to run against a real database connection -- see
    ``tests/integration/test_api_scans.py``.
    """
    settings = get_settings()
    # Settings.check_role_boundaries (app/config.py) already guarantees
    # database_url is not None for worker_role=api -- a genuinely
    # missing value already raised at Settings() construction, before
    # this function ever runs. This assert exists for mypy strict's
    # benefit, not because it could meaningfully fail here.
    assert settings.database_url is not None

    engine = create_engine(settings)
    app.state.wired = AppState(
        session_factory=create_session_factory(engine),
        # Phase 4 (Nmap): both concrete adapters this process actually
        # supports are constructed here, once, at startup -- same
        # credential-free construction NucleiAdapter already had, no new
        # abstraction added for the second one. See AppState.active_scanners'
        # own docstring (app/api/dependencies.py) for why this is a plain
        # tuple, not a registry.
        active_scanners=(NucleiAdapter(), NmapAdapter()),
        auth_config=_build_auth_config(settings),
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

    Milestone 7 note, stated plainly rather than left for a future
    session to rediscover: as of this milestone, no route currently
    mounted on this app actually raises a bare ``LookupError`` that
    reaches this handler. ``run_scan`` (app/api/v1/scans.py) now raises
    ``HTTPException(404, ...)`` directly from its own synchronous
    existence check, rather than relying on
    ``RunScanWorkflowUseCase.execute``'s ``LookupError`` to propagate up
    from inside the request handler -- that use case still raises it,
    but only from inside the ``ingestion_worker`` Celery task now
    (``app/workers/tasks.py``), where it becomes a Celery task failure,
    not an HTTP response this handler ever sees. This handler is kept
    registered anyway, not removed as dead code: it implements a
    codebase-wide convention (every repository's mutation methods --
    ``update``/``soft_delete`` across every bounded context -- already
    raise ``LookupError`` the same way), and Findings/Assets/Reporting's
    still-unbuilt HTTP routes (PROJECT_STATE.md's own deferred scope) are
    the more likely next caller of it, not something this milestone
    should remove only to re-add in the very next one that needs it.

    Typed as ``Exception`` (not ``LookupError``) only because
    ``Starlette.add_exception_handler``'s own type signature requires an
    exact ``Exception`` parameter type for every registered handler,
    regardless of which specific exception class it is registered
    against (a mypy contravariance rule, not a runtime concern).
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def _scanner_mismatch_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A ``Scan`` whose recorded ``scanner_name`` does not match any of
    the ``ActiveScanner`` adapters this process is wired to (a small,
    fixed tuple of concrete adapters as of Phase 4 -- see
    ``AppState.active_scanners``'s own docstring, app/api/dependencies.py,
    for why that still isn't a scanner registry) is a state conflict, not
    a missing resource or a malformed request -- 409, not 404 or 422. As
    of Milestone 7, this check runs directly in ``app/api/v1/scans.py``'s
    ``run_scan`` route (before a Celery task is ever dispatched, not
    inside the pipeline itself), but it still raises the same
    ``ScannerMismatchError`` type ``RunScanWorkflowUseCase`` always has
    (still constructed with exactly one ``ActiveScanner`` -- see that
    class's own module docstring), so this one global handler covers
    both call sites without either needing to know about the other.

    Typed as ``Exception`` for the same mypy-contravariance reason given
    in ``_lookup_error_handler`` above -- Starlette only ever calls this
    with a ``ScannerMismatchError`` instance.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _authentication_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handles ``AuthenticationError`` and both its
    subclasses (``InvalidCredentialsError``, ``InvalidRefreshTokenError``
    -- app/application/identity/errors.py) -- one handler covers both
    since both represent "the credential/token presented is not valid"
    and both map to the same HTTP 401, the same "one handler per shared
    base class" pattern this module already uses for ``LookupError``
    covering several repositories' raise sites.

    Typed as ``Exception`` for the same mypy-contravariance reason given
    in ``_lookup_error_handler`` above.
    """
    return JSONResponse(status_code=401, content={"detail": str(exc)})


async def _email_already_registered_handler(request: Request, exc: Exception) -> JSONResponse:
    """A registration attempt for an email that already has
    an active ``User`` row is a conflict with existing state -- 409, not
    401 (see ``EmailAlreadyRegisteredError``'s own docstring on why it is
    deliberately not part of the ``AuthenticationError`` hierarchy
    above)."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _organization_slug_taken_handler(request: Request, exc: Exception) -> JSONResponse:
    """A ``POST /api/v1/organizations`` naming a ``slug`` that already has
    an active ``Organization`` row is a conflict with existing state --
    409, mirroring ``_email_already_registered_handler`` immediately
    above for the identical reason (uniqueness conflict, not a credential
    failure or a missing resource)."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def create_app(cors_allowed_origins: Sequence[str] | None = None) -> FastAPI:
    """Factory, not a bare module-level ``FastAPI()`` -- tests call this
    directly and override ``app/api/dependencies.py``'s low-level
    providers rather than ever running ``_lifespan`` for real, so
    building the app itself must not require a real database connection
    to succeed. ``app`` below is the module-level instance uvicorn's
    ``app.main:app`` points at; every test builds its own via this
    function instead, so no test shares mutable state with another test
    or with a real deployment.

    ``cors_allowed_origins``: an explicit override, mainly for tests
    (``tests/unit/test_cors.py``) that want a deterministic value
    regardless of the environment they happen to run in. Defaults to
    ``get_cors_allowed_origins()`` (app/config.py) when not given --
    deliberately not ``Settings``/``get_settings()``: this function runs
    unconditionally on every import of this module (every test that does
    ``from app.main import create_app``, plus the bottom-of-file
    ``app = create_app()`` line), so it must not require a full,
    production-shaped ``Settings()`` to succeed -- see
    ``get_cors_allowed_origins()``'s own docstring for the full
    reasoning.
    """
    app = FastAPI(title="Security Platform API", lifespan=_lifespan)

    origins = (
        list(cors_allowed_origins)
        if cors_allowed_origins is not None
        else get_cors_allowed_origins()
    )
    if origins:
        # allow_credentials=True is required for the httpOnly auth
        # cookies (PROJECT_STATE.md section 2's locked transport
        # decision) to ever reach this API from a browser running the
        # Phase 3 frontend on a different origin. Starlette rejects
        # combining that with a wildcard allow_origins, which is also
        # why an empty/unconfigured origins list disables CORS entirely
        # here rather than silently falling back to "*". allow_methods/
        # allow_headers stay scoped to what the public API actually uses
        # today (GET/POST, JSON request bodies) rather than "*" -- widen
        # both the day a route needs a different method or header.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    app.include_router(scans_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(organizations_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/internal")
    app.add_exception_handler(LookupError, _lookup_error_handler)
    app.add_exception_handler(ScannerMismatchError, _scanner_mismatch_error_handler)
    app.add_exception_handler(AuthenticationError, _authentication_error_handler)
    app.add_exception_handler(EmailAlreadyRegisteredError, _email_already_registered_handler)
    app.add_exception_handler(
        OrganizationSlugAlreadyTakenError, _organization_slug_taken_handler
    )
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 -- dev convenience entrypoint;
    # the real deployment target is the Docker Compose `backend` service
    # (Milestone 7), which runs this via `uvicorn app.main:app` with its
    # own host/port configuration, not this __main__ block.
