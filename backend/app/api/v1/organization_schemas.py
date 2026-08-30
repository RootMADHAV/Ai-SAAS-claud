"""Pydantic request/response schemas for the Organization bootstrap API
(``POST /api/v1/organizations``) -- Phase 3 backend preparation, kept in
its own module the same "one module per bounded context's API DTOs"
split ``auth_schemas.py``'s own docstring already establishes, rather
than folded into that file: this is a different endpoint (no
credentials, requires an existing session) with its own request/response
shape.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Lowercase, hyphen-separated, no leading/trailing/double hyphen -- the
#: same shape ``make_organization``'s own test fixtures
#: (tests/integration/support.py) already produce, and the conventional
#: URL-safe slug format. Enforced here at the request-validation
#: boundary (a clean 422) rather than left to the database's plain
#: ``UniqueConstraint`` (which would only catch a *duplicate*, not a
#: malformed, slug).
_SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class OrganizationCreateRequest(BaseModel):
    """Request body for ``POST /api/v1/organizations``."""

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=63, pattern=_SLUG_PATTERN)


class OrganizationResponse(BaseModel):
    """Returned by ``POST /api/v1/organizations``. No membership/role
    info in the body -- the caller that just created this organization
    already knows they are its Owner; a general "who am I in this org"
    read is a future concern (member listing is not built yet, see
    ``OrganizationRepositoryPort.list_members``'s own docstring)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    slug: str
