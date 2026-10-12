"""Unit tests for the membership audit trail (Phase 6 M3).

Reuses the faithful fakes from ``test_manage_members.py``. The audit fake
shares the organization fake's ordered ``events`` log here, so ordering
("lock", then the mutation, then the audit write) is asserted directly.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
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
    RemoveMemberUseCase,
)
from app.domain.identity.audit import MEMBER_AUDIT_RESOURCE_TYPE, MemberAuditAction
from app.domain.identity.entities import AuditLogEntry, User
from app.domain.shared.ids import new_id
from tests.unit.test_manage_members import (
    ADMIN,
    MEMBER,
    OWNER,
    REMOVED,
    VIEWER,
    Env,
    FakeAuditLogRepository,
)


@pytest.fixture
def env() -> Env:
    environment = Env()
    environment.audit = FakeAuditLogRepository(events=environment.orgs.events)
    return environment


def _only(env: Env) -> AuditLogEntry:
    assert len(env.audit.entries) == 1, "exactly one audit entry must be written"
    return env.audit.entries[0]


def _check_common(
    env: Env, entry: AuditLogEntry, action: MemberAuditAction, actor: User, member_id: UUID
) -> None:
    assert entry.action == action.value
    assert entry.organization_id == env.org_id
    assert entry.actor_user_id == actor.id
    assert entry.resource_type == MEMBER_AUDIT_RESOURCE_TYPE == "organization_member"
    assert entry.resource_id == member_id
    assert entry.created_at is not None


# ============================================================ domain


def test_the_four_approved_actions_are_exactly_these_strings_in_this_order() -> None:
    assert [action.value for action in MemberAuditAction] == [
        "member.added",
        "member.reactivated",
        "member.role_changed",
        "member.removed",
    ]


@pytest.mark.parametrize(
    ("action", "value"),
    [
        (MemberAuditAction.ADDED, "member.added"),
        (MemberAuditAction.REACTIVATED, "member.reactivated"),
        (MemberAuditAction.ROLE_CHANGED, "member.role_changed"),
        (MemberAuditAction.REMOVED, "member.removed"),
    ],
)
def test_each_action_has_its_exact_stored_value(action: MemberAuditAction, value: str) -> None:
    assert action.value == value
    assert str(action) == value  # StrEnum: usable directly as the stored string


def test_actions_follow_the_resource_dot_verb_convention() -> None:
    for action in MemberAuditAction:
        resource, _, verb = action.value.partition(".")
        assert resource == "member"
        assert verb and "." not in verb and verb == verb.lower()


# ============================================== successful mutations


async def test_add_writes_exactly_one_member_added_entry_after_the_mutation(env: Env) -> None:
    owner = env.seed(OWNER)
    newcomer = env.user_without_membership("new@example.com")

    await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="new@example.com", role=VIEWER
    )

    entry = _only(env)
    _check_common(env, entry, MemberAuditAction.ADDED, owner, env.membership(newcomer).id)
    assert entry.metadata == {"target_user_id": str(newcomer.id), "role": "viewer"}
    assert env.orgs.events == ["lock", "add_member", "audit"]


async def test_reactivation_writes_member_reactivated_not_member_added(env: Env) -> None:
    owner = env.seed(OWNER)
    returning = env.seed(MEMBER, status=REMOVED, email="back@example.com")
    original_row = env.membership(returning).id

    await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="back@example.com", role=ADMIN
    )

    entry = _only(env)
    _check_common(env, entry, MemberAuditAction.REACTIVATED, owner, original_row)
    assert entry.action != MemberAuditAction.ADDED.value
    assert entry.metadata == {"target_user_id": str(returning.id), "role": "admin"}
    assert env.orgs.events == ["lock", "update_member", "audit"]


async def test_role_change_writes_member_role_changed_with_previous_and_new_role(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(MEMBER)

    await env.change_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target.id, new_role=ADMIN
    )

    entry = _only(env)
    _check_common(env, entry, MemberAuditAction.ROLE_CHANGED, owner, env.membership(target).id)
    assert entry.metadata == {
        "target_user_id": str(target.id),
        "previous_role": "member",
        "new_role": "admin",
    }
    assert env.orgs.events == ["lock", "update_member", "audit"]


async def test_removal_writes_member_removed_with_the_previous_role(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(VIEWER)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target.id
    )

    entry = _only(env)
    _check_common(env, entry, MemberAuditAction.REMOVED, owner, env.membership(target).id)
    assert entry.metadata == {"target_user_id": str(target.id), "previous_role": "viewer"}
    assert env.orgs.events == ["lock", "update_member", "audit"]


async def test_an_admins_authorized_mutation_is_attributed_to_the_admin(env: Env) -> None:
    admin = env.seed(ADMIN)
    target = env.seed(MEMBER)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=admin.id, target_user_id=target.id
    )

    assert _only(env).actor_user_id == admin.id


async def test_removing_a_former_owner_records_owner_as_the_previous_role(env: Env) -> None:
    first = env.seed(OWNER)
    second = env.seed(OWNER)

    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=first.id, target_user_id=second.id
    )

    entry = _only(env)
    assert entry.metadata == {"target_user_id": str(second.id), "previous_role": "owner"}


async def test_each_successful_mutation_in_a_sequence_adds_exactly_one_entry(env: Env) -> None:
    owner = env.seed(OWNER)
    env.user_without_membership("n@example.com")
    newcomer_id = next(u.id for u in env.users.users.values() if u.email == "n@example.com")

    await env.add_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, email="n@example.com", role=MEMBER
    )
    await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        target_user_id=newcomer_id,
        new_role=VIEWER,
    )
    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=newcomer_id
    )

    assert [e.action for e in env.audit.entries] == [
        "member.added",
        "member.role_changed",
        "member.removed",
    ]


# ================================================== metadata is ids only


async def _every_audited_operation(env: Env) -> list[AuditLogEntry]:
    """Run all four audited operations against users carrying a distinctive
    email and full name, and return the four entries."""
    owner = env.seed(OWNER, email="owner.secret@example.com")
    env.user_without_membership("added.secret@example.com")
    changed = env.seed(MEMBER, email="changed.secret@example.com")
    removed = env.seed(VIEWER, email="removed.secret@example.com")
    back = env.seed(MEMBER, status=REMOVED, email="back.secret@example.com")
    assert back.id != changed.id

    await env.add_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        email="added.secret@example.com",
        role=VIEWER,
    )
    await env.add_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        email="back.secret@example.com",
        role=MEMBER,
    )
    await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        target_user_id=changed.id,
        new_role=ADMIN,
    )
    await env.remove_uc().execute(
        organization_id=env.org_id, actor_user_id=owner.id, target_user_id=removed.id
    )
    return list(env.audit.entries)


async def test_audit_entries_never_contain_an_email_or_a_name(env: Env) -> None:
    entries = await _every_audited_operation(env)

    assert [e.action for e in entries] == [
        "member.added",
        "member.reactivated",
        "member.role_changed",
        "member.removed",
    ]
    for entry in entries:
        everything = repr(entry) + json.dumps(entry.metadata)
        assert "@" not in everything
        assert "secret" not in everything
        assert "Some Person" not in everything  # the fake users' full name
        assert "never-exposed" not in everything  # the fake users' password hash


async def test_metadata_keys_are_exactly_the_documented_contract(env: Env) -> None:
    entries = await _every_audited_operation(env)

    keys = {e.action: set(e.metadata or {}) for e in entries}
    assert keys == {
        "member.added": {"target_user_id", "role"},
        "member.reactivated": {"target_user_id", "role"},
        "member.role_changed": {"target_user_id", "previous_role", "new_role"},
        "member.removed": {"target_user_id", "previous_role"},
    }


async def test_metadata_values_are_plain_json_safe_strings(env: Env) -> None:
    entries = await _every_audited_operation(env)

    for entry in entries:
        assert entry.metadata is not None
        assert all(isinstance(value, str) for value in entry.metadata.values())
        assert json.loads(json.dumps(entry.metadata)) == entry.metadata


# ================================= no entry on refused / failed / no-op


async def test_no_entry_when_the_actor_lacks_permission(env: Env) -> None:
    env.seed(OWNER)
    admin = env.seed(ADMIN)
    viewer = env.seed(VIEWER)
    other_admin = env.seed(ADMIN)
    owner_target = env.seed(OWNER)
    env.user_without_membership("n@example.com")

    refused: list[Callable[[], Awaitable[object]]] = [
        lambda: env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=viewer.id, email="n@example.com", role=VIEWER
        ),
        lambda: env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=admin.id, email="n@example.com", role=ADMIN
        ),
        lambda: env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=admin.id,
            target_user_id=other_admin.id,
            new_role=MEMBER,
        ),
        lambda: env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=admin.id, target_user_id=owner_target.id
        ),
        lambda: env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=viewer.id, target_user_id=admin.id
        ),
    ]
    for attempt in refused:
        with pytest.raises(MemberManagementForbiddenError):
            await attempt()

    assert env.audit.entries == []


async def test_no_entry_for_a_removed_or_absent_actor(env: Env) -> None:
    env.seed(OWNER)
    removed_owner = env.seed(OWNER, status=REMOVED)
    stranger = env.user_without_membership("stranger@example.com")
    target = env.seed(MEMBER)

    for actor in (removed_owner, stranger):
        with pytest.raises(MemberManagementForbiddenError):
            await env.remove_uc().execute(
                organization_id=env.org_id, actor_user_id=actor.id, target_user_id=target.id
            )

    assert env.audit.entries == []


async def test_no_entry_for_an_unknown_or_inactive_user(env: Env) -> None:
    owner = env.seed(OWNER)
    env.user_without_membership("dormant@example.com", active=False)

    for email in ("nobody@example.com", "dormant@example.com"):
        with pytest.raises(UserNotFoundError):
            await env.add_uc().execute(
                organization_id=env.org_id, actor_user_id=owner.id, email=email, role=VIEWER
            )

    assert env.audit.entries == []


async def test_no_entry_for_an_unknown_removed_or_foreign_member(env: Env) -> None:
    owner = env.seed(OWNER)
    removed = env.seed(MEMBER, status=REMOVED)
    foreign = env.seed(MEMBER, org_id=new_id())

    for target_id in (new_id(), removed.id, foreign.id):
        with pytest.raises(MemberNotFoundError):
            await env.change_uc().execute(
                organization_id=env.org_id,
                actor_user_id=owner.id,
                target_user_id=target_id,
                new_role=VIEWER,
            )
        with pytest.raises(MemberNotFoundError):
            await env.remove_uc().execute(
                organization_id=env.org_id, actor_user_id=owner.id, target_user_id=target_id
            )

    assert env.audit.entries == []


async def test_no_entry_when_the_user_is_already_an_active_member(env: Env) -> None:
    owner = env.seed(OWNER)
    env.seed(MEMBER, email="m@example.com")

    with pytest.raises(MemberAlreadyActiveError):
        await env.add_uc().execute(
            organization_id=env.org_id, actor_user_id=owner.id, email="m@example.com", role=ADMIN
        )

    assert env.audit.entries == []


async def test_no_entry_when_the_last_owner_guard_refuses(env: Env) -> None:
    sole = env.seed(OWNER)

    with pytest.raises(LastOwnerError):
        await env.change_uc().execute(
            organization_id=env.org_id,
            actor_user_id=sole.id,
            target_user_id=sole.id,
            new_role=VIEWER,
        )
    with pytest.raises(LastOwnerError):
        await env.remove_uc().execute(
            organization_id=env.org_id, actor_user_id=sole.id, target_user_id=sole.id
        )

    assert env.audit.entries == []


async def test_no_entry_for_a_no_op_role_change(env: Env) -> None:
    owner = env.seed(OWNER)
    target = env.seed(MEMBER)

    await env.change_uc().execute(
        organization_id=env.org_id,
        actor_user_id=owner.id,
        target_user_id=target.id,
        new_role=MEMBER,
    )

    assert env.audit.entries == []
    assert env.orgs.events == ["lock"]  # no write of any kind


# ======================================== required + fail-closed (unit)


class _AuditBoom(Exception):
    pass


class _ExplodingAudit(FakeAuditLogRepository):
    async def add(self, entry: AuditLogEntry) -> None:
        raise _AuditBoom("audit insert failed")


def test_the_audit_repository_is_a_required_constructor_argument(env: Env) -> None:
    with pytest.raises(TypeError):
        AddMemberUseCase(env.orgs, env.users)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ChangeMemberRoleUseCase(env.orgs, env.users)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        RemoveMemberUseCase(env.orgs)  # type: ignore[call-arg]


async def test_an_audit_failure_propagates_from_every_mutation_and_is_never_swallowed(
    env: Env,
) -> None:
    owner = env.seed(OWNER)
    env.user_without_membership("n@example.com")
    member = env.seed(MEMBER)
    exploding = _ExplodingAudit()

    with pytest.raises(_AuditBoom):
        await AddMemberUseCase(env.orgs, env.users, exploding).execute(
            organization_id=env.org_id, actor_user_id=owner.id, email="n@example.com", role=VIEWER
        )
    with pytest.raises(_AuditBoom):
        await ChangeMemberRoleUseCase(env.orgs, env.users, exploding).execute(
            organization_id=env.org_id,
            actor_user_id=owner.id,
            target_user_id=member.id,
            new_role=VIEWER,
        )
    with pytest.raises(_AuditBoom):
        await RemoveMemberUseCase(env.orgs, exploding).execute(
            organization_id=env.org_id, actor_user_id=owner.id, target_user_id=member.id
        )


async def test_a_refusal_is_raised_before_the_audit_is_ever_reached(env: Env) -> None:
    """With an exploding audit repository, a request that is refused still
    raises its own domain error (never the audit one): refusal happens
    before any mutation, and nothing reaches the audit write."""
    viewer = env.seed(VIEWER)
    target = env.seed(MEMBER)

    with pytest.raises(MemberManagementForbiddenError):
        await RemoveMemberUseCase(env.orgs, _ExplodingAudit()).execute(
            organization_id=env.org_id, actor_user_id=viewer.id, target_user_id=target.id
        )
