"""login_user use case: verifies email/password credentials and issues a
fresh access/refresh token pair on success.
"""

from __future__ import annotations

from app.application.identity.errors import InvalidCredentialsError
from app.application.identity.tokens import TokenPair, issue_token_pair
from app.application.interfaces.identity_repository import (
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
)
from app.infrastructure.security.password_hashing import verify_password


class LoginUseCase:
    def __init__(
        self,
        *,
        user_repository: UserRepositoryPort,
        refresh_token_repository: RefreshTokenRepositoryPort,
        jwt_secret: str,
        access_token_expire_minutes: int,
        refresh_token_expire_days: int,
    ) -> None:
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._jwt_secret = jwt_secret
        self._access_token_expire_minutes = access_token_expire_minutes
        self._refresh_token_expire_days = refresh_token_expire_days

    async def execute(self, *, email: str, password: str) -> TokenPair:
        """Raises ``InvalidCredentialsError`` for any of: unknown email,
        inactive account, account with no password set yet (an
        OAuth-only account -- ``User.hashed_password`` is nullable per
        its own docstring, and there is no OAuth login flow to have set
        one), or a wrong password. Every one of these maps to the same
        exception and the same deliberately generic message -- a login
        endpoint that responded differently for "no such user" versus
        "wrong password" would let a caller enumerate registered email
        addresses one guess at a time.
        """
        user = await self._user_repository.get_by_email(email)
        if user is None or not user.is_active or user.hashed_password is None:
            raise InvalidCredentialsError("invalid email or password")
        if not verify_password(password=password, hashed_password=user.hashed_password):
            raise InvalidCredentialsError("invalid email or password")

        return await issue_token_pair(
            user=user,
            refresh_token_repository=self._refresh_token_repository,
            jwt_secret=self._jwt_secret,
            access_token_expire_minutes=self._access_token_expire_minutes,
            refresh_token_expire_days=self._refresh_token_expire_days,
        )
