"""Unit tests for the member-management use cases (Phase 6 M2).

The fake repository is deliberately faithful in the one way that matters
here: ``get_member`` returns a COPY, and nothing persists unless the use
case calls ``add_member``/``update_member`` -- exactly like the real
SQLAlchemy repository. A fake that handed back live objects would let a
use case "succeed" by mutating in place without ever writing. The fake
also records an ordered ``events`` log so tests can assert that the row
lock is taken FIRST and that failed operations performed no write.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

import pytest

from app.application.identity.errors import (
    LastOwnerError,
    MemberAlreadyActiveError,
    MemberManagementForbiddenError,
    MemberNotFoundError,
    UserNotFoundError,
)
from app.application.identity.manage_members import (
    AddMemberUseCase,
    ChangeMemberRoleUseCase,
    ListMembersUseCase,
    RemoveMemberUseCase,
)
from app.application.interfaces.identity_repository import (
    AuditLogRepositoryPort,
    OrganizationRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.entities import AuditLogEntry, Organization, OrganizationMember, User
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id

OWNER, ADMIN, MEMBER, VIEWER = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.MEMBER,
    OrganizationRole.VIEWER,
)
ACTIVE, REMOVED = MembershipStatus.ACTIVE, MembershipStatus.REMOVED
WRITES = {"add_member", "update_member"}


class FakeOrganizationRepository(OrganizationRepositoryPort):
    def __init__(self) -> None:
        self.members: dict[UUID, OrganizationMember] = {}
        self.events: list[str] = []

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        raise NotImplementedError

    async def get_by_slug(self, slug: str) -> Organization | None:
        raise NotImplementedError

    async def add(self, organization: Organization) -> None:
        raise NotImplementedError

    async def update(self, organization: Organization) -> None:
        raise NotImplementedError

    async def soft_delete(self, organization_id: UUID) -> None:
        raise NotImplementedError

    async def add_member(self, member: OrganizationMember) -> None:
        self.events.append("add_member")
        self.members[member.id] = dataclasses.replace(member)

    async def get_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember | None:
        for member in self.members.values():
            if member.organization_id == organization_id and member.user_id == user_id:
                return dataclasses.replace(member)
        return None

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        return [
            dataclasses.replace(m)
            for m in self.members.values()
            if m.organization_id == organization_id
        ]

    async def update_member(self, member: OrganizationMember) -> None:
        self.events.append("update_member")
        if member.id not in self.members:
            raise LookupError(f"OrganizationMember {member.id} does not exist")
        self.members[member.id] = dataclasses.replace(member)

    async def count_active_owners(self, organization_id: UUID) -> int:
        return sum(
            1
            for m in self.members.values()
            if m.organization_id == organization_id and m.role is OWNER and m.status is ACTIVE
        )

    async def lock_for_membership_change(self, organization_id: UUID) -> None:
        self.events.append("lock")


class FakeUserRepository(UserRepositoryPort):
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.email_lookups = 0

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        self.email_lookups += 1
        return next((u for u in self.users.values() if u.email == email), None)

    async def add(self, user: User) -> None:
        raise NotImplementedError

    async def update(self, user: User) -> None:
        raise NotImplementedError

    async def soft_delete(self, user_id: UUID) -> None:
        raise NotImplementedError


class FakeAuditLogRepository(AuditLogRepositoryPort):
    """Records entries. ``events`` is optional: when a list is supplied
    (shared with the organization fake's log) every write appends
    ``"audit"``, so a test can assert ordering against the mutation."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.entries: list[AuditLogEntry] = []
        self._events = events

    async def add(self, entry: AuditLogEntry) -> None:
        if self._events is not None:
            self._events.append("audit")
        self.entries.append(entry)

    async def list_for_organization(
        self, organization_id: UUID, limit: int = 100
    ) -> list[AuditLogEntry]:
        matching = [e for e in reversed(self.entries) if e.organization_id == organization_id]
        return matching[:limit]


@dataclass
class Env:
    org_id: UUID = field(default_factory=new_id)
    orgs: FakeOrganizationRepository = field(default_factory=FakeOrganizationRepository)
    users: FakeUserRepository = field(default_factory=FakeUserRepository)
    audit: FakeAuditLogRepository = field(default_factory=FakeAuditLogRepository)
    _tick: int = 0

    def seed(
        self,
        role: OrganizationRole,
        *,
        status: MembershipStatus = ACTIVE,
        email: str | None = None,
        active: bool = True,
        org_id: UUID | None = None,
    ) -> User:
        """Create a user plus a membership in ``org_id`` (default: this org)."""
        now = utcnow()
        user = User(
            id=new_id(),
            email=email or f"{new_id()}@example.com",
            full_name="Some Person",
            is_active=active,
            created_at=now,
            updated_at=now,
            hashed_password="never-exposed",
        )
        self.users.users[user.id] = user
        self._tick += 1
        created = now + timedelta(seconds=self._tick)
        member = OrganizationMember(
            id=new_id(),
            organization_id=org_id or self.org_id,
            user_id=user.id,
            role=role,
            status=status,
            created_at=created,
            updated_at=created,
        )
        self.orgs.members[member.id] = member
        return user

    def user_without_membership(self, email: str, *, active: bool = True) -> User:
        user = self.seed(MEMBER, email=email, active=active)
        for key in [k for k, m in self.orgs.members.items() if m.user_id == user.id]:
            del self.orgs.members[key]
        return user

    def snapshot(self) -> dict[UUID, OrganizationMember]:
        return {k: dataclasses.replace(v) for k, v in self.orgs.members.items()}

    def membership(self, user: User) -> OrganizationMember:
        member = next(m for m in self.orgs.members.values() if m.user_id == user.id)
        return member

    def active_owners(self) -> int:
        return sum(
            1
            for m in self.orgs.members.values()
            if m.organization_id == self.org_id and m.role is OWNER and m.status is ACTIVE
        )

    def add_uc(self) -> AddMemberUseCase:
        return AddMemberUseCase(self.orgs, self.users, self.audit)

    def change_uc(self) -> ChangeMemberRoleUseCase:
        return ChangeMemberRoleUseCase(self.orgs, self.users, self.audit)

    def remove_uc(self) -> RemoveMemberUseCase:
        return RemoveMemberUseCase(self.orgs, self.audit)

    def list_uc(self) -> ListMembersUseCase:
        return ListMembersUseCase(self.orgs, self.users)


@pytest.fixture
def env() -> Env:
    return Env()


# ===================================================================== list


async def test_list_returns_only_active_members_oldest_first_with_user_details(env: Env) -> None:
    first = env.seed(OWNER, email="first@example.com")
    second = env.seed(VIEWER, email="second@example.com")
    env.seed(MEMBER, status=REMOVED)
    env.seed(MEMBER, org_id=new_id())  # another organization's member

    views = await env.list_uc().execute(organization_id=env.org_id)

    assert [v.user.id for v in views] == [first.id, second.id]
    assert [v.member.role for v in views] == [OWNER, VIEWER]
    assert views[0].user.email == "first@example.com"


async def test_list_omits_a_membership_whose_user_no_longer_exists(env: Env) -> None:
    keep = env.seed(OWNER)
    gone = env.seed(MEMBER)
    del env.users.users[gone.id]

    views = await env.list_uc().execute(organization_id=env.org_id)

    assert [v.user.id for v in views] == [keep.id]


# ====================================================================== add


async def test_add_creates_an_active_membership_taking_the_lock_first(env: Env) -> None:
    owner = env.seed(OWNER)
    newcomer = env.user_without_membership("new@example.com")

    view = await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="new@example.com", role=VIEWER
    )

    assert view.user.id == newcomer.id
    assert view.member.role is VIEWER
    assert view.member.status is ACTIVE
    assert env.membership(newcomer).status is ACTIVE
    assert env.orgs.events == ["lock", "add_member"]


@pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER, VIEWER])
async def test_owner_may_add_any_role(env: Env, role: OrganizationRole) -> None:
    owner = env.seed(OWNER)
    newcomer = env.user_without_membership("n@example.com")

    await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="n@example.com", role=role
    )

    assert env.membership(newcomer).role is role


@pytest.mark.parametrize("role", [MEMBER, VIEWER])
async def test_admin_may_add_member_and_viewer(env: Env, role: OrganizationRole) -> None:
    admin = env.seed(ADMIN)
    newcomer = env.user_without_membership("n@example.com")

    await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=admin.id, email="n@example.com", role=role
    )

    assert env.membership(newcomer).role is role


@pytest.mark.parametrize("role", [ADMIN, OWNER])
async def test_admin_cannot_add_admin_or_owner_and_learns_nothing_about_the_email(
    env: Env, role: OrganizationRole
) -> None:
    admin = env.seed(ADMIN)
    env.user_without_membership("n@example.com")
    before = env.snapshot()

    with pytest.raises(MemberManagementForbiddenError):
        await env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=admin.id, email="n@example.com", role=role
        )

    assert env.users.email_lookups == 0  # authorization precedes any user lookup
    assert env.snapshot() == before
    assert not WRITES & set(env.orgs.events)


@pytest.mark.parametrize("actor_role", [MEMBER, VIEWER])
async def test_member_and_viewer_cannot_add_and_cannot_probe_emails(
    env: Env, actor_role: OrganizationRole
) -> None:
    actor = env.seed(actor_role)
    env.user_without_membership("n@example.com")

    with pytest.raises(MemberManagementForbiddenError):
        await env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=actor.id, email="n@example.com", role=VIEWER
        )

    assert env.users.email_lookups == 0


async def test_a_removed_or_absent_actor_cannot_add(env: Env) -> None:
    removed_owner = env.seed(OWNER, status=REMOVED)
    stranger = env.user_without_membership("stranger@example.com")
    env.user_without_membership("n@example.com")

    for actor in (removed_owner, stranger):
        with pytest.raises(MemberManagementForbiddenError):
            await env.add_uc().execute(
                organization_id=env.org_id,
                actor_user_id=actor.id,
                email="n@example.com",
                role=VIEWER,
            )


async def test_add_rejects_an_unknown_email_without_writing(env: Env) -> None:
    owner = env.seed(OWNER)
    before = env.snapshot()

    with pytest.raises(UserNotFoundError):
        await env.add_uc().execute(
            organization_id=env.org_id,
            actor_user_id=owner.id,
            email="nobody@example.com",
            role=VIEWER,
        )

    assert env.snapshot() == before
    assert not WRITES & set(env.orgs.events)


async def test_add_rejects_an_inactive_user_like_an_unknown_one(env: Env) -> None:
    owner = env.seed(OWNER)
    env.user_without_membership("dormant@example.com", active=False)
    before = env.snapshot()

    with pytest.raises(UserNotFoundError):
        await env.add_uc().execute(
            organization_id=env.org_id,
            actor_user_id=owner.id,
            email="dormant@example.com",
            role=VIEWER,
        )

    assert env.snapshot() == before


async def test_add_rejects_an_already_active_member_without_changing_it(env: Env) -> None:
    owner = env.seed(OWNER)
    existing = env.seed(MEMBER, email="m@example.com")
    before = env.snapshot()

    with pytest.raises(MemberAlreadyActiveError):
        await env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=owner.id, email="m@example.com", role=ADMIN
        )

    assert env.snapshot() == before
    assert env.membership(existing).role is MEMBER


async def test_add_reactivates_a_removed_membership_instead_of_inserting_a_duplicate(
    env: Env,
) -> None:
    owner = env.seed(OWNER)
    returning = env.seed(OWNER, status=REMOVED, email="back@example.com")
    original_id = env.membership(returning).id
    rows_before = len(env.orgs.members)

    view = await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="back@example.com", role=MEMBER
    )

    assert len(env.orgs.members) == rows_before  # no duplicate row
    assert view.member.id == original_id
    assert view.member.status is ACTIVE
    assert view.member.role is MEMBER  # the explicitly granted role, not the stale OWNER
    assert env.orgs.events == ["lock", "update_member"]


async def test_admin_may_reactivate_a_removed_former_owner_only_as_member_or_viewer(
    env: Env,
) -> None:
    admin = env.seed(ADMIN)
    env.seed(OWNER, status=REMOVED, email="old-owner@example.com")

    with pytest.raises(MemberManagementForbiddenError):
        await env.add_uc().execute(
            organization_id=env.org_id,
            actor_user_id=admin.id,
            email="old-owner@example.com",
            role=OWNER,
        )
    view = await env.add_uc().execute(
        organization_id=env.org_id,
        actor_user_id=admin.id,
        email="old-owner@example.com",
        role=VIEWER,
    )

    assert view.member.role is VIEWER


# ================================================================== change


async def test_owner_changes_a_role_taking_the_lock_first(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(MEMBER)

    view = await env.change_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target.id, new_role=ADMIN
    )

    assert view.member.role is ADMIN
    assert env.membership(target).role is ADMIN
    assert env.orgs.events == ["lock", "update_member"]


@pytest.mark.parametrize(("current", "new"), [(MEMBER, VIEWER), (VIEWER, MEMBER)])
async def test_admin_may_change_between_member_and_viewer(
    env: Env, current: OrganizationRole, new: OrganizationRole
) -> None:
    admin = env.seed(ADMIN)
    target = env.seed(current)

    await env.change_uc().execute(
        organization_id=env.org_id, actor_user_id=admin.id, target_user_id=target.id, new_role=new
    )

    assert env.membership(target).role is new


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (MEMBER, ADMIN),  # ADMIN cannot grant ADMIN
        (VIEWER, OWNER),  # ADMIN cannot grant OWNER
        (ADMIN, MEMBER),  # ADMIN cannot modify ADMIN
        (ADMIN, ADMIN),
        (OWNER, MEMBER),  # ADMIN cannot modify OWNER
        (OWNER, OWNER),
    ],
)
async def test_admin_cannot_escalate_or_touch_admin_and_owner(
    env: Env, current: OrganizationRole, new: OrganizationRole
) -> None:
    env.seed(OWNER)  # a second owner exists, so only the ROLE policy can refuse
    admin = env.seed(ADMIN)
    target = env.seed(current)
    before = env.snapshot()

    with pytest.raises(MemberManagementForbiddenError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=admin.id,
            target_user_id=target.id,
            new_role=new,
        )

    assert env.snapshot() == before
    assert not WRITES & set(env.orgs.events)


@pytest.mark.parametrize("actor_role", [MEMBER, VIEWER])
async def test_member_and_viewer_cannot_change_roles_or_escalate_themselves(
    env: Env, actor_role: OrganizationRole
) -> None:
    env.seed(OWNER)
    actor = env.seed(actor_role)
    before = env.snapshot()

    for new_role in (ADMIN, OWNER):
        with pytest.raises(MemberManagementForbiddenError):
            await env.change_uc().execute(
                organization_id=env.org_id,
                actor_user_id=actor.id,
                target_user_id=actor.id,
                new_role=new_role,
            )

    assert env.snapshot() == before


async def test_change_to_the_same_role_is_a_successful_no_op_without_a_write(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(MEMBER)

    view = await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        target_user_id=target.id,
        new_role=MEMBER,
    )

    assert view.member.role is MEMBER
    assert "update_member" not in env.orgs.events


async def test_change_role_target_not_found_cases(env: Env) -> None:
    owner = env.seed(OWNER)
    removed = env.seed(MEMBER, status=REMOVED)
    other_org_member = env.seed(MEMBER, org_id=new_id())

    for target_id in (new_id(), removed.id, other_org_member.id):
        with pytest.raises(MemberNotFoundError):
            await env.change_uc().execute(
                organization_id=env.org_id,
                actor_user_id=owner.id,
                target_user_id=target_id,
                new_role=VIEWER,
            )


async def test_change_role_of_a_member_whose_user_no_longer_exists_is_not_found(
    env: Env,
) -> None:
    owner = env.seed(OWNER)
    orphan = env.seed(MEMBER)
    del env.users.users[orphan.id]  # e.g. the user was soft-deleted
    before = env.snapshot()

    with pytest.raises(MemberNotFoundError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=owner.id,
            target_user_id=orphan.id,
            new_role=VIEWER,
        )

    assert env.snapshot() == before


async def test_sole_owner_cannot_demote_themselves_and_nothing_is_written(env: Env) -> None:
    owner = env.seed(OWNER)
    env.seed(MEMBER)
    before = env.snapshot()

    with pytest.raises(LastOwnerError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=owner.id,
            target_user_id=owner.id,
            new_role=ADMIN,
        )

    assert env.snapshot() == before
    assert env.active_owners() == 1
    assert env.orgs.events == ["lock"]


async def test_owner_may_demote_another_owner_and_then_is_the_protected_last_owner(
    env: Env,
) -> None:
    first = env.seed(OWNER)
    second = env.seed(OWNER)

    await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=first.id,
        target_user_id=second.id,
        new_role=VIEWER,
    )
    assert env.active_owners() == 1

    with pytest.raises(LastOwnerError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=first.id,
            target_user_id=first.id,
            new_role=MEMBER,
        )
    assert env.membership(first).role is OWNER


async def test_owner_may_demote_themselves_when_another_active_owner_remains(env: Env) -> None:
    first = env.seed(OWNER)
    env.seed(OWNER)

    view = await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=first.id,
        target_user_id=first.id,
        new_role=MEMBER,
    )

    assert view.member.role is MEMBER
    assert env.active_owners() == 1


async def test_a_removed_owner_does_not_count_towards_the_last_owner_protection(env: Env) -> None:
    sole_active = env.seed(OWNER)
    env.seed(OWNER, status=REMOVED)

    with pytest.raises(LastOwnerError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=sole_active.id,
            target_user_id=sole_active.id,
            new_role=VIEWER,
        )


# ================================================================== remove


async def test_owner_removes_a_member_by_status_not_by_deleting_the_row(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(MEMBER)
    rows_before = len(env.orgs.members)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target.id
    )

    assert len(env.orgs.members) == rows_before
    assert env.membership(target).status is REMOVED
    assert env.orgs.events == ["lock", "update_member"]


@pytest.mark.parametrize("target_role", [MEMBER, VIEWER])
async def test_admin_may_remove_member_and_viewer(env: Env, target_role: OrganizationRole) -> None:
    admin = env.seed(ADMIN)
    target = env.seed(target_role)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=admin.id, target_user_id=target.id
    )

    assert env.membership(target).status is REMOVED


@pytest.mark.parametrize("target_role", [ADMIN, OWNER])
async def test_admin_cannot_remove_admin_or_owner(env: Env, target_role: OrganizationRole) -> None:
    env.seed(OWNER)
    admin = env.seed(ADMIN)
    target = env.seed(target_role)
    before = env.snapshot()

    with pytest.raises(MemberManagementForbiddenError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=admin.id, target_user_id=target.id
        )

    assert env.snapshot() == before


async def test_admin_cannot_remove_themselves(env: Env) -> None:
    env.seed(OWNER)
    admin = env.seed(ADMIN)

    with pytest.raises(MemberManagementForbiddenError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=admin.id, target_user_id=admin.id
        )


@pytest.mark.parametrize("actor_role", [MEMBER, VIEWER])
async def test_member_and_viewer_cannot_remove_anyone(
    env: Env, actor_role: OrganizationRole
) -> None:
    env.seed(OWNER)
    actor = env.seed(actor_role)
    victim = env.seed(VIEWER)
    before = env.snapshot()

    for target in (victim, actor):
        with pytest.raises(MemberManagementForbiddenError):
            await env.remove_uc().execute(
                organization_id=env.org_id, actor_user_id=actor.id, target_user_id=target.id
            )

    assert env.snapshot() == before


async def test_remove_target_not_found_cases(env: Env) -> None:
    owner = env.seed(OWNER)
    already_removed = env.seed(MEMBER, status=REMOVED)
    other_org_member = env.seed(MEMBER, org_id=new_id())

    for target_id in (new_id(), already_removed.id, other_org_member.id):
        with pytest.raises(MemberNotFoundError):
            await env.remove_uc().execute(
                organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target_id
            )


async def test_sole_owner_cannot_remove_themselves_and_nothing_is_written(env: Env) -> None:
    owner = env.seed(OWNER)
    env.seed(MEMBER)
    before = env.snapshot()

    with pytest.raises(LastOwnerError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=owner.id, target_user_id=owner.id
        )

    assert env.snapshot() == before
    assert env.active_owners() == 1
    assert env.orgs.events == ["lock"]


async def test_owner_may_remove_another_owner_but_never_the_last_one(env: Env) -> None:
    first = env.seed(OWNER)
    second = env.seed(OWNER)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=first.id, target_user_id=second.id
    )
    assert env.active_owners() == 1

    with pytest.raises(LastOwnerError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=first.id, target_user_id=first.id
        )
    assert env.membership(first).status is ACTIVE


async def test_owner_may_remove_themselves_when_another_active_owner_remains(env: Env) -> None:
    first = env.seed(OWNER)
    second = env.seed(OWNER)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=first.id, target_user_id=first.id
    )

    assert env.membership(first).status is REMOVED
    assert env.membership(second).status is ACTIVE
    assert env.active_owners() == 1


# ======================================================== cross-cutting


async def test_every_mutation_takes_the_row_lock_before_anything_else(env: Env) -> None:
    """Even a request that is going to be refused takes the lock first, so
    the authorization and owner-count reads that follow are never stale."""
    owner = env.seed(OWNER)
    viewer = env.seed(VIEWER)
    env.user_without_membership("n@example.com")
    attempts = [
        env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=viewer.id, email="n@example.com", role=VIEWER
        ),
        env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=viewer.id,
            target_user_id=owner.id,
            new_role=VIEWER,
        ),
        env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=viewer.id, target_user_id=owner.id
        ),
    ]

    for attempt in attempts:
        env.orgs.events.clear()
        with pytest.raises(MemberManagementForbiddenError):
            await attempt
        assert env.orgs.events == ["lock"]


async def test_the_actor_is_re_read_from_the_repository_not_trusted_from_the_request(
    env: Env,
) -> None:
    """An actor demoted to VIEWER (e.g. by a concurrent request that held
    the lock first) is judged on their CURRENT role, whatever role the
    request pipeline saw before the lock wait."""
    actor = env.seed(OWNER)
    env.membership(actor).role = VIEWER  # the repository's current truth (stored row)
    victim = env.seed(MEMBER)

    with pytest.raises(MemberManagementForbiddenError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=actor.id, target_user_id=victim.id
        )
