"""Repository ports for the Identity & Access bounded context.

Named ``*RepositoryPort`` to match the existing port-naming convention in
PROJECT_STATE.md section 1 (``ScannerPort``, ``AIProviderPort``,
``EventBusPort``, ``StoragePort``). An ABC, not a ``Protocol`` --
consistent with "port" being used elsewhere in this project as an
explicit interface a concrete adapter subclasses, not a structural type
checked implicitly.

Every method is async: the only realistic implementation
(``app/infrastructure/db/repositories/identity_repository.py``) talks to
PostgreSQL over an async engine, and a sync port would force every
caller, all the way up through use cases, to block on I/O.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.identity.entities import (
    AuditLogEntry,
    Organization,
    OrganizationMember,
    RefreshToken,
    User,
)


class OrganizationRepositoryPort(ABC):
    """Persistence for the ``Organization`` aggregate, including its
    ``OrganizationMember`` rows. Membership lives on this port rather than
    a separate one because a membership has no meaning independent of the
    organization it belongs to -- the same reasoning that keeps
    ``FindingOccurrence`` under ``FindingRepositoryPort`` rather than its
    own port.
    """

    @abstractmethod
    async def get_by_id(self, organization_id: UUID) -> Organization | None: ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Organization | None: ...

    @abstractmethod
    async def add(self, organization: Organization) -> None: ...

    @abstractmethod
    async def update(self, organization: Organization) -> None: ...

    @abstractmethod
    async def soft_delete(self, organization_id: UUID) -> None: ...

    @abstractmethod
    async def add_member(self, member: OrganizationMember) -> None: ...

    @abstractmethod
    async def get_member(
        self, organization_id: UUID, user_id: UUID
    ) -> OrganizationMember | None: ...

    @abstractmethod
    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]: ...

    @abstractmethod
    async def update_member(self, member: OrganizationMember) -> None: ...

    @abstractmethod
    async def count_active_owners(self, organization_id: UUID) -> int:
        """Supports the ">= 1 Owner always" invariant (PROJECT_STATE.md
        section 5) without enforcing it here -- the use case that removes
        or demotes a member calls this first and decides what to do with
        the result. The invariant lives in application-layer logic, not
        in this port."""
        ...


class UserRepositoryPort(ABC):
    """Persistence for the ``User`` entity. Not org-scoped -- see
    ``app/domain/identity/entities.py``."""

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def add(self, user: User) -> None: ...

    @abstractmethod
    async def update(self, user: User) -> None: ...

    @abstractmethod
    async def soft_delete(self, user_id: UUID) -> None: ...


class AuditLogRepositoryPort(ABC):
    """Persistence for ``AuditLogEntry``. Append-only, per
    PROJECT_STATE.md section 3 -- no ``update``/``delete`` method exists
    on this port because there is no legitimate use case for either; a
    port should not offer an operation its own domain rules forbid.
    """

    @abstractmethod
    async def add(self, entry: AuditLogEntry) -> None: ...

    @abstractmethod
    async def list_for_organization(
        self, organization_id: UUID, limit: int = 100
    ) -> list[AuditLogEntry]: ...


class RefreshTokenRepositoryPort(ABC):
    """Persistence for ``RefreshToken`` rows. Not
    org-scoped -- like ``UserRepositoryPort``, ``refresh_tokens`` carries
    no ``organization_id`` column and has no RLS policy (see the initial
    migration's module docstring on tables with no ``organization_id`` at
    all)."""

    @abstractmethod
    async def add(self, token: RefreshToken) -> None: ...

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        """Look up by the natural key a client's raw refresh token hashes
        to -- the only way this repository is ever queried, since a raw
        refresh token is never itself persisted (see ``RefreshToken``'s
        own docstring)."""
        ...

    @abstractmethod
    async def revoke(self, token_id: UUID) -> None:
        """Sets ``revoked_at``. Does not delete the row -- a revoked
        token that is later replayed should still be recognizable as
        "this token existed and was revoked," not indistinguishable from
        one that never existed at all."""
        ...
