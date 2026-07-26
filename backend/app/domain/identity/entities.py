"""Domain entities for the Identity & Access bounded context.

Scope note: PROJECT_STATE.md section 5 lists ``Organization`` /
``Membership`` as "designed... not yet coded" as of Milestone 1. Coding
them here is Milestone 2 work, not a Milestone 3+ encroachment, because
the repository ports this milestone must define (application/interfaces)
need a domain type to be typed against -- a port typed against a
SQLAlchemy model would leak an infrastructure concern into the
application layer, violating the dependency rule in PROJECT_STATE.md
section 1.

These are deliberately thin: fields and the invariants that are already
explicit in PROJECT_STATE.md, nothing invented. The one named invariant
-- "an organization must always have >= 1 Owner; the last one cannot be
removed" (section 5) -- is an aggregate-level rule that spans a
collection of ``OrganizationMember`` rows, not a single entity's
constructor; it belongs to an application-layer use case (a later
milestone), and is intentionally not fabricated here as an unenforced
method that would only create a false impression of being enforced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.shared.enums import MembershipStatus, OrganizationRole


@dataclass(slots=True)
class Organization:
    """The tenant boundary. Mutable (unlike the findings value objects):
    an entity has identity and a lifecycle, so equality and hashing by
    field value would be wrong -- two ``Organization`` instances with the
    same id represent the same organization even if one has a stale
    ``name``."""

    id: UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class User:
    """An account holder. Not org-scoped -- see identity.py's model
    docstring for why. ``hashed_password`` stays optional here for the
    same reason it is nullable at the persistence layer: OAuth2 is
    designed but its flows are deferred (PROJECT_STATE.md section 3)."""

    id: UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    hashed_password: str | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class AuditLogEntry:
    """Append-only audit record. ``organization_id`` is nullable to allow
    platform-level entries with no tenant context -- see the persistence
    model's docstring (app/infrastructure/db/models/identity.py) for why
    that does not create a cross-tenant visibility gap."""

    id: UUID
    action: str
    created_at: datetime
    organization_id: UUID | None = None
    actor_user_id: UUID | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    metadata: dict[str, object] | None = None


@dataclass(slots=True)
class OrganizationMember:
    """One user's membership in one organization. ``status`` is this
    entity's lifecycle field (PROJECT_STATE.md section 3) -- there is no
    ``deleted_at`` to mirror; removing a member is a status transition to
    ``MembershipStatus.REMOVED``, not a soft delete."""

    id: UUID
    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
