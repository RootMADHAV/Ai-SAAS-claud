"""Unit tests for RefreshTokenUseCase, with fake repositories."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from app.application.identity.errors import InvalidRefreshTokenError
from app.application.identity.refresh_token import RefreshTokenUseCase
from app.application.interfaces.identity_repository import (
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.entities import RefreshToken, User
from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id
from app.infrastructure.security.token_service import (
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

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
        "email": "dana@example.com",
        "full_name": "Dana Example",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


async def _seed_refresh_token(
    repo: FakeRefreshTokenRepository, user_id: UUID, **overrides: object
) -> str:
    raw = generate_refresh_token()
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "user_id": user_id,
        "token_hash": hash_refresh_token(raw),
        "expires_at": now + timedelta(days=30),
        "created_at": now,
        "revoked_at": None,
    }
    defaults.update(overrides)
    token = RefreshToken(**defaults)  # type: ignore[arg-type]
    await repo.add(token)
    return raw


def _make_use_case(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> RefreshTokenUseCase:
    return RefreshTokenUseCase(
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        jwt_secret=_SECRET,
        access_token_expire_minutes=15,
        refresh_token_expire_days=30,
    )


async def test_refresh_succeeds_with_a_valid_token(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    raw = await _seed_refresh_token(refresh_token_repository, user.id)
    use_case = _make_use_case(user_repository, refresh_token_repository)

    result = await use_case.execute(raw_refresh_token=raw)

    assert result.user.id == user.id
    assert decode_access_token(result.access_token, secret=_SECRET) == user.id
    assert result.refresh_token != raw


async def test_refresh_rotates_the_token_revoking_the_presented_one(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    raw = await _seed_refresh_token(refresh_token_repository, user.id)
    use_case = _make_use_case(user_repository, refresh_token_repository)

    await use_case.execute(raw_refresh_token=raw)

    old_stored = await refresh_token_repository.get_by_token_hash(hash_refresh_token(raw))
    assert old_stored is not None
    assert old_stored.revoked_at is not None


async def test_refresh_rejects_reuse_of_an_already_rotated_token(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    raw = await _seed_refresh_token(refresh_token_repository, user.id)
    use_case = _make_use_case(user_repository, refresh_token_repository)

    await use_case.execute(raw_refresh_token=raw)

    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(raw_refresh_token=raw)


async def test_refresh_rejects_an_unknown_token(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    use_case = _make_use_case(user_repository, refresh_token_repository)
    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(raw_refresh_token="a-token-that-was-never-issued")


async def test_refresh_rejects_an_expired_token(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user()
    user_repository.users[user.id] = user
    raw = await _seed_refresh_token(
        refresh_token_repository, user.id, expires_at=utcnow() - timedelta(seconds=1)
    )
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(raw_refresh_token=raw)


async def test_refresh_rejects_a_token_whose_user_no_longer_exists(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    missing_user_id = new_id()
    raw = await _seed_refresh_token(refresh_token_repository, missing_user_id)
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(raw_refresh_token=raw)


async def test_refresh_rejects_a_token_for_a_now_inactive_user(
    user_repository: FakeUserRepository, refresh_token_repository: FakeRefreshTokenRepository
) -> None:
    user = _active_user(is_active=False)
    user_repository.users[user.id] = user
    raw = await _seed_refresh_token(refresh_token_repository, user.id)
    use_case = _make_use_case(user_repository, refresh_token_repository)

    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(raw_refresh_token=raw)
