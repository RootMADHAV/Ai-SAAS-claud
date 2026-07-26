"""SQLAlchemy implementations of the Identity & Access repository ports.

Each repository takes an ``AsyncSession`` by constructor injection and
never commits it -- the caller (an application-layer use case, or a test)
owns the transaction boundary, per the unit-of-work pattern implied by
``session_scoped_to_org`` (app/infrastructure/db/session.py). A
repository that committed internally would make it impossible for a use
case to combine two repository calls into one atomic transaction.

Mapping between ORM rows and domain entities happens at the edge of each
method, in both directions -- nothing above this module ever sees a
SQLAlchemy model, keeping the dependency rule in PROJECT_STATE.md
section 1 intact.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.identity_repository import (
    AuditLogRepositoryPort,
    OrganizationRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.entities import AuditLogEntry, Organization, OrganizationMember, User
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.infrastructure.db.models.identity import (
    AuditLog as AuditLogRow,
)
from app.infrastructure.db.models.identity import (
    Organization as OrganizationRow,
)
from app.infrastructure.db.models.identity import (
    OrganizationMember as OrganizationMemberRow,
)
from app.infrastructure.db.models.identity import (
    User as UserRow,
)


def _organization_to_domain(row: OrganizationRow) -> Organization:
    return Organization(
        id=row.id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _user_to_domain(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        hashed_password=row.hashed_password,
        deleted_at=row.deleted_at,
    )


def _member_to_domain(row: OrganizationMemberRow) -> OrganizationMember:
    return OrganizationMember(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        role=OrganizationRole(row.role),
        status=MembershipStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit_entry_to_domain(row: AuditLogRow) -> AuditLogEntry:
    return AuditLogEntry(
        id=row.id,
        action=row.action,
        created_at=row.created_at,
        organization_id=row.organization_id,
        actor_user_id=row.actor_user_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        metadata=row.audit_metadata,
    )


class SqlAlchemyOrganizationRepository(OrganizationRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        # Explicit filter, not session.get(): RLS enforces tenant
        # isolation on this table but deliberately does not filter
        # deleted_at (see the DESIGN NOTE in the initial-schema migration
        # for why that combination is unimplementable in Postgres RLS).
        # A soft-deleted organization must still be invisible to this
        # normal lookup path, so the filter lives here instead.
        result = await self._session.execute(
            select(OrganizationRow).where(
                OrganizationRow.id == organization_id, OrganizationRow.deleted_at.is_(None)
            )
        )
        row = result.scalar_one_or_none()
        return _organization_to_domain(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self._session.execute(
            select(OrganizationRow).where(
                OrganizationRow.slug == slug, OrganizationRow.deleted_at.is_(None)
            )
        )
        row = result.scalar_one_or_none()
        return _organization_to_domain(row) if row is not None else None

    async def add(self, organization: Organization) -> None:
        self._session.add(
            OrganizationRow(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                created_at=organization.created_at,
                updated_at=organization.updated_at,
                deleted_at=organization.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, organization: Organization) -> None:
        row = await self._session.get(OrganizationRow, organization.id)
        if row is None:
            raise LookupError(f"Organization {organization.id} does not exist")
        row.name = organization.name
        row.slug = organization.slug
        row.deleted_at = organization.deleted_at
        await self._session.flush()

    async def soft_delete(self, organization_id: UUID) -> None:
        row = await self._session.get(OrganizationRow, organization_id)
        if row is None:
            raise LookupError(f"Organization {organization_id} does not exist")
        row.deleted_at = utcnow()
        await self._session.flush()

    async def add_member(self, member: OrganizationMember) -> None:
        self._session.add(
            OrganizationMemberRow(
                id=member.id,
                organization_id=member.organization_id,
                user_id=member.user_id,
                role=member.role,
                status=member.status,
                created_at=member.created_at,
                updated_at=member.updated_at,
            )
        )
        await self._session.flush()

    async def get_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember | None:
        result = await self._session.execute(
            select(OrganizationMemberRow).where(
                OrganizationMemberRow.organization_id == organization_id,
                OrganizationMemberRow.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return _member_to_domain(row) if row is not None else None

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        result = await self._session.execute(
            select(OrganizationMemberRow).where(
                OrganizationMemberRow.organization_id == organization_id
            )
        )
        return [_member_to_domain(row) for row in result.scalars().all()]

    async def update_member(self, member: OrganizationMember) -> None:
        row = await self._session.get(OrganizationMemberRow, member.id)
        if row is None:
            raise LookupError(f"OrganizationMember {member.id} does not exist")
        row.role = member.role
        row.status = member.status
        await self._session.flush()

    async def count_active_owners(self, organization_id: UUID) -> int:
        result = await self._session.execute(
            select(OrganizationMemberRow).where(
                OrganizationMemberRow.organization_id == organization_id,
                OrganizationMemberRow.role == OrganizationRole.OWNER,
                OrganizationMemberRow.status == MembershipStatus.ACTIVE,
            )
        )
        return len(result.scalars().all())


class SqlAlchemyUserRepository(UserRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        # Explicit filter, not session.get(): users are not RLS-scoped
        # (no organization_id column, per the model's docstring) so
        # deleted_at filtering has always been this repository's job here,
        # not the database's -- unlike the five RLS tables, this was never
        # an RLS concern to begin with.
        result = await self._session.execute(
            select(UserRow).where(UserRow.id == user_id, UserRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _user_to_domain(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserRow).where(UserRow.email == email, UserRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _user_to_domain(row) if row is not None else None

    async def add(self, user: User) -> None:
        self._session.add(
            UserRow(
                id=user.id,
                email=user.email,
                hashed_password=user.hashed_password,
                full_name=user.full_name,
                is_active=user.is_active,
                created_at=user.created_at,
                updated_at=user.updated_at,
                deleted_at=user.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, user: User) -> None:
        row = await self._session.get(UserRow, user.id)
        if row is None:
            raise LookupError(f"User {user.id} does not exist")
        row.email = user.email
        row.hashed_password = user.hashed_password
        row.full_name = user.full_name
        row.is_active = user.is_active
        row.deleted_at = user.deleted_at
        await self._session.flush()

    async def soft_delete(self, user_id: UUID) -> None:
        row = await self._session.get(UserRow, user_id)
        if row is None:
            raise LookupError(f"User {user_id} does not exist")
        row.deleted_at = utcnow()
        await self._session.flush()


class SqlAlchemyAuditLogRepository(AuditLogRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogEntry) -> None:
        self._session.add(
            AuditLogRow(
                id=entry.id,
                organization_id=entry.organization_id,
                actor_user_id=entry.actor_user_id,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                audit_metadata=entry.metadata,
                created_at=entry.created_at,
            )
        )
        await self._session.flush()

    async def list_for_organization(
        self, organization_id: UUID, limit: int = 100
    ) -> list[AuditLogEntry]:
        result = await self._session.execute(
            select(AuditLogRow)
            .where(AuditLogRow.organization_id == organization_id)
            .order_by(AuditLogRow.created_at.desc())
            .limit(limit)
        )
        return [_audit_entry_to_domain(row) for row in result.scalars().all()]
