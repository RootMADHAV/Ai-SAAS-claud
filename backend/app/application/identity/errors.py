"""Exception hierarchy for the Identity & Access authentication use
cases -- the API layer's global exception handlers (app/main.py) map
each of these to a specific HTTP status code, the same
codebase-wide convention already established for
``LookupError`` -> 404 and ``ScannerMismatchError`` -> 409 (Milestone 5).

``AuthenticationError`` is the common base for "the credential or token
presented is not valid" (-> 401) -- both ``InvalidCredentialsError``
(login) and ``InvalidRefreshTokenError`` (refresh) subclass it so
``app/main.py`` needs exactly one handler registration to cover both,
not one per use case. ``EmailAlreadyRegisteredError`` is deliberately
*not* part of that hierarchy -- a duplicate-email registration attempt
is a conflict with existing state (-> 409), not an authentication
failure, and conflating the two would misreport a 409 as a 401 or vice
versa at the one place (app/main.py) that decides the HTTP status code.
"""

from __future__ import annotations


class AuthenticationError(ValueError):
    """Base for authentication failures that map to HTTP 401. Never
    raised directly -- always one of the subclasses below, each carrying
    a message safe to return to the client (neither confirms nor denies
    *why* a credential was rejected beyond what is already the
    conventional, deliberately-vague "invalid email or password" for
    login, so a 401 response never becomes an account-enumeration
    oracle)."""


class InvalidCredentialsError(AuthenticationError):
    """Raised by ``LoginUseCase`` when the presented email/password does
    not match an active account."""


class InvalidRefreshTokenError(AuthenticationError):
    """Raised by ``RefreshTokenUseCase`` when the presented refresh token
    is unknown, already revoked, or expired."""


class EmailAlreadyRegisteredError(ValueError):
    """Raised by ``RegisterUserUseCase`` when the requested email already
    has an active ``User`` row. Maps to HTTP 409, not 401 -- see module
    docstring."""
