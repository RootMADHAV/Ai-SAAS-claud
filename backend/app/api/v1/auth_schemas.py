"""Pydantic request/response schemas for the public Identity & Access
authentication API (``/api/v1/auth/...``).

Kept in their own module, separate from ``schemas.py`` -- that module's
own docstring scopes it explicitly to "the public Scanning API"; auth
schemas are a different bounded context's HTTP surface and belong beside
it, the same "one module per bounded context's API DTOs" split
``app/api/v1/scans.py`` vs. ``auth.py`` already establishes at the
router level.

Correction: no ``TokenResponse``/``RefreshRequest`` here. Access and
refresh tokens are delivered exclusively via httpOnly cookies (the
project's locked auth-transport decision, PROJECT_STATE.md section 2;
see ``auth.py``'s ``_set_auth_cookies``) -- never in a JSON request or
response body. Putting a token's raw value in a body that ordinary
application JavaScript can read (via ``fetch``/``XMLHttpRequest``) would
defeat the entire purpose of an httpOnly cookie, which exists precisely
so client-side script -- including an XSS payload -- cannot read the
token at all.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# bcrypt's own hard limit (see app/infrastructure/security/
# password_hashing.py) is 72 *bytes*, not characters -- capping at 72
# characters here is a conservative, cheap-to-explain approximation
# (ASCII: 1 byte/char; only multi-byte UTF-8 passwords could still
# exceed 72 bytes under 72 characters) that turns the overwhelmingly
# common case of an over-long password into a clean 422 at the request-
# validation boundary rather than a 500 surfaced from bcrypt itself.
# password_hashing.hash_password's own byte-length check remains the
# actual authority and still applies regardless.
_MAX_PASSWORD_LENGTH = 72


class RegisterRequest(BaseModel):
    """Request body for ``POST /api/v1/auth/register``."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=_MAX_PASSWORD_LENGTH)
    full_name: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    """Returned by ``POST /api/v1/auth/register``, ``POST /api/v1/auth/
    login``, and ``POST /api/v1/auth/refresh`` alike -- basic identity
    info the caller can use immediately, with no token value in it (see
    module docstring)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    full_name: str


class LoginRequest(BaseModel):
    """Request body for ``POST /api/v1/auth/login``."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LENGTH)
