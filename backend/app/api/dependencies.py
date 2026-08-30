"""FastAPI dependency providers -- the composition root's per-request
wiring for the Scanning API (``app/api/v1/scans.py``) and, since the
Authentication work resolving Technical Debt #9, the Identity & Access
authentication API (``app/api/v1/auth.py``).

Everything here builds on already-existing, already-verified layers
(Milestone 2's repositories, Milestone 3's ``ScannerPort`` adapter,
Milestone 4's use cases). Nothing in this module invents a new port, a
new use case, or a new persistence concept -- it only wires existing
pieces together per HTTP request, which is exactly Milestone 5's scope
(PROJECT_STATE.md section 15: "route handlers that call
TriggerScanUseCase/RunScanWorkflowUseCase ... and the Milestone 2
repositories").

One request = one RLS-scoped transaction: ``get_org_session`` opens
exactly one ``session_scoped_to_org`` (app/infrastructure/db/session.py,
Milestone 2) per request, and every repository dependency below depends
on that same function -- FastAPI caches a dependency's result per
request by default, so several separate ``Depends(get_org_session)``
calls within one request resolve to the same session, not several
transactions.

Milestone 7 update -- least-privilege, not unrelated cleanup: this API
process no longer runs ``RunScanWorkflowUseCase`` itself (that now
happens inside the ``ingestion_worker`` Celery task,
``app/workers/tasks.py`` -- see ``app/api/v1/scans.py``'s ``run_scan``
route, which validates a scan's existence/scanner-name synchronously and
then dispatches a task instead of calling the use case directly). Two
consequences, both applied here:
  - ``AppState`` no longer holds ``storage``/``analysis_service`` --
    this process never touches ``MinioStoragePort`` or
    ``AnalysisService``/``AnthropicProvider`` anymore, so it has no
    reason to hold their credentials at all (``app/main.py``'s
    ``_lifespan`` correspondingly stopped constructing them; see
    ``app/config.py``'s ``check_role_boundaries`` for where the
    ``ANTHROPIC_API_KEY`` requirement moved to instead --
    ``worker_role=ingestion_worker``, not ``worker_role=api``).
    ``active_scanner`` (``NucleiAdapter``) is unaffected and stays --
    it is credential-free (see its own constructor) and
    ``run_scan`` still needs its ``.name`` for the cheap mismatch check.
  - ``get_storage``/``get_analysis_service``/
    ``get_run_scan_workflow_use_case`` are removed rather than left as
    unreachable dead code -- nothing calls them once ``run_scan`` no
    longer constructs ``RunScanWorkflowUseCase`` itself. In their place,
    ``get_scan_dispatcher`` is the new seam ``run_scan`` depends on: a
    thin callable wrapping ``run_scan_workflow_task.delay(...)``, the
    same override-in-tests pattern already established for
    ``get_active_scanner``/(the now-removed) ``get_storage`` --
    ``tests/integration/test_api_scans.py`` overrides it with a spy
    instead of either running a real Celery worker or hitting a real
    Redis broker in an HTTP-layer test whose job is to verify dispatch
    happened with the right arguments, not to re-verify the task body
    (that is ``tests/integration/test_scan_worker_task.py``'s job,
    mirroring the existing split between this module's own tests and
    ``tests/integration/test_scan_pipeline_orchestrator.py``'s).
  - ``get_asset_repository``/``get_finding_repository`` are removed for
    the same reason: their only caller was
    ``get_run_scan_workflow_use_case``, which no longer exists. Findings/
    Assets have no HTTP surface yet (PROJECT_STATE.md's Milestone 5
    design-decision note, unchanged) so nothing else in this module
    needs them; a future milestone that adds one re-adds the provider it
    actually needs, the same way ``get_analysis_service`` itself was
    only ever added in the milestone that first needed it.

Update (Authentication, resolving Technical Debt #9 -- "No
authentication on any /api/v1 route"). Three additions, each described
in more detail on the function/class itself:

  - ``AuthConfig``/``get_auth_config`` -- the JWT signing secret and
    token-lifetime settings, built once at startup from ``Settings``
    (mirroring how ``active_scanner`` is built once and read via
    ``get_active_scanner``) rather than re-reading ``get_settings()`` on
    every request.
  - ``get_identity_session`` -- a plain (non-RLS-scoped) transaction for
    Identity & Access operations that are not tenant-scoped: ``users``
    and ``refresh_tokens`` carry no ``organization_id`` column and have
    no RLS policy at all (see their models' own docstrings, and the
    initial migration's module docstring on tables with no
    ``organization_id`` at all), so there is no ``app.current_org_id``
    to set for these operations, unlike every
    ``/api/v1/organizations/{organization_id}/...`` route.
    Register/login/refresh use cases and ``get_current_user`` all depend
    on this, not ``get_org_session`` -- none of them has an
    ``organization_id`` in their URL path to scope a session to.
  - ``get_current_user``/``require_organization_member`` -- the two
    dependencies that actually secure a route. ``get_current_user``
    verifies the httpOnly ``access_token`` cookie (the project's locked
    auth-transport decision, PROJECT_STATE.md section 2; set by
    ``app/api/v1/auth.py``'s ``login``/``refresh``) and loads the
    corresponding ``User`` (401 if missing, malformed,
    expired, or naming a user that no longer exists/is inactive).
    ``require_organization_member`` additionally checks that user is an
    ``ACTIVE`` member of the ``organization_id`` named in the URL path
    (403 if not) -- composed from ``get_current_user`` plus
    ``get_org_session`` (already open for the route's own repository
    calls), not a new, separate database round trip. Every
    ``/api/v1/organizations/{organization_id}/scans/...`` route
    (app/api/v1/scans.py) now depends on ``require_organization_member``.

Update (Phase 3 backend preparation, PROJECT_STATE.md -- organization
bootstrap): ``get_new_organization_id``/``get_org_bootstrap_session``/
``get_organization_repository_for_bootstrap``/
``get_create_organization_use_case``, at the end of this module, wire
``POST /api/v1/organizations`` (app/api/v1/organizations.py) -- not a
variant of ``get_org_session`` (which 404s when the named organization
does not already exist, exactly backwards for a creation endpoint) and
not a use case that generates its own id like every other identity use
case does, because ``organizations``' RLS policy is self-referential
(see ``get_new_organization_id``'s own docstring): the id must exist
before the RLS-scoped session that inserts the row can even open.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.identity.create_organization import CreateOrganizationUseCase
from app.application.identity.login_user import LoginUseCase
from app.application.identity.refresh_token import RefreshTokenUseCase
from app.application.identity.register_user import RegisterUserUseCase
from app.application.interfaces.identity_repository import (
    OrganizationRepositoryPort,
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
)
from app.application.interfaces.scanner_port import ActiveScanner
from app.application.interfaces.scanning_repository import ScanRepositoryPort
from app.application.scanning.trigger_scan import TriggerScanUseCase
from app.domain.identity.entities import OrganizationMember, User
from app.domain.shared.enums import MembershipStatus
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from app.infrastructure.db.session import session_scoped_to_org
from app.infrastructure.security.token_service import (
    ACCESS_TOKEN_COOKIE_NAME,
    InvalidAccessTokenError,
    decode_access_token,
)
from app.workers.tasks import run_scan_workflow_task

#: The signature ``run_scan`` (app/api/v1/scans.py) dispatches through --
#: "given an organization and a scan already confirmed to exist, enqueue
#: its pipeline run." Plain ``UUID`` in, nothing out: the caller does not
#: wait on or inspect the dispatched task's result (see
#: ``app/workers/tasks.py``'s module docstring on why no Celery result is
#: ever read back).
ScanDispatcher = Callable[[UUID, UUID], None]


@dataclass(slots=True, frozen=True)
class AuthConfig:
    """JWT signing secret and token lifetimes, read from
    ``Settings`` once at startup (``app/main.py``'s ``_lifespan``) --
    mirrors ``active_scanner``'s own "build once, read via a small
    getter" pattern on ``AppState`` below, rather than every request
    re-calling ``get_settings()`` for values that never change for the
    life of the process."""

    jwt_secret: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int


@dataclass(slots=True)
class AppState:
    """Everything the composition root (``app/main.py``'s lifespan)
    builds once at process startup and every request's dependency chain
    reads from below -- a session factory bound to one long-lived
    engine, and the one Milestone 3 adapter (``ActiveScanner``) this
    process still needs directly (for the cheap scanner-name check in
    ``run_scan`` -- see module docstring on why ``storage``/
    ``analysis_service`` no longer live here as of Milestone 7). Grouped
    into one dataclass, stored as a single ``request.app.state``
    attribute, so there is exactly one place (``_state`` below) that
    casts out of Starlette's untyped ``State`` container, instead of one
    cast per field.
    """

    session_factory: async_sessionmaker[AsyncSession]
    active_scanner: ActiveScanner
    auth_config: AuthConfig


def _state(request: Request) -> AppState:
    """``request.app.state`` is an untyped ``starlette.datastructures.State``
    -- this is the one place that narrows it back to ``AppState``, rather
    than scattering ``# type: ignore`` at every call site that reads a
    field off it. Every other function in this module goes through this
    one, never ``request.app.state`` directly."""
    return cast(AppState, request.app.state.wired)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The one seam tests override wholesale (see
    ``tests/integration/test_api_scans.py``) to point at a test
    database's engine instead of process-startup wiring -- everything
    built on top of this function (``get_org_session`` and everything
    that depends on it) then runs unmodified against that test engine,
    so a test still exercises the real org-scoping and 404 logic, not a
    bypassed version of it."""
    return _state(request).session_factory


def get_active_scanner(request: Request) -> ActiveScanner:
    return _state(request).active_scanner


def get_auth_config(request: Request) -> AuthConfig:
    return _state(request).auth_config


def get_scan_dispatcher() -> ScanDispatcher:
    """Returns a callable that enqueues ``run_scan_workflow_task``
    (``app/workers/tasks.py``) for the given scan. Deliberately not a
    method on ``AppState`` and not itself reading from
    ``request.app.state`` -- ``run_scan_workflow_task`` is a module-level
    Celery task object, not a per-process resource this API needs to
    build or hold a reference to (unlike ``active_scanner``, which really
    is constructed once at startup); wrapping it in one small function
    here exists purely as the override seam tests use, the same reason
    ``get_active_scanner``/``get_session_factory`` are functions rather
    than call sites reaching for ``AppState``/Celery directly.
    """

    def _dispatch(organization_id: UUID, scan_id: UUID) -> None:
        run_scan_workflow_task.delay(str(organization_id), str(scan_id))

    return _dispatch


async def get_org_session(
    organization_id: UUID,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """One request = one RLS-scoped transaction, committed on a clean
    return and rolled back on any exception -- exactly what
    ``session_scoped_to_org`` already guarantees for any caller
    (Milestone 2); this dependency does not reimplement that, it opens
    one.

    The organization-existence check is what turns what would otherwise
    be a foreign-key-violation ``IntegrityError`` on the first write (an
    ugly, uninformative 500) into a clean 404 raised before anything is
    written -- using the same ``OrganizationRepositoryPort.get_by_id``
    every other caller in this codebase already uses, not a new lookup
    path invented for the API. No authentication happens here:
    Milestone 5 does not verify *who* is calling, only that the
    organization named in the URL path exists -- see this milestone's
    design-decision note in PROJECT_STATE.md on why building JWT
    verification now, ahead of Identity & Access's own use cases
    (still an empty ``application/identity`` scaffold), is out of scope.
    """
    async with session_scoped_to_org(session_factory, organization_id) as session:
        organization = await SqlAlchemyOrganizationRepository(session).get_by_id(organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail=f"organization {organization_id} not found")
        yield session


def get_scan_repository(
    session: AsyncSession = Depends(get_org_session),
) -> ScanRepositoryPort:
    return SqlAlchemyScanRepository(session)


def get_trigger_scan_use_case(
    scan_repository: ScanRepositoryPort = Depends(get_scan_repository),
) -> TriggerScanUseCase:
    return TriggerScanUseCase(scan_repository)


async def get_identity_session(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """A plain transaction for Identity & Access operations that are not
    tenant-scoped -- see module docstring for why
    ``users``/``refresh_tokens`` have no ``app.current_org_id`` to set,
    unlike ``get_org_session``'s RLS-scoped transactions."""
    async with session_factory() as session, session.begin():
        yield session


def get_user_repository(
    session: AsyncSession = Depends(get_identity_session),
) -> UserRepositoryPort:
    return SqlAlchemyUserRepository(session)


def get_refresh_token_repository(
    session: AsyncSession = Depends(get_identity_session),
) -> RefreshTokenRepositoryPort:
    return SqlAlchemyRefreshTokenRepository(session)


def get_register_user_use_case(
    user_repository: UserRepositoryPort = Depends(get_user_repository),
) -> RegisterUserUseCase:
    return RegisterUserUseCase(user_repository)


def get_login_use_case(
    user_repository: UserRepositoryPort = Depends(get_user_repository),
    refresh_token_repository: RefreshTokenRepositoryPort = Depends(get_refresh_token_repository),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> LoginUseCase:
    return LoginUseCase(
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        jwt_secret=auth_config.jwt_secret,
        access_token_expire_minutes=auth_config.access_token_expire_minutes,
        refresh_token_expire_days=auth_config.refresh_token_expire_days,
    )


def get_refresh_token_use_case(
    user_repository: UserRepositoryPort = Depends(get_user_repository),
    refresh_token_repository: RefreshTokenRepositoryPort = Depends(get_refresh_token_repository),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> RefreshTokenUseCase:
    return RefreshTokenUseCase(
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        jwt_secret=auth_config.jwt_secret,
        access_token_expire_minutes=auth_config.access_token_expire_minutes,
        refresh_token_expire_days=auth_config.refresh_token_expire_days,
    )


async def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_TOKEN_COOKIE_NAME),
    user_repository: UserRepositoryPort = Depends(get_user_repository),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> User:
    """Verifies the httpOnly ``access_token`` cookie (the project's
    locked auth-transport decision, PROJECT_STATE.md section 2 -- set by
    ``app/api/v1/auth.py``'s ``login``/``refresh``) and returns the
    ``User`` it names.

    Raises ``HTTPException(401)`` if the cookie is missing, the token is
    malformed/expired/wrongly-signed (``InvalidAccessTokenError``, see
    ``app/infrastructure/security/token_service.py``), or the token
    names a user that no longer exists or is no longer active. Every one
    of these maps to the same generic 401 message -- distinguishing
    "no such user" from "token expired" in the response would leak more
    than a caller needs to know to simply log in again.
    """
    if access_token is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        user_id = decode_access_token(access_token, secret=auth_config.jwt_secret)
    except InvalidAccessTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired access token") from exc

    user = await user_repository.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="invalid or expired access token")
    return user


async def require_organization_member(
    organization_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_org_session),
) -> OrganizationMember:
    """Authorization, as distinct from ``get_org_session``'s
    existence check (see that function's own docstring): confirms
    ``current_user`` is an ``ACTIVE`` member of ``organization_id``
    before a route proceeds. Every organization-scoped Scanning route
    (app/api/v1/scans.py) depends on this now, resolving Technical Debt
    #9's "any caller can act as any organization by supplying its id" --
    a token that authenticates successfully (``get_current_user``) but
    names a user with no membership in this organization still gets 403,
    not access.

    Reuses ``get_org_session``'s already-open transaction for this one
    extra lookup rather than opening a second session -- FastAPI's
    per-request dependency caching means every other dependency in this
    request that also depends on ``get_org_session`` (e.g.
    ``get_scan_repository``) shares this exact same session/transaction,
    not a separate one.
    """
    member = await SqlAlchemyOrganizationRepository(session).get_member(
        organization_id, current_user.id
    )
    if member is None or member.status is not MembershipStatus.ACTIVE:
        raise HTTPException(
            status_code=403,
            detail=f"user {current_user.id} is not an active member of organization {organization_id}",
        )
    return member


def get_new_organization_id() -> UUID:
    """Generates the id a brand-new ``Organization`` row will use, ahead
    of opening the RLS-scoped session that inserts it. ``organizations``'
    own RLS policy is self-referential (``id =
    current_setting('app.current_org_id')::uuid`` -- see the initial
    migration's module docstring), so the id must be known and set as
    the session's org context *before* the insert -- unlike every other
    org-scoped route (app/api/v1/scans.py), whose ``organization_id``
    already exists in the URL path, there is no existing id to scope to
    here.

    A plain FastAPI dependency, not a call inside the use case (unlike
    every other identity use case's own id generation, e.g.
    ``RegisterUserUseCase``), purely so this function and
    ``get_org_bootstrap_session`` below -- and the route handler,
    app/api/v1/organizations.py, which also depends on this function
    directly to attach the same id to its response -- all resolve to the
    *same* value within one request. FastAPI caches a dependency's
    result per request by default, so every ``Depends(get_new_organization_id)``
    call site in one request resolves once, not several different ids.
    """
    return new_id()


async def get_org_bootstrap_session(
    organization_id: UUID = Depends(get_new_organization_id),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """An RLS-scoped transaction for creating a brand-new organization --
    not ``get_org_session`` above, which 404s when the named organization
    does not already exist (exactly backwards for a creation endpoint).
    Opens ``session_scoped_to_org`` directly against the freshly
    generated ``organization_id`` (see ``get_new_organization_id``'s own
    docstring for why that id must exist before this session opens)."""
    async with session_scoped_to_org(session_factory, organization_id) as session:
        yield session


def get_organization_repository_for_bootstrap(
    session: AsyncSession = Depends(get_org_bootstrap_session),
) -> OrganizationRepositoryPort:
    return SqlAlchemyOrganizationRepository(session)


def get_create_organization_use_case(
    organization_repository: OrganizationRepositoryPort = Depends(
        get_organization_repository_for_bootstrap
    ),
) -> CreateOrganizationUseCase:
    return CreateOrganizationUseCase(organization_repository)
