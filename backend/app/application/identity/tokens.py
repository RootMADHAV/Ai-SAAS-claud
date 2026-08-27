"""Shared token-pair issuance, used by both ``LoginUseCase`` and
``RefreshTokenUseCase`` -- factored out here rather than duplicated in
each, since "verify who this is, then issue a fresh access+refresh
pair" is the same operation in both call sites (a fresh login and a
refresh-token rotation both end with a brand-new pair; only *how the
caller is verified* differs between the two use cases).

Deliberately a plain function, not a class -- there is no state to hold
between calls (unlike ``LoginUseCase``/``RefreshTokenUseCase`` themselves,
which hold their repository/config dependencies across their one public
``execute()`` method); a function taking every dependency explicitly is
the simplest shape for something with no lifecycle of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.application.interfaces.identity_repository import RefreshTokenRepositoryPort
from app.domain.identity.entities import RefreshToken, User
from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id
from app.infrastructure.security.token_service import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


@dataclass(slots=True)
class TokenPair:
    """An issued access+refresh token pair, plus the ``User`` they were
    issued for -- returned by both ``LoginUseCase.execute`` and
    ``RefreshTokenUseCase.execute`` so the API layer's response schema
    (app/api/v1/auth_schemas.py's ``TokenResponse``) can be built
    identically from either."""

    access_token: str
    refresh_token: str
    user: User


async def issue_token_pair(
    *,
    user: User,
    refresh_token_repository: RefreshTokenRepositoryPort,
    jwt_secret: str,
    access_token_expire_minutes: int,
    refresh_token_expire_days: int,
) -> TokenPair:
    """Mints a new access token (JWT, stateless) and a new refresh token
    (opaque, persisted hashed) for ``user``. Does not revoke or look up
    any existing refresh token for this user -- that is the caller's
    job when it matters (``RefreshTokenUseCase`` revokes the token it
    was given *before* calling this, implementing rotation; a fresh
    login intentionally does not touch any of the user's other
    sessions, so more than one refresh token can be active for the same
    user at once -- e.g. logged in on two devices -- which is the
    conventional, expected behavior for refresh-token-based auth, not
    a gap.
    """
    access_token = create_access_token(
        user_id=user.id, secret=jwt_secret, expire_minutes=access_token_expire_minutes
    )
    raw_refresh_token = generate_refresh_token()
    now = utcnow()
    await refresh_token_repository.add(
        RefreshToken(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=now + timedelta(days=refresh_token_expire_days),
            created_at=now,
        )
    )
    return TokenPair(access_token=access_token, refresh_token=raw_refresh_token, user=user)
