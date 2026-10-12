"""Unit tests for the member-management rules in
``app/domain/identity/access.py`` -- Phase 6 Milestone 2.

The expected outcomes below are written as independent literal tables
(who may do what), NOT derived from the implementation's own rule helper,
so these tests can disagree with the code. They cover every combination
of actor role, target's current role, requested new role, and operation.
"""

from __future__ import annotations

import pytest

from app.domain.identity.access import (
    SCAN_WRITE_ROLES,
    can_add_member,
    can_change_member_role,
    can_manage_members,
    can_remove_member,
    can_write_scans,
)
from app.domain.shared.enums import OrganizationRole

OWNER, ADMIN, MEMBER, VIEWER = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.MEMBER,
    OrganizationRole.VIEWER,
)
ALL_ROLES = (OWNER, ADMIN, MEMBER, VIEWER)

# --- expected policy, written out by hand --------------------------------

EXPECTED_MANAGERS = {OWNER, ADMIN}

# actor -> the new roles that actor may grant when adding a member
EXPECTED_ADD = {
    OWNER: {OWNER, ADMIN, MEMBER, VIEWER},
    ADMIN: {MEMBER, VIEWER},
    MEMBER: set(),
    VIEWER: set(),
}

# actor -> the target current roles that actor may remove
EXPECTED_REMOVE = {
    OWNER: {OWNER, ADMIN, MEMBER, VIEWER},
    ADMIN: {MEMBER, VIEWER},
    MEMBER: set(),
    VIEWER: set(),
}

# (actor, target current role, new role) triples that are allowed
EXPECTED_CHANGE = (
    {(OWNER, target, new) for target in ALL_ROLES for new in ALL_ROLES}
    | {(ADMIN, MEMBER, MEMBER), (ADMIN, MEMBER, VIEWER)}
    | {(ADMIN, VIEWER, MEMBER), (ADMIN, VIEWER, VIEWER)}
)


@pytest.mark.parametrize("actor", ALL_ROLES)
def test_can_manage_members_matches_the_expected_table(actor: OrganizationRole) -> None:
    assert can_manage_members(actor) is (actor in EXPECTED_MANAGERS)


@pytest.mark.parametrize("new_role", ALL_ROLES)
@pytest.mark.parametrize("actor", ALL_ROLES)
def test_can_add_member_matches_the_expected_table(
    actor: OrganizationRole, new_role: OrganizationRole
) -> None:
    assert can_add_member(actor, new_role) is (new_role in EXPECTED_ADD[actor])


@pytest.mark.parametrize("target", ALL_ROLES)
@pytest.mark.parametrize("actor", ALL_ROLES)
def test_can_remove_member_matches_the_expected_table(
    actor: OrganizationRole, target: OrganizationRole
) -> None:
    assert can_remove_member(actor, target) is (target in EXPECTED_REMOVE[actor])


@pytest.mark.parametrize("new_role", ALL_ROLES)
@pytest.mark.parametrize("target", ALL_ROLES)
@pytest.mark.parametrize("actor", ALL_ROLES)
def test_can_change_member_role_matches_the_expected_table(
    actor: OrganizationRole, target: OrganizationRole, new_role: OrganizationRole
) -> None:
    assert can_change_member_role(actor, target, new_role) is (
        (actor, target, new_role) in EXPECTED_CHANGE
    )


# --- the named security requirements, stated directly --------------------


def test_admin_cannot_grant_admin_or_owner() -> None:
    assert not can_add_member(ADMIN, ADMIN)
    assert not can_add_member(ADMIN, OWNER)
    assert not can_change_member_role(ADMIN, MEMBER, ADMIN)
    assert not can_change_member_role(ADMIN, VIEWER, OWNER)


def test_admin_cannot_modify_or_remove_admin_or_owner_including_itself() -> None:
    for target in (ADMIN, OWNER):
        assert not can_remove_member(ADMIN, target)
        for new_role in ALL_ROLES:
            assert not can_change_member_role(ADMIN, target, new_role)


@pytest.mark.parametrize("actor", [MEMBER, VIEWER])
def test_member_and_viewer_can_do_nothing_including_self_escalation(
    actor: OrganizationRole,
) -> None:
    assert not can_manage_members(actor)
    assert not can_change_member_role(actor, actor, OWNER)
    assert not can_change_member_role(actor, actor, ADMIN)
    assert not any(can_add_member(actor, role) for role in ALL_ROLES)
    assert not any(can_remove_member(actor, role) for role in ALL_ROLES)


def test_the_m1_scan_write_policy_is_unchanged() -> None:
    assert set(SCAN_WRITE_ROLES) == {OWNER, ADMIN, MEMBER}
    assert [can_write_scans(role) for role in (OWNER, ADMIN, MEMBER, VIEWER)] == [
        True,
        True,
        True,
        False,
    ]
