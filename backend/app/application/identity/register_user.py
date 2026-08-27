"""register_user use case: creates a new User account with a hashed
password.

Deliberately scoped to Identity & Access's own aggregate only -- does
not create an Organization or OrganizationMember row. Building an
organization-creation flow here would either bypass the ">= 1 Owner
always" invariant PROJECT_STATE.md section 5 names, or require building
around it prematurely -- exactly the reasoning PROJECT_STATE.md's own
Milestone 5 design-decision note already gives for deferring an
organization/user-creation HTTP endpoint. A registered user is added to
an organization by a future invite/membership flow (this module's own
package docstring names ``invite_member`` as still unbuilt), not by
this use case.
"""

from __future__ import annotations

from app.application.identity.errors import EmailAlreadyRegisteredError
from app.application.interfaces.identity_repository import UserRepositoryPort
from app.domain.identity.entities import User
from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id
from app.infrastructure.security.password_hashing import hash_password


class RegisterUserUseCase:
    def __init__(self, user_repository: UserRepositoryPort) -> None:
        self._user_repository = user_repository

    async def execute(self, *, email: str, password: str, full_name: str) -> User:
        """Raises ``EmailAlreadyRegisteredError`` if ``email`` already has
        an active ``User`` row (``UserRepositoryPort.get_by_email`` --
        already filters ``deleted_at IS NULL``, so a previously
        soft-deleted account with the same email does not block a new
        registration, matching this codebase's existing soft-delete
        semantics everywhere else)."""
        existing = await self._user_repository.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyRegisteredError(f"email {email!r} is already registered")

        now = utcnow()
        user = User(
            id=new_id(),
            email=email,
            full_name=full_name,
            is_active=True,
            created_at=now,
            updated_at=now,
            hashed_password=hash_password(password),
        )
        await self._user_repository.add(user)
        return user
