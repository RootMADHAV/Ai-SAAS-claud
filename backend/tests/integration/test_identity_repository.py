"""Integration tests for the Identity & Access repository implementations.

Runs against a real PostgreSQL database (see tests/conftest.py) --
skipped automatically if none is reachable. These are deliberately the
tests that prove Row-Level Security actually isolates tenants; every
other integration module in this suite exercises CRUD correctness but
does not re-prove the RLS mechanism itself, since it is identical
policy machinery on every org-scoped table (PROJECT_STATE.md section 3).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.entities import AuditLogEntry, OrganizationMember
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from tests.integration.support import make_organization, make_user, set_org_context

pytestmark = pytest.mark.integration


async def test_add_and_get_organization_by_id(db_session: AsyncSession) -> None:
    org = make_organization(name="Acme Security")
    await set_org_context(db_session, org.id)
    repo = SqlAlchemyOrganizationRepository(db_session)

    await repo.add(org)
    fetched = await repo.get_by_id(org.id)

    assert fetched is not None
    assert fetched.id == org.id
    assert fetched.name == "Acme Security"
    assert fetched.slug == org.slug
    assert fetched.deleted_at is None


async def test_get_by_slug(db_session: AsyncSession) -> None:
    org = make_organization(slug="unique-slug-value")
    await set_org_context(db_session, org.id)
    repo = SqlAlchemyOrganizationRepository(db_session)
    await repo.add(org)

    fetched = await repo.get_by_slug("unique-slug-value")

    assert fetched is not None
    assert fetched.id == org.id


async def test_get_by_id_returns_none_for_unknown_id(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    repo = SqlAlchemyOrganizationRepository(db_session)

    assert await repo.get_by_id(new_id()) is None


async def test_update_persists_name_and_slug_changes(db_session: AsyncSession) -> None:
    org = make_organization(name="Old Name", slug="old-slug")
    await set_org_context(db_session, org.id)
    repo = SqlAlchemyOrganizationRepository(db_session)
    await repo.add(org)

    org.name = "New Name"
    org.slug = "new-slug"
    await repo.update(org)

    reloaded = await repo.get_by_id(org.id)
    assert reloaded is not None
    assert reloaded.name == "New Name"
    assert reloaded.slug == "new-slug"


async def test_soft_delete_hides_organization_under_its_own_rls_context(
    db_session: AsyncSession,
) -> None:
    """Soft delete plus the repository-level ``deleted_at IS NULL`` filter
    (app/infrastructure/db/repositories/identity_repository.py -- moved
    out of RLS for the reason documented in the initial-schema migration's
    DESIGN NOTE) means a deleted org disappears even from a session
    scoped to that exact org -- there is no "see my own deleted row"
    escape hatch, by design."""
    org = make_organization()
    await set_org_context(db_session, org.id)
    repo = SqlAlchemyOrganizationRepository(db_session)
    await repo.add(org)

    # This UPDATE is exactly the operation that failed with "new row
    # violates row-level security policy" before the RLS/soft-delete
    # split documented in the migration's DESIGN NOTE -- asserting it
    # succeeds is a regression test for that specific, real Postgres
    # limitation, not just a happy-path check.
    await repo.soft_delete(org.id)

    assert await repo.get_by_id(org.id) is None


async def test_rls_isolates_organizations_between_tenants(db_session: AsyncSession) -> None:
    """The core tenant-isolation proof: a session scoped to org A can
    add and read its own row, but a session scoped to org B never sees
    it -- enforced by Postgres itself (FORCE ROW LEVEL SECURITY), not by
    any WHERE clause this repository writes."""
    org_a = make_organization(name="Tenant A")
    org_b = make_organization(name="Tenant B")
    repo = SqlAlchemyOrganizationRepository(db_session)

    await set_org_context(db_session, org_a.id)
    await repo.add(org_a)
    assert await repo.get_by_id(org_a.id) is not None

    await set_org_context(db_session, org_b.id)
    assert await repo.get_by_id(org_a.id) is None

    await repo.add(org_b)
    assert await repo.get_by_id(org_b.id) is not None


async def test_organization_membership_lifecycle(db_session: AsyncSession) -> None:
    org = make_organization()
    user = make_user()
    await set_org_context(db_session, org.id)
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    await org_repo.add(org)
    await user_repo.add(user)

    member = OrganizationMember(
        id=new_id(),
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.OWNER,
        status=MembershipStatus.ACTIVE,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
    await org_repo.add_member(member)

    fetched = await org_repo.get_member(org.id, user.id)
    assert fetched is not None
    assert fetched.role is OrganizationRole.OWNER

    members = await org_repo.list_members(org.id)
    assert [m.user_id for m in members] == [user.id]

    assert await org_repo.count_active_owners(org.id) == 1

    fetched.role = OrganizationRole.ADMIN
    await org_repo.update_member(fetched)
    updated = await org_repo.get_member(org.id, user.id)
    assert updated is not None
    assert updated.role is OrganizationRole.ADMIN
    assert await org_repo.count_active_owners(org.id) == 0


async def test_user_repository_lifecycle(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    user = make_user(email="someone@example.com")
    repo = SqlAlchemyUserRepository(db_session)

    await repo.add(user)
    fetched = await repo.get_by_email("someone@example.com")
    assert fetched is not None
    assert fetched.id == user.id

    fetched.full_name = "Updated Name"
    await repo.update(fetched)
    assert (await repo.get_by_id(user.id)).full_name == "Updated Name"  # type: ignore[union-attr]

    await repo.soft_delete(user.id)
    assert await repo.get_by_id(user.id) is None


async def test_audit_log_add_and_list_for_organization(db_session: AsyncSession) -> None:
    org = make_organization()
    await set_org_context(db_session, org.id)
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    await org_repo.add(org)

    audit_repo = SqlAlchemyAuditLogRepository(db_session)
    entry = AuditLogEntry(
        id=new_id(),
        action="organization.created",
        created_at=org.created_at,
        organization_id=org.id,
        metadata={"source": "integration-test"},
    )
    await audit_repo.add(entry)

    entries = await audit_repo.list_for_organization(org.id)
    assert len(entries) == 1
    assert entries[0].action == "organization.created"
    assert entries[0].metadata == {"source": "integration-test"}
