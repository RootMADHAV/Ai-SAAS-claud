"""Unit tests for the small provider functions in
``app/api/dependencies.py`` whose own bodies are never exercised by the
integration suite: ``tests/integration/test_api_scans.py`` overrides
``get_session_factory``/``get_active_scanners``/``get_scan_dispatcher``
wholesale via ``app.dependency_overrides`` (by design -- see that
module's docstring), which means their *real* implementations are
otherwise never called by any test, only their *replacements*.

This gap is distinct from -- and should not be conflated with -- the
separate, previously-documented ``coverage``/SQLAlchemy-async-greenlet
measurement artifact (PROJECT_STATE.md/docs/session_state.md) that
affects lines *inside* an awaited async-session call (``get_org_session``'s
own body, and every route handler's). That gap is a tool limitation on
code that genuinely does run; this one is code that genuinely does not
run under the existing test suite's override pattern. Closed directly
here, the same way ``tests/integration/test_main_lifespan.py`` was added
to close an analogous gap for ``_lifespan`` itself, rather than left
unexamined.

Also covers ``get_auth_config`` (same "reads off AppState" pattern as
the three functions above), plus fake-repository-driven unit coverage of
``get_current_user`` -- the dependency that verifies the httpOnly
``access_token`` cookie. ``require_organization_member`` is deliberately
**not** unit-tested here: it constructs its own
``SqlAlchemyOrganizationRepository`` directly from the ``AsyncSession``
it is given (the same pattern every other route-level dependency in
this module uses, e.g. ``get_scan_repository``), so faking it
meaningfully at this tier would mean faking SQLAlchemy's own query
execution, not this function's own logic. Its real coverage is
``tests/integration/test_api_scans.py``'s
``TestAuthenticationAndAuthorization`` class, against a real Postgres
instance -- the same tier every other ``get_org_session``-dependent
function in this module is already verified at, for the same reason.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    AppState,
    AuthConfig,
    get_active_scanners,
    get_auth_config,
    get_current_user,
    get_scan_dispatcher,
    get_session_factory,
    require_scan_write_access,
)
from app.application.interfaces.identity_repository import UserRepositoryPort
from app.domain.identity.entities import OrganizationMember, User
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id
from app.infrastructure.security.token_service import create_access_token
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter

_SECRET = "unit-test-secret-value"


def _auth_config() -> AuthConfig:
    return AuthConfig(
        jwt_secret=_SECRET, access_token_expire_minutes=15, refresh_token_expire_days=30
    )


def _fake_request(wired: AppState) -> Mock:
    request = Mock()
    request.app.state.wired = wired
    return request


def test_get_session_factory_reads_it_off_app_state() -> None:
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker()
    wired = AppState(
        session_factory=session_factory,
        active_scanners=(NucleiAdapter(),),
        auth_config=_auth_config(),
    )

    result = get_session_factory(_fake_request(wired))

    assert result is session_factory


def test_get_active_scanners_reads_it_off_app_state() -> None:
    scanners = (NucleiAdapter(), NmapAdapter())
    wired = AppState(
        session_factory=async_sessionmaker(), active_scanners=scanners, auth_config=_auth_config()
    )

    result = get_active_scanners(_fake_request(wired))

    assert result is scanners
    assert {scanner.name for scanner in result} == {"nuclei", "nmap"}


def test_get_auth_config_reads_it_off_app_state() -> None:
    auth_config = _auth_config()
    wired = AppState(
        session_factory=async_sessionmaker(),
        active_scanners=(NucleiAdapter(),),
        auth_config=auth_config,
    )

    result = get_auth_config(_fake_request(wired))

    assert result is auth_config


def test_get_scan_dispatcher_returns_a_callable_that_enqueues_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "app.api.dependencies.run_scan_workflow_task.delay",
        lambda organization_id, scan_id, scanner_name: calls.append(
            (organization_id, scan_id, scanner_name)
        ),
    )
    organization_id, scan_id = uuid4(), uuid4()

    dispatch = get_scan_dispatcher()
    dispatch(organization_id, scan_id, "nmap")

    assert calls == [(str(organization_id), str(scan_id), "nmap")]


class _FakeUserRepository(UserRepositoryPort):
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        raise NotImplementedError

    async def add(self, user: User) -> None:
        self.users[user.id] = user

    async def update(self, user: User) -> None:
        self.users[user.id] = user

    async def soft_delete(self, user_id: UUID) -> None:
        raise NotImplementedError


def _active_user(**overrides: object) -> User:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "email": "gina@example.com",
        "full_name": "Gina Example",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


async def test_get_current_user_returns_the_user_named_by_a_valid_cookie() -> None:
    user = _active_user()
    repo = _FakeUserRepository()
    repo.users[user.id] = user
    token = create_access_token(user_id=user.id, secret=_SECRET, expire_minutes=15)

    result = await get_current_user(
        access_token=token, user_repository=repo, auth_config=_auth_config()
    )

    assert result.id == user.id


async def test_get_current_user_raises_401_with_no_cookie() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            access_token=None, user_repository=_FakeUserRepository(), auth_config=_auth_config()
        )

    assert exc_info.value.status_code == 401


async def test_get_current_user_raises_401_for_a_malformed_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            access_token="not-a-real-token",
            user_repository=_FakeUserRepository(),
            auth_config=_auth_config(),
        )

    assert exc_info.value.status_code == 401


async def test_get_current_user_raises_401_when_the_token_names_an_unknown_user() -> None:
    token = create_access_token(user_id=uuid4(), secret=_SECRET, expire_minutes=15)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            access_token=token,
            user_repository=_FakeUserRepository(),
            auth_config=_auth_config(),
        )

    assert exc_info.value.status_code == 401


async def test_get_current_user_raises_401_when_the_user_is_inactive() -> None:
    user = _active_user(is_active=False)
    repo = _FakeUserRepository()
    repo.users[user.id] = user
    token = create_access_token(user_id=user.id, secret=_SECRET, expire_minutes=15)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(access_token=token, user_repository=repo, auth_config=_auth_config())

    assert exc_info.value.status_code == 401


def _member(role: OrganizationRole) -> OrganizationMember:
    now = utcnow()
    return OrganizationMember(
        id=new_id(),
        organization_id=new_id(),
        user_id=new_id(),
        role=role,
        status=MembershipStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "role", [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.MEMBER]
)
async def test_require_scan_write_access_returns_the_member_for_a_writer_role(
    role: OrganizationRole,
) -> None:
    """Phase 6 Milestone 1. Unlike ``require_organization_member`` (see
    this module's docstring), ``require_scan_write_access`` has no
    database access of its own -- it only inspects the
    ``OrganizationMember`` that dependency already resolved -- so it can
    be unit-tested directly with a plain domain object."""
    member = _member(role)

    result = await require_scan_write_access(member=member)

    assert result is member


async def test_require_scan_write_access_raises_403_for_a_viewer() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_scan_write_access(member=_member(OrganizationRole.VIEWER))

    assert exc_info.value.status_code == 403


async def test_require_scan_write_access_403_detail_names_the_role_and_organization() -> None:
    viewer = _member(OrganizationRole.VIEWER)

    with pytest.raises(HTTPException) as exc_info:
        await require_scan_write_access(member=viewer)

    detail = str(exc_info.value.detail)
    assert "viewer" in detail
    assert str(viewer.organization_id) in detail
    assert "admin, member, owner" in detail
