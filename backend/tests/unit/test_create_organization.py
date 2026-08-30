from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.identity.create_organization import CreateOrganizationUseCase
from app.application.identity.errors import OrganizationSlugAlreadyTakenError
from app.application.interfaces.identity_repository import OrganizationRepositoryPort
from app.domain.identity.entities import Organization, OrganizationMember
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id


class FakeOrganizationRepository(OrganizationRepositoryPort):
    """``add()`` raises ``IntegrityError`` on a duplicate slug -- mirrors
    the real ``organizations.slug`` UNIQUE constraint (enforced globally
    at the database level, unlike RLS's row-visibility filtering -- see
    CreateOrganizationUseCase's own module docstring), since that
    constraint violation, not a pre-check, is what this use case now
    relies on to detect a duplicate."""

    def __init__(self) -> None:
        self.organizations: dict[UUID, Organization] = {}
        self.members: dict[UUID, OrganizationMember] = {}

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        return self.organizations.get(organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        for org in self.organizations.values():
            if org.slug == slug and not org.is_deleted:
                return org
        return None

    async def add(self, organization: Organization) -> None:
        if any(org.slug == organization.slug for org in self.organizations.values()):
            raise IntegrityError(
                statement="INSERT INTO organizations ...",
                params={},
                orig=Exception(
                    'duplicate key value violates unique constraint "organizations_slug_key"'
                ),
            )
        self.organizations[organization.id] = organization

    async def update(self, organization: Organization) -> None:
        if organization.id not in self.organizations:
            raise LookupError(f"Organization {organization.id} does not exist")
        self.organizations[organization.id] = organization

    async def soft_delete(self, organization_id: UUID) -> None:
        raise NotImplementedError

    async def add_member(self, member: OrganizationMember) -> None:
        self.members[member.id] = member

    async def get_member(
        self, organization_id: UUID, user_id: UUID
    ) -> OrganizationMember | None:
        for member in self.members.values():
            if member.organization_id == organization_id and member.user_id == user_id:
                return member
        return None

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        return [m for m in self.members.values() if m.organization_id == organization_id]

    async def update_member(self, member: OrganizationMember) -> None:
        if member.id not in self.members:
            raise LookupError(f"OrganizationMember {member.id} does not exist")
        self.members[member.id] = member

    async def count_active_owners(self, organization_id: UUID) -> int:
        return sum(
            1
            for m in self.members.values()
            if m.organization_id == organization_id
            and m.role is OrganizationRole.OWNER
            and m.status is MembershipStatus.ACTIVE
        )


@pytest.fixture
def organization_repository() -> FakeOrganizationRepository:
    return FakeOrganizationRepository()


async def test_create_organization_creates_the_organization(
    organization_repository: FakeOrganizationRepository,
) -> None:
    use_case = CreateOrganizationUseCase(organization_repository)
    org_id = new_id()
    owner_id = new_id()

    organization = await use_case.execute(
        organization_id=org_id, name="Acme Security", slug="acme-security", owner_user_id=owner_id
    )

    assert organization.id == org_id
    assert organization.name == "Acme Security"
    assert organization.slug == "acme-security"
    assert organization_repository.organizations[org_id].slug == "acme-security"


async def test_create_organization_makes_the_caller_an_active_owner(
    organization_repository: FakeOrganizationRepository,
) -> None:
    use_case = CreateOrganizationUseCase(organization_repository)
    org_id = new_id()
    owner_id = new_id()

    await use_case.execute(
        organization_id=org_id, name="Acme Security", slug="acme-security", owner_user_id=owner_id
    )

    member = await organization_repository.get_member(org_id, owner_id)
    assert member is not None
    assert member.role is OrganizationRole.OWNER
    assert member.status is MembershipStatus.ACTIVE


async def test_create_organization_rejects_a_duplicate_slug(
    organization_repository: FakeOrganizationRepository,
) -> None:
    use_case = CreateOrganizationUseCase(organization_repository)
    await use_case.execute(
        organization_id=new_id(), name="First Org", slug="dupe-slug", owner_user_id=new_id()
    )

    with pytest.raises(OrganizationSlugAlreadyTakenError, match="dupe-slug"):
        await use_case.execute(
            organization_id=new_id(), name="Second Org", slug="dupe-slug", owner_user_id=new_id()
        )


async def test_create_organization_does_not_add_a_membership_when_the_slug_is_taken(
    organization_repository: FakeOrganizationRepository,
) -> None:
    """The failed INSERT means no Organization row exists to attach a
    membership to -- confirms add_member is never reached after add()
    raises."""
    use_case = CreateOrganizationUseCase(organization_repository)
    await use_case.execute(
        organization_id=new_id(), name="First Org", slug="dupe-slug", owner_user_id=new_id()
    )
    second_owner_id = new_id()

    with pytest.raises(OrganizationSlugAlreadyTakenError):
        await use_case.execute(
            organization_id=new_id(),
            name="Second Org",
            slug="dupe-slug",
            owner_user_id=second_owner_id,
        )

    assert not any(m.user_id == second_owner_id for m in organization_repository.members.values())


async def test_create_organization_allows_the_same_slug_after_the_first_org_is_deleted(
    organization_repository: FakeOrganizationRepository,
) -> None:
    """Soft-deleting the fake's in-memory row directly (bypassing
    ``get_by_slug``, which this use case no longer calls -- see module
    docstring) to document the intended real-world behavior: the
    database's UNIQUE(slug) constraint is on the live column value, so a
    genuinely deleted row's slug freeing up for reuse is a property of
    whatever the real soft-delete implementation does to that column
    (out of this use case's own scope -- no soft-delete path exists for
    Organization yet), not something this test can fully exercise
    end-to-end without one existing."""
    use_case = CreateOrganizationUseCase(organization_repository)
    first_id = new_id()
    await use_case.execute(
        organization_id=first_id, name="First Org", slug="reusable-slug", owner_user_id=new_id()
    )
    del organization_repository.organizations[first_id]

    organization = await use_case.execute(
        organization_id=new_id(), name="Second Org", slug="reusable-slug", owner_user_id=new_id()
    )
    assert organization.slug == "reusable-slug"
