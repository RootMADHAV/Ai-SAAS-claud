"""Request/response schemas for the member-management routes (Phase 6 M2).

``MemberResponse`` is an explicit allow-list of fields. It must never be
built from the ``User`` entity wholesale: that entity carries
``hashed_password``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.application.identity.manage_members import MemberView
from app.domain.shared.enums import MembershipStatus, OrganizationRole


class MemberAddRequest(BaseModel):
    email: EmailStr
    role: OrganizationRole


class MemberRoleChangeRequest(BaseModel):
    role: OrganizationRole


class MemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str
    full_name: str
    role: OrganizationRole
    status: MembershipStatus
    joined_at: datetime

    @classmethod
    def from_view(cls, view: MemberView) -> MemberResponse:
        return cls(
            user_id=view.user.id,
            email=view.user.email,
            full_name=view.user.full_name,
            role=view.member.role,
            status=view.member.status,
            joined_at=view.member.created_at,
        )
