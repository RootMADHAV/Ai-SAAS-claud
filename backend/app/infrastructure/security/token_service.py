"""JWT access-token and opaque refresh-token primitives.

Same architectural placement as ``password_hashing.py`` (see that
module's docstring) -- a single-implementation, framework/DB-free
infrastructure utility, imported directly by the application-layer
identity use cases rather than hidden behind a new port.

Two different token shapes are used deliberately, not for their own
sake:
  - The **access token** is a signed JWT (``PyJWT``, HS256) -- stateless,
    verified by signature alone, no database round-trip needed on every
    authenticated request (``get_current_user``,
    app/api/dependencies.py). Short-lived (``Settings.
    access_token_expire_minutes``, default 15) precisely because it
    cannot be revoked before it expires.
  - The **refresh token** is opaque random data (``secrets.token_urlsafe``),
    not a JWT -- it is looked up by its hash against the ``refresh_tokens``
    table (``RefreshTokenRepositoryPort.get_by_token_hash``) on every use,
    which is what makes revocation and rotation (app/application/
    identity/tokens.py) possible at all: a self-contained JWT refresh
    token could not be invalidated before its own expiry without a
    separate denylist, which is exactly what the ``refresh_tokens`` table
    already is. Only the SHA-256 hash of the raw value is ever persisted
    (mirrors ``RefreshToken.token_hash``'s own docstring) -- a fast hash,
    not bcrypt, since a refresh token is already high-entropy random data,
    not a human-chosen secret bcrypt's slow-hash design defends against.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

_ALGORITHM = "HS256"
_TOKEN_TYPE_CLAIM = "type"

#: The cookie names the httpOnly-cookie transport (the project's locked
#: auth-transport decision, PROJECT_STATE.md section 2) uses to deliver
#: tokens to the client. Defined once here, not duplicated as string
#: literals in both `app/api/v1/auth.py` (which sets them) and
#: `app/api/dependencies.py` (which reads `ACCESS_TOKEN_COOKIE_NAME`
#: back), so the two can never silently drift out of sync.
ACCESS_TOKEN_COOKIE_NAME = "access_token"
REFRESH_TOKEN_COOKIE_NAME = "refresh_token"
_ACCESS_TOKEN_TYPE = "access"


class InvalidAccessTokenError(ValueError):
    """Raised when an access token is malformed, expired, signed with the
    wrong secret, or not actually an access token (see ``type`` claim)."""


def create_access_token(*, user_id: UUID, secret: str, expire_minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        _TOKEN_TYPE_CLAIM: _ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> UUID:
    """Verify ``token``'s signature and expiry and return the ``UUID`` of
    the user it was issued for.

    Raises ``InvalidAccessTokenError`` for any failure -- expired,
    malformed, wrong signature, wrong token type, or a subject claim that
    is not a valid UUID. Callers (``get_current_user``,
    app/api/dependencies.py) map this uniformly to HTTP 401 without
    needing to distinguish *why* the token was rejected.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc

    if payload.get(_TOKEN_TYPE_CLAIM) != _ACCESS_TOKEN_TYPE:
        raise InvalidAccessTokenError("token is not an access token")

    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidAccessTokenError("token missing a valid subject claim") from exc


def generate_refresh_token() -> str:
    """A cryptographically random, URL-safe opaque token -- 32 bytes of
    entropy (256 bits), never itself persisted (see module docstring)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
