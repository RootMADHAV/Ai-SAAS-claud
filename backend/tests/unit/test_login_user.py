"""Unit tests for LoginUseCase, with fake repositories."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.application.identity.errors import InvalidCredentialsError
from app.application.identity.login_user import LoginUseCase
from app.application.interfaces.identity_repository import (
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.entities import RefreshToken, User
from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id
from app.infrastructure.security.password_hashing import hash_password
from app.infrastructure.security.token_service import decode_access_token, hash_refresh_token

_SECRET = "unit-test-secret-value"


class FakeUserRepository(UserRepositoryPort):
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        for user in self.users.values():
            if user.email == email and not user.is_deleted:
                return user
        return None

    async def add(self, user: User) -> None:
        self.users[user.id] = user

    async def update(self, user: User) -> None:
        self.users[user.id] = user

    async def soft_delete(self, user_id: UUID) -> None:
        raise NotImplementedError


class FakeRefreshTokenRepository(RefreshTokenRepositoryPort):
    def __init__(self) -> None:
        self.tokens: dict[UUID, RefreshToken] = {}

    async def add(self, token: RefreshToken) -> None:
        self.tokens[token.id] = token

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        for token in self.tokens.values():
            if token.token_hash == token_hash:
                return token
        return None

    async def revoke(self, token_id: UUID) -> None:
        self.tokens[token_id].revoked_at = utcnow()


@pytest.fixture
def user_repository() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def refresh_token_repository() -> FakeRefreshTokenRepository:
    return FakeRefreshTokenRepository()


def _active_user(**overrides: object) -> User:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "email": "carol@example.com",
        "full_name": "Carol Example",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "hashed_password": hash_password("correct-password"),
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def _make_use_case(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> LoginUseCase:
    return LoginUseCase(
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        jwt_secret=_SECRET,
        access_token_expire_minutes=15,
        refresh_token_expire_days=30,
    )


async def test_login_succeeds_with_correct_credentials(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    result = await use_case.execute(email=user.email, password="correct-password")

    assert result.user.id == user.id
    assert decode_access_token(result.access_token, secret=_SECRET) == user.id
    assert result.refresh_token


async def test_login_persists_a_refresh_token_hash_not_the_raw_value(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    result = await use_case.execute(email=user.email, password="correct-password")

    stored = await refresh_token_repository.get_by_token_hash(
        hash_refresh_token(result.refresh_token)
    )
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.revoked_at is None
    assert all(
        t.token_hash != result.refresh_token for t in refresh_token_repository.tokens.values()
    )


async def test_login_rejects_unknown_email(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    use_case = _make_use_case(user_repository, refresh_token_repository)
    with pytest.raises(InvalidCredentialsError):
        await use_case.execute(email="nobody@example.com", password="whatever")


async def test_login_rejects_wrong_password(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidCredentialsError):
        await use_case.execute(email=user.email, password="wrong-password")


async def test_login_rejects_an_inactive_user(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user(is_active=False)
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidCredentialsError):
        await use_case.execute(email=user.email, password="correct-password")


async def test_login_rejects_a_user_with_no_password_set(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user(hashed_password=None)
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidCredentialsError):
        await use_case.execute(email=user.email, password="anything")


async def test_login_error_message_does_not_reveal_whether_the_email_exists(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    use_case = _make_use_case(user_repository, refresh_token_repository)

    unknown_email_error = None
    wrong_password_error = None
    try:
        await use_case.execute(email="nobody@example.com", password="whatever")
    except InvalidCredentialsError as exc:
        unknown_email_error = str(exc)
    try:
        await use_case.execute(email=user.email, password="wrong-password")
    except InvalidCredentialsError as exc:
        wrong_password_error = str(exc)

    assert unknown_email_error == wrong_password_error
