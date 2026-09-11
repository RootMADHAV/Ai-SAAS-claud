"""FastAPI dependency providers -- the composition root's per-request
wiring for the Scanning API and the Identity & Access authentication API.
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
from app.application.identity.logout import LogoutUseCase
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

ScanDispatcher = Callable[[UUID, UUID], None]


@dataclass(slots=True, frozen=True)
class AuthConfig:
    jwt_secret: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int


@dataclass(slots=True)
class AppState:
    session_factory: async_sessionmaker[AsyncSession]
    active_scanner: ActiveScanner
    auth_config: AuthConfig


def _state(request: Request) -> AppState:
    return cast(AppState, request.app.state.wired)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return _state(request).session_factory


def get_active_scanner(request: Request) -> ActiveScanner:
    return _state(request).active_scanner


def get_auth_config(request: Request) -> AuthConfig:
    return _state(request).auth_config


def get_scan_dispatcher() -> ScanDispatcher:
    def _dispatch(organization_id: UUID, scan_id: UUID) -> None:
        run_scan_workflow_task.delay(str(organization_id), str(scan_id))

    return _dispatch


async def get_org_session(
    organization_id: UUID,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
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


def get_logout_use_case(
    refresh_token_repository: RefreshTokenRepositoryPort = Depends(get_refresh_token_repository),
) -> LogoutUseCase:
    """Same DI shape as the other auth use case providers above -- only
    needs ``RefreshTokenRepositoryPort`` (see ``LogoutUseCase``'s own
    docstring on why it has no ``UserRepositoryPort`` dependency, unlike
    ``get_login_use_case``/``get_refresh_token_use_case``), and no
    ``AuthConfig`` either: logout never issues or verifies a JWT, it only
    revokes the opaque refresh token it is handed."""
    return LogoutUseCase(refresh_token_repository=refresh_token_repository)


async def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_TOKEN_COOKIE_NAME),
    user_repository: UserRepositoryPort = Depends(get_user_repository),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> User:
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
    return new_id()


async def get_org_bootstrap_session(
    organization_id: UUID = Depends(get_new_organization_id),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
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
