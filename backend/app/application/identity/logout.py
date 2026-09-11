"""logout use case: revokes the presented refresh token, ending the
session it belongs to server-side.

Deliberately tolerant of an already-invalid token -- unlike
``RefreshTokenUseCase`` (which must reject an invalid token, since it is
about to issue a fresh credential pair on the caller's behalf), logging
out when the token is already expired, already revoked, or simply
unknown is not an error from the caller's point of view: the end state
("this session is no longer valid") is exactly what was asked for
either way. This use case treats all three the same as a successful
no-op, so the API layer's route (app/api/v1/auth.py) never has to map a
logout-specific error to a status code -- there isn't one.

No ``UserRepositoryPort`` dependency, unlike ``LoginUseCase``/
``RefreshTokenUseCase`` -- logging out only ever needs to look up and
revoke the presented refresh token by its own hash; there is no reason
to also load the ``User`` row it belongs to.
"""

from __future__ import annotations

from app.application.interfaces.identity_repository import RefreshTokenRepositoryPort
from app.infrastructure.security.token_service import hash_refresh_token


class LogoutUseCase:
    def __init__(self, *, refresh_token_repository: RefreshTokenRepositoryPort) -> None:
        self._refresh_token_repository = refresh_token_repository

    async def execute(self, *, raw_refresh_token: str) -> None:
        """No-op (not an error) if the token is unknown or already
        revoked -- see module docstring. An already-expired-but-not-yet-
        revoked token is still revoked here rather than left alone: it is
        already unusable for a refresh either way, but explicitly marking
        it revoked keeps ``revoked_at`` an accurate record of "this
        session was deliberately ended," not just "this token expired on
        its own," for any future audit-log/session-history consumer."""
        token_hash = hash_refresh_token(raw_refresh_token)
        stored = await self._refresh_token_repository.get_by_token_hash(token_hash)
        if stored is None or stored.revoked_at is not None:
            return
        await self._refresh_token_repository.revoke(stored.id)
