"""Unit tests for ``require_member_manager`` (Phase 6 M2). Like
``require_scan_write_access`` it only inspects the already-resolved
``OrganizationMember``, so it is tested directly with a plain domain
object -- no database."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.dependencies import require_member_manager
from app.domain.identity.entities import OrganizationMember
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id


def _member(role: OrganizationRole) -> OrganizationMember:
    now = utcnow()
    return OrganizationMember(
        id=new_id(),
        organization_id=new_id(),
        user_id=new_id(),
        role=role,
        status=MembershipStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("role", [OrganizationRole.OWNER, OrganizationRole.ADMIN])
async def test_require_member_manager_returns_the_member_for_owner_and_admin(
    role: OrganizationRole,
) -> None:
    member = _member(role)

    assert await require_member_manager(member=member) is member


@pytest.mark.parametrize("role", [OrganizationRole.MEMBER, OrganizationRole.VIEWER])
async def test_require_member_manager_raises_403_for_member_and_viewer(
    role: OrganizationRole,
) -> None:
    member = _member(role)

    with pytest.raises(HTTPException) as exc_info:
        await require_member_manager(member=member)

    assert exc_info.value.status_code == 403
    detail = str(exc_info.value.detail)
    assert role.value in detail
    assert str(member.organization_id) in detail
