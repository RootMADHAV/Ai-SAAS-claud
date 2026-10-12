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


class OrganizationSlugAlreadyTakenError(ValueError):
    """Raised by ``CreateOrganizationUseCase`` (Phase 3 backend
    preparation, PROJECT_STATE.md) when the requested slug already has an
    active ``Organization`` row. Maps to HTTP 409, not part of the
    ``AuthenticationError`` hierarchy -- same reasoning as
    ``EmailAlreadyRegisteredError`` above: a uniqueness conflict, not a
    credential failure."""


class MemberNotFoundError(LookupError):
    """Raised by the member-management use cases (Phase 6 M2) when the
    target user is not an ACTIVE member of the organization in the URL
    (absent, already ``REMOVED``, or a member of a different
    organization -- which RLS makes invisible anyway). Subclasses
    ``LookupError`` so the existing global handler maps it to HTTP 404; it
    is not part of the ``AuthenticationError`` hierarchy."""


class UserNotFoundError(LookupError):
    """Raised by ``AddMemberUseCase`` when no ACTIVE registered user has
    the requested email. Maps to HTTP 404 via ``LookupError``. The message
    deliberately does not echo the address. Note this is the documented
    user-enumeration trade-off of direct add-by-email: an actor who is
    allowed to add can learn whether an address is registered."""


class MembershipConflictError(ValueError):
    """Base for member-management requests that conflict with current
    membership state. Maps to HTTP 409 (one handler in ``app/main.py``
    covers every subclass), the same convention as
    ``EmailAlreadyRegisteredError``."""


class MemberAlreadyActiveError(MembershipConflictError):
    """Raised by ``AddMemberUseCase`` when the user already has an
    ACTIVE membership in the organization."""


class LastOwnerError(MembershipConflictError):
    """Raised when a role change or removal would leave the organization
    with zero ACTIVE OWNER memberships. Raised BEFORE anything is written,
    under the row lock taken by ``lock_for_membership_change``."""


class MemberManagementForbiddenError(Exception):
    """Raised by the member-management use cases when the actor's CURRENT
    membership (re-read under the lock) does not authorize the operation:
    not an ACTIVE member, not an OWNER/ADMIN, or an ADMIN attempting to
    grant or touch ADMIN/OWNER. Maps to HTTP 403. Deliberately not a
    ``ValueError``/``LookupError`` subclass, so it can never be caught by
    the 404/409 handlers registered for those bases."""
