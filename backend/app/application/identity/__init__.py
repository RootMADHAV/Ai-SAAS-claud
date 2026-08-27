"""Identity use cases.

Authentication (resolving Technical Debt #9): ``register_user.py``
(``RegisterUserUseCase`` -- creates a ``User`` with a hashed password),
``login_user.py`` (``LoginUseCase`` -- verifies credentials and issues an
access/refresh token pair), ``refresh_token.py`` (``RefreshTokenUseCase``
-- rotates a presented refresh token for a fresh access/refresh pair),
``tokens.py`` (``TokenPair`` plus the ``issue_token_pair`` helper shared
by the two token-issuing use cases above), and ``errors.py`` (the
exception hierarchy the API layer's global exception handlers map to
HTTP status codes). ``invite_member`` (org invitations) remains unbuilt
-- out of this work's approved scope (Technical Debt #9 only);
Organization/OrganizationMember creation and management stay future
work, per PROJECT_STATE.md's own Milestone 5 design-decision note on why
an organization-creation endpoint was deferred.
"""
