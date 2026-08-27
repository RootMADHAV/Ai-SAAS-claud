"""refresh_token use case: rotates a presented refresh token for a fresh
access/refresh pair.

Rotation, not reuse: the presented token is revoked *before* a new pair
is issued (see ``execute`` below), the standard refresh-token-rotation
practice -- a stolen-and-replayed token can be used at most once before
its own reuse is detectable (a client legitimately holding the *latest*
issued token will always find its own token already revoked if an
attacker got to it first and rotated it out from under them, a signal a
production system would use to revoke the entire token family; this
milestone does not build that detection/family-revocation layer, since
nothing in the current scope consumes it yet -- flagged as future work,
not silently assumed solved).
"""

from __future__ import annotations

from app.application.identity.errors import InvalidRefreshTokenError
from app.application.identity.tokens import TokenPair, issue_token_pair
from app.application.interfaces.identity_repository import (
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
)
from app.domain.shared.clock import utcnow
from app.infrastructure.security.token_service import hash_refresh_token


class RefreshTokenUseCase:
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

    async def execute(self, *, raw_refresh_token: str) -> TokenPair:
        """Raises ``InvalidRefreshTokenError`` if the presented token is
        unknown, already revoked, expired, or belongs to a user who no
        longer exists or is no longer active."""
        token_hash = hash_refresh_token(raw_refresh_token)
        stored = await self._refresh_token_repository.get_by_token_hash(token_hash)
        now = utcnow()
        if stored is None or stored.revoked_at is not None or stored.expires_at <= now:
            raise InvalidRefreshTokenError("refresh token is invalid, revoked, or expired")

        user = await self._user_repository.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshTokenError("refresh token is invalid, revoked, or expired")

        # Revoke before issuing the replacement -- see module docstring
        # on why this order implements rotation, not mere renewal.
        await self._refresh_token_repository.revoke(stored.id)

        return await issue_token_pair(
            user=user,
            refresh_token_repository=self._refresh_token_repository,
            jwt_secret=self._jwt_secret,
            access_token_expire_minutes=self._access_token_expire_minutes,
            refresh_token_expire_days=self._refresh_token_expire_days,
        )
