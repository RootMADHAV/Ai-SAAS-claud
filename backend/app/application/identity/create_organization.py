"""create_organization use case: bootstraps a new Organization and adds
the requesting, already-authenticated user as its first ACTIVE Owner --
the minimum mechanism this codebase's own register/login flow needs to
reach a usable organization_id at all. register_user.py's own docstring
says "A registered user is added to an organization by a future invite/
membership flow" -- that flow, where an *existing* member invites
someone else, remains future work; this is not that. This is the one
case an invite flow structurally cannot cover: the very first member of
a brand-new organization, who by definition has no existing member to
invite them.

Deliberately narrow: no organization update/rename, no member listing/
removal, no role changes beyond "creator becomes Owner" -- see
PROJECT_STATE.md's Phase 3 backend-preparation note for why this one
bootstrap capability, and nothing else, was added ahead of Phase 3.

Requires a pre-generated ``organization_id`` (unlike every other use
case in this package, which generates its own id inside ``execute()``)
because ``organizations``' own RLS policy is self-referential (``id =
current_setting('app.current_org_id')::uuid`` -- see the initial
migration's module docstring): the id must be known and set as the
session's org context *before* the INSERT that creates the row, which in
turn must happen before this use case is even constructed (session
scoping is a FastAPI dependency concern -- app/api/dependencies.py's
``get_new_organization_id``/``get_org_bootstrap_session``). This use
case only ever sees the id after that choice has already been made
further up the call stack.

RLS correction (found while verifying this work against a real,
non-superuser Postgres role -- superuser bypasses RLS entirely and
masked this during earlier, less rigorous verification): duplicate-slug
detection cannot be a ``get_by_slug`` pre-check, the way
``RegisterUserUseCase``'s duplicate-email check works. ``organizations``'
RLS policy is self-referential (``id = current_setting(...)``), and this
use case's session is scoped to the *new*, not-yet-existing
organization's own id -- so any query against ``organizations`` from
inside this use case can only ever see a row whose id equals the new
org's id, never any *other* organization's row, regardless of what
``slug`` values already exist elsewhere. A ``get_by_slug`` pre-check
here would silently return "not found" for every duplicate slug
belonging to a different organization -- exactly the case it would
exist to catch -- and let the INSERT through to fail with a raw,
unhandled ``IntegrityError`` (an opaque 500, not the clean 409 callers
should get).

The real, reliable uniqueness guarantee is the database's own
``UNIQUE(slug)`` constraint on ``organizations`` -- enforced globally,
not row-visibility-filtered the way RLS is. This use case relies on
that directly: ``add()`` is wrapped, and a duplicate-key violation is
translated into ``OrganizationSlugAlreadyTakenError`` here -- the exact
fix Technical Debt #8 (PROJECT_STATE.md) already prescribes for an
analogous RLS/uniqueness interaction ("catch the unique-constraint
violation"), applied for real here rather than left as documented future
work.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.application.identity.errors import OrganizationSlugAlreadyTakenError
from app.application.interfaces.identity_repository import OrganizationRepositoryPort
from app.domain.identity.entities import Organization, OrganizationMember
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id


class CreateOrganizationUseCase:
    def __init__(self, organization_repository: OrganizationRepositoryPort) -> None:
        self._organization_repository = organization_repository

    async def execute(
        self, *, organization_id: UUID, name: str, slug: str, owner_user_id: UUID
    ) -> Organization:
        """Raises ``OrganizationSlugAlreadyTakenError`` if ``slug`` is
        already in use by another organization -- detected via the
        database's own unique-constraint violation on the INSERT, not a
        pre-check (see module docstring for why a pre-check cannot work
        reliably here)."""
        now = utcnow()
        organization = Organization(
            id=organization_id, name=name, slug=slug, created_at=now, updated_at=now
        )
        try:
            await self._organization_repository.add(organization)
        except IntegrityError as exc:
            raise OrganizationSlugAlreadyTakenError(
                f"organization slug {slug!r} is already taken"
            ) from exc

        owner_membership = OrganizationMember(
            id=new_id(),
            organization_id=organization_id,
            user_id=owner_user_id,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        await self._organization_repository.add_member(owner_membership)

        return organization
