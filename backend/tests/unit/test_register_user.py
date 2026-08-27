"""Unit tests for RegisterUserUseCase, with a fake UserRepositoryPort."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.application.identity.errors import EmailAlreadyRegisteredError
from app.application.identity.register_user import RegisterUserUseCase
from app.application.interfaces.identity_repository import UserRepositoryPort
from app.domain.identity.entities import User
from app.infrastructure.security.password_hashing import verify_password


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
        if user.id not in self.users:
            raise LookupError(f"User {user.id} does not exist")
        self.users[user.id] = user

    async def soft_delete(self, user_id: UUID) -> None:
        raise NotImplementedError


@pytest.fixture
def user_repository() -> FakeUserRepository:
    return FakeUserRepository()


async def test_register_creates_a_user_with_a_hashed_password(
    user_repository: FakeUserRepository,
) -> None:
    use_case = RegisterUserUseCase(user_repository)
    user = await use_case.execute(
        email="alice@example.com", password="correct-horse-battery", full_name="Alice Example"
    )

    assert user.email == "alice@example.com"
    assert user.full_name == "Alice Example"
    assert user.is_active is True
    assert user.hashed_password is not None
    assert user.hashed_password != "correct-horse-battery"
    assert verify_password(password="correct-horse-battery", hashed_password=user.hashed_password)


async def test_register_persists_the_user(user_repository: FakeUserRepository) -> None:
    use_case = RegisterUserUseCase(user_repository)
    user = await use_case.execute(
        email="bob@example.com", password="another-password", full_name="Bob Example"
    )
    assert user_repository.users[user.id].email == "bob@example.com"


async def test_register_rejects_a_duplicate_email(user_repository: FakeUserRepository) -> None:
    use_case = RegisterUserUseCase(user_repository)
    await use_case.execute(email="dupe@example.com", password="first-password", full_name="First")

    with pytest.raises(EmailAlreadyRegisteredError, match="dupe@example.com"):
        await use_case.execute(
            email="dupe@example.com", password="second-password", full_name="Second"
        )


async def test_register_rejects_a_password_over_bcrypts_72_byte_limit(
    user_repository: FakeUserRepository,
) -> None:
    use_case = RegisterUserUseCase(user_repository)
    with pytest.raises(ValueError, match="72 bytes"):
        await use_case.execute(email="toolong@example.com", password="a" * 73, full_name="Too Long")
