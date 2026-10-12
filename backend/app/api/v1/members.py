"""Member-management routes -- Phase 6 Milestone 2.

All routes run inside ``get_org_session``'s single organization-scoped
(RLS) transaction. Mutations are POST-only by design (the CORS
configuration allows only GET/POST and is deliberately not widened here):

    GET  /organizations/{organization_id}/members
    POST /organizations/{organization_id}/members                       add
    POST /organizations/{organization_id}/members/{user_id}/role        change role
    POST /organizations/{organization_id}/members/{user_id}/remove      remove

Authorization: listing needs only ``require_organization_member`` (every
ACTIVE role may read the roster). The mutations declare
``require_member_manager`` FIRST so an unauthenticated caller gets 401
before the organization-exists 404; it is only an early gate -- the use
cases re-check authority authoritatively under the row lock, including the
target-dependent ADMIN restrictions. Error mapping: 403 insufficient
authority, 404 unknown organization/member/user, 409 already a member or
last-owner violation (see ``app/main.py`` handlers).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import (
    get_add_member_use_case,
    get_change_member_role_use_case,
    get_list_members_use_case,
    get_remove_member_use_case,
    require_member_manager,
    require_organization_member,
)
from app.api.v1.member_schemas import MemberAddRequest, MemberResponse, MemberRoleChangeRequest
from app.application.identity.manage_members import (
    AddMemberUseCase,
    ChangeMemberRoleUseCase,
    ListMembersUseCase,
    RemoveMemberUseCase,
)
from app.domain.identity.entities import OrganizationMember

router = APIRouter(prefix="/organizations/{organization_id}/members", tags=["members"])


@router.get("", response_model=list[MemberResponse])
async def list_members(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_organization_member),
    use_case: ListMembersUseCase = Depends(get_list_members_use_case),
) -> list[MemberResponse]:
    views = await use_case.execute(organization_id=organization_id)
    return [MemberResponse.from_view(view) for view in views]


@router.post("", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    organization_id: UUID,
    payload: MemberAddRequest,
    actor: OrganizationMember = Depends(require_member_manager),
    use_case: AddMemberUseCase = Depends(get_add_member_use_case),
) -> MemberResponse:
    view = await use_case.execute(
        organization_id=organization_id,
        actor_user_id=actor.user_id,
        email=payload.email,
        role=payload.role,
    )
    return MemberResponse.from_view(view)


@router.post("/{user_id}/role", response_model=MemberResponse)
async def change_member_role(
    organization_id: UUID,
    user_id: UUID,
    payload: MemberRoleChangeRequest,
    actor: OrganizationMember = Depends(require_member_manager),
    use_case: ChangeMemberRoleUseCase = Depends(get_change_member_role_use_case),
) -> MemberResponse:
    view = await use_case.execute(
        organization_id=organization_id,
        actor_user_id=actor.user_id,
        target_user_id=user_id,
        new_role=payload.role,
    )
    return MemberResponse.from_view(view)


@router.post("/{user_id}/remove", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_member(
    organization_id: UUID,
    user_id: UUID,
    actor: OrganizationMember = Depends(require_member_manager),
    use_case: RemoveMemberUseCase = Depends(get_remove_member_use_case),
) -> Response:
    await use_case.execute(
        organization_id=organization_id, actor_user_id=actor.user_id, target_user_id=user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
