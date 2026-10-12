"""Member management for an organization -- Phase 6 Milestone 2.

Four explicit use cases (list, add, change role, remove) and nothing
generic: no CRUD base class, no command bus. The authorization *rules* are
pure domain logic in ``app/domain/identity/access.py``; this module
applies them to real data and enforces the one rule that needs the
database, the last-owner invariant.

**Last-owner invariant (a security property).** An organization must never
be left with zero ACTIVE OWNER memberships. Every mutation therefore:

  1. calls ``lock_for_membership_change`` FIRST, in the same transaction
     as the mutation -- an exclusive ``SELECT ... FOR UPDATE`` on the
     organization row, so concurrent mutations on one organization are
     serialized;
  2. only then reads the actor, the target and the owner count -- so the
     count cannot go stale between "check" and "write" (two owners
     demoting each other at once would otherwise both read "2" and both
     commit);
  3. refuses (``LastOwnerError``) *before* writing anything when the
     change would remove the last ACTIVE OWNER, leaving state untouched.

The actor is deliberately NOT trusted from the request's dependency
chain: each mutation re-reads the actor's membership after acquiring the
lock, so a concurrent demotion/removal of the actor is respected rather
than racing it. (``require_member_manager`` in the API layer still gives
an early, cheap 403; this is the authoritative check.)

Authorization is checked BEFORE any lookup that could reveal whether an
email address is registered, so an actor who may not add the requested
role learns nothing about registered users. For an actor who may add it,
direct add-by-email necessarily reveals whether an address is registered
(404 vs 201/409) -- a documented, accepted trade-off of the direct-add
design (no invitation flow), not mitigated here (no rate limiting
exists in this codebase).

Removal never deletes a row: the membership is set to ``REMOVED``, which
``require_organization_member`` already treats as no access. Re-adding a
REMOVED user reactivates that same row (``UNIQUE(organization_id,
user_id)`` forbids a second one).

**Audit trail (Phase 6 M3).** Every SUCCESSFUL add, reactivation, role
change and removal writes exactly one ``AuditLogEntry`` (vocabulary in
``app/domain/identity/audit.py``) via a REQUIRED ``AuditLogRepositoryPort``
-- required on purpose, so a wiring mistake cannot silently disable
auditing. The entry is written AFTER the mutation, in the SAME
organization-scoped transaction and still under the row lock, so the two
commit or roll back together. It is fail-closed: an audit error is never
caught here, it propagates, and the whole membership change rolls back.
(A separate transaction is not merely undesirable but impossible inside
the locked section: the audit row's foreign key to the organization needs a
key-share lock on the row this transaction holds ``FOR UPDATE``.)
Entries carry IDs and roles only -- never an email or a name -- so the
trail survives user erasure and cannot become a second PII store.
Denied, failed, not-found, conflicting and no-op requests write nothing
(they raise before any mutation and the transaction is rolled back).

Out of scope here by design: invitations, leaving an organization,
ownership transfer, an audit read API/pagination, auditing of refused
attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.application.identity.errors import (
    LastOwnerError,
    MemberAlreadyActiveError,
    MemberManagementForbiddenError,
    MemberNotFoundError,
    UserNotFoundError,
)
from app.application.interfaces.identity_repository import (
    AuditLogRepositoryPort,
    OrganizationRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.access import (
    can_add_member,
    can_change_member_role,
    can_manage_members,
    can_remove_member,
)
from app.domain.identity.audit import MEMBER_AUDIT_RESOURCE_TYPE, MemberAuditAction
from app.domain.identity.entities import AuditLogEntry, OrganizationMember, User
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id


@dataclass(frozen=True, slots=True)
class MemberView:
    """A membership joined with its user -- what the API presents."""

    member: OrganizationMember
    user: User


async def _require_active_manager(
    organizations: OrganizationRepositoryPort, organization_id: UUID, actor_user_id: UUID
) -> OrganizationMember:
    """Re-read the actor's membership (fresh, under the lock) and require
    an ACTIVE membership whose role may manage members at all."""
    actor = await organizations.get_member(organization_id, actor_user_id)
    if actor is None or actor.status is not MembershipStatus.ACTIVE:
        raise MemberManagementForbiddenError("not an active member of this organization")
    if not can_manage_members(actor.role):
        raise MemberManagementForbiddenError(f"role {actor.role.value!r} may not manage members")
    return actor


async def _load_active_target(
    organizations: OrganizationRepositoryPort,
    users: UserRepositoryPort,
    organization_id: UUID,
    target_user_id: UUID,
) -> MemberView:
    """The target must be an ACTIVE member of THIS organization (a REMOVED
    or absent membership is "not found"; another organization's member is
    invisible here by RLS and by the organization_id filter)."""
    member = await organizations.get_member(organization_id, target_user_id)
    if member is None or member.status is not MembershipStatus.ACTIVE:
        raise MemberNotFoundError("no active member with that user id in this organization")
    user = await users.get_by_id(target_user_id)
    if user is None:
        raise MemberNotFoundError("no active member with that user id in this organization")
    return MemberView(member=member, user=user)


async def _ensure_not_last_owner(
    organizations: OrganizationRepositoryPort, organization_id: UUID, target: OrganizationMember
) -> None:
    """Call only for an operation that would stop ``target`` being an
    ACTIVE OWNER (demotion or removal). Valid only after
    ``lock_for_membership_change`` -- the count is race-free solely
    because the lock is held."""
    if target.role is not OrganizationRole.OWNER or target.status is not MembershipStatus.ACTIVE:
        return
    if await organizations.count_active_owners(organization_id) <= 1:
        raise LastOwnerError("an organization must keep at least one active owner")


async def _record_audit(
    audit: AuditLogRepositoryPort,
    *,
    action: MemberAuditAction,
    organization_id: UUID,
    actor_user_id: UUID,
    member_id: UUID,
    metadata: dict[str, object],
) -> None:
    """Write one membership audit entry in the caller's transaction.
    Deliberately NOT wrapped in try/except: an audit failure must abort
    (and roll back) the membership mutation it describes -- never swallow
    it, retry it elsewhere, or defer it. ``metadata`` must hold ids and
    role names only (all JSON-safe strings)."""
    await audit.add(
        AuditLogEntry(
            id=new_id(),
            action=action.value,
            created_at=utcnow(),
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            resource_type=MEMBER_AUDIT_RESOURCE_TYPE,
            resource_id=member_id,
            metadata=metadata,
        )
    )


class ListMembersUseCase:
    def __init__(
        self,
        organization_repository: OrganizationRepositoryPort,
        user_repository: UserRepositoryPort,
    ) -> None:
        self._organizations = organization_repository
        self._users = user_repository

    async def execute(self, *, organization_id: UUID) -> list[MemberView]:
        """ACTIVE members only, oldest first. Callers must already have
        verified the requester is an ACTIVE member (every role may read).
        A membership whose user no longer exists (soft-deleted) is
        omitted. One user lookup per member -- acceptable at current
        organization sizes, recorded as technical debt."""
        members = await self._organizations.list_members(organization_id)
        active = sorted(
            (m for m in members if m.status is MembershipStatus.ACTIVE),
            key=lambda m: (m.created_at, str(m.id)),
        )
        views: list[MemberView] = []
        for member in active:
            user = await self._users.get_by_id(member.user_id)
            if user is not None:
                views.append(MemberView(member=member, user=user))
        return views


class AddMemberUseCase:
    def __init__(
        self,
        organization_repository: OrganizationRepositoryPort,
        user_repository: UserRepositoryPort,
        audit_repository: AuditLogRepositoryPort,
    ) -> None:
        self._organizations = organization_repository
        self._users = user_repository
        self._audit = audit_repository

    async def execute(
        self,
        *,
        organization_id: UUID,
        actor_user_id: UUID,
        email: str,
        role: OrganizationRole,
    ) -> MemberView:
        """Add an existing, active, registered user directly as ACTIVE (or
        reactivate their REMOVED membership). Raises
        ``MemberManagementForbiddenError`` (actor may not grant ``role``),
        ``UserNotFoundError`` (no ACTIVE registered user with that email),
        ``MemberAlreadyActiveError``. On success writes ``member.added`` (new
        membership) or ``member.reactivated`` (REMOVED membership brought back)
        in this same transaction; on any failure nothing is written."""
        await self._organizations.lock_for_membership_change(organization_id)
        actor = await _require_active_manager(self._organizations, organization_id, actor_user_id)
        if not can_add_member(actor.role, role):
            raise MemberManagementForbiddenError(
                f"role {actor.role.value!r} may not grant role {role.value!r}"
            )

        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            raise UserNotFoundError("no active user is registered with that email address")

        now = utcnow()
        existing = await self._organizations.get_member(organization_id, user.id)
        if existing is None:
            member = OrganizationMember(
                id=new_id(),
                organization_id=organization_id,
                user_id=user.id,
                role=role,
                status=MembershipStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            await self._organizations.add_member(member)
            action = MemberAuditAction.ADDED
        elif existing.status is MembershipStatus.ACTIVE:
            raise MemberAlreadyActiveError("user is already an active member of this organization")
        else:
            existing.role = role
            existing.status = MembershipStatus.ACTIVE
            existing.updated_at = now
            await self._organizations.update_member(existing)
            member = existing
            action = MemberAuditAction.REACTIVATED
        await _record_audit(
            self._audit,
            action=action,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            member_id=member.id,
            metadata={"target_user_id": str(user.id), "role": role.value},
        )
        return MemberView(member=member, user=user)


class ChangeMemberRoleUseCase:
    def __init__(
        self,
        organization_repository: OrganizationRepositoryPort,
        user_repository: UserRepositoryPort,
        audit_repository: AuditLogRepositoryPort,
    ) -> None:
        self._organizations = organization_repository
        self._users = user_repository
        self._audit = audit_repository

    async def execute(
        self,
        *,
        organization_id: UUID,
        actor_user_id: UUID,
        target_user_id: UUID,
        new_role: OrganizationRole,
    ) -> MemberView:
        """Raises ``MemberManagementForbiddenError``, ``MemberNotFoundError``,
        or ``LastOwnerError`` (the change would demote the last ACTIVE
        OWNER) -- in every failure case nothing has been written. A real role
        change writes ``member.role_changed`` in this same transaction; a
        change to the member's current role is a no-op and writes nothing."""
        await self._organizations.lock_for_membership_change(organization_id)
        actor = await _require_active_manager(self._organizations, organization_id, actor_user_id)
        view = await _load_active_target(
            self._organizations, self._users, organization_id, target_user_id
        )
        target = view.member
        if not can_change_member_role(actor.role, target.role, new_role):
            raise MemberManagementForbiddenError(
                f"role {actor.role.value!r} may not change a {target.role.value!r} "
                f"member to {new_role.value!r}"
            )
        if new_role is target.role:
            return view
        if new_role is not OrganizationRole.OWNER:
            await _ensure_not_last_owner(self._organizations, organization_id, target)

        previous_role = target.role
        target.role = new_role
        target.updated_at = utcnow()
        await self._organizations.update_member(target)
        await _record_audit(
            self._audit,
            action=MemberAuditAction.ROLE_CHANGED,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            member_id=target.id,
            metadata={
                "target_user_id": str(target.user_id),
                "previous_role": previous_role.value,
                "new_role": new_role.value,
            },
        )
        return view


class RemoveMemberUseCase:
    def __init__(
        self,
        organization_repository: OrganizationRepositoryPort,
        audit_repository: AuditLogRepositoryPort,
    ) -> None:
        self._organizations = organization_repository
        self._audit = audit_repository

    async def execute(
        self, *, organization_id: UUID, actor_user_id: UUID, target_user_id: UUID
    ) -> None:
        """Set the target's membership to REMOVED (the row is kept).
        Raises ``MemberManagementForbiddenError``, ``MemberNotFoundError``,
        or ``LastOwnerError`` -- in every failure case nothing has been
        written. On success writes ``member.removed`` in this same
        transaction."""
        await self._organizations.lock_for_membership_change(organization_id)
        actor = await _require_active_manager(self._organizations, organization_id, actor_user_id)
        target = await self._organizations.get_member(organization_id, target_user_id)
        if target is None or target.status is not MembershipStatus.ACTIVE:
            raise MemberNotFoundError("no active member with that user id in this organization")
        if not can_remove_member(actor.role, target.role):
            raise MemberManagementForbiddenError(
                f"role {actor.role.value!r} may not remove a {target.role.value!r} member"
            )
        await _ensure_not_last_owner(self._organizations, organization_id, target)

        previous_role = target.role
        target.status = MembershipStatus.REMOVED
        target.updated_at = utcnow()
        await self._organizations.update_member(target)
        await _record_audit(
            self._audit,
            action=MemberAuditAction.REMOVED,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            member_id=target.id,
            metadata={"target_user_id": str(target.user_id), "previous_role": previous_role.value},
        )
