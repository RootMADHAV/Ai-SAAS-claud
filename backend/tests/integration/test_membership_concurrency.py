"""Deterministic concurrency tests for the last-owner invariant
(Phase 6 M2).

``test_api_members.py::TestConcurrentOwnerChanges`` races real HTTP
requests, but an unsynchronized race can pass by luck even without a lock.
These tests remove the luck. ``_RendezvousRepository`` makes every
transaction pause right after it has counted active owners until all
racing transactions have counted (or a short timeout expires):

  - WITH ``lock_for_membership_change`` the second transaction cannot even
    reach the count -- it is blocked on the organization row lock -- so the
    first one's rendezvous simply times out, it commits, and the second
    then counts 1 and is refused. The invariant holds.
  - WITHOUT the lock both transactions reach the rendezvous together, both
    read "2 owners", both write, both commit: zero owners. These tests are
    written so that exact failure is what they detect.

Everything runs through the real use cases, repositories, Postgres
transactions and row locks as the non-superuser ``app_user`` (RLS on).
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.identity.errors import (
    LastOwnerError,
    MemberManagementForbiddenError,
)
from app.application.identity.manage_members import (
    ChangeMemberRoleUseCase,
    RemoveMemberUseCase,
)
from app.domain.identity.audit import MemberAuditAction
from app.domain.identity.entities import AuditLogEntry, User
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.session import session_scoped_to_org
from tests.integration.support import make_member, make_organization, make_user, set_org_context

pytestmark = pytest.mark.integration

OWNER, VIEWER = OrganizationRole.OWNER, OrganizationRole.VIEWER
_RACE_ITERATIONS = 20


class _Rendezvous:
    """Hold each party after it has counted owners until every party has
    counted -- or ``timeout`` elapses (a party blocked on the row lock can
    never arrive, so the others must not wait forever)."""

    def __init__(self, parties: int = 2, timeout: float = 0.15) -> None:
        self._parties = parties
        self._timeout = timeout
        self._arrived = 0
        self._all_arrived = asyncio.Event()

    async def arrive(self) -> None:
        self._arrived += 1
        if self._arrived >= self._parties:
            self._all_arrived.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._all_arrived.wait(), self._timeout)


class _RendezvousRepository(SqlAlchemyOrganizationRepository):
    def __init__(self, session: AsyncSession, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def count_active_owners(self, organization_id: UUID) -> int:
        count = await super().count_active_owners(organization_id)
        await self._rendezvous.arrive()
        return count


async def _org_with_owners(db: AsyncSession, owners: int = 2) -> tuple[UUID, list[User]]:
    organization = make_organization()
    await set_org_context(db, organization.id)
    await SqlAlchemyOrganizationRepository(db).add(organization)
    users: list[User] = []
    for _ in range(owners):
        user = make_user()
        await SqlAlchemyUserRepository(db).add(user)
        await SqlAlchemyOrganizationRepository(db).add_member(
            make_member(organization.id, user.id, role=OWNER)
        )
        users.append(user)
    await db.commit()
    return organization.id, users


async def _active_owners(db: AsyncSession, org_id: UUID) -> int:
    await set_org_context(db, org_id)
    count = await SqlAlchemyOrganizationRepository(db).count_active_owners(org_id)
    await db.rollback()
    return count


async def _audit_entries(db: AsyncSession, org_id: UUID) -> list[AuditLogEntry]:
    await set_org_context(db, org_id)
    entries = await SqlAlchemyAuditLogRepository(db).list_for_organization(org_id)
    await db.rollback()
    return entries


async def _attempt(
    engine: AsyncEngine,
    rendezvous: _Rendezvous,
    kind: str,
    org_id: UUID,
    actor: User,
    target: User,
) -> str:
    """One mutation in its OWN transaction (own connection), exactly as a
    request would run it: ``session_scoped_to_org`` begins, sets the RLS
    context, commits on success and rolls back on any exception."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_scoped_to_org(factory, org_id) as session:
            organizations = _RendezvousRepository(session, rendezvous)
            if kind == "remove":
                await RemoveMemberUseCase(
                    organizations, SqlAlchemyAuditLogRepository(session)
                ).execute(organization_id=org_id, actor_user_id=actor.id, target_user_id=target.id)
            else:
                await ChangeMemberRoleUseCase(
                    organizations,
                    SqlAlchemyUserRepository(session),
                    SqlAlchemyAuditLogRepository(session),
                ).execute(
                    organization_id=org_id,
                    actor_user_id=actor.id,
                    target_user_id=target.id,
                    new_role=VIEWER,
                )
        return "done"
    except LastOwnerError:
        return "last_owner"
    except MemberManagementForbiddenError:
        return "forbidden"


class TestLockPrimitive:
    async def test_a_second_transaction_blocks_until_the_first_commits(
        self, engine: AsyncEngine, db_session: AsyncSession
    ) -> None:
        org_id, _ = await _org_with_owners(db_session, owners=1)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        second_acquired = asyncio.Event()

        async def second_transaction() -> None:
            async with session_scoped_to_org(factory, org_id) as session:
                await SqlAlchemyOrganizationRepository(session).lock_for_membership_change(org_id)
                second_acquired.set()

        async with session_scoped_to_org(factory, org_id) as first:
            await SqlAlchemyOrganizationRepository(first).lock_for_membership_change(org_id)
            waiter = asyncio.create_task(second_transaction())
            await asyncio.sleep(0.3)
            assert not second_acquired.is_set()  # blocked on the row lock
            assert not waiter.done()
        # first transaction committed -> lock released
        await asyncio.wait_for(waiter, timeout=5)
        assert second_acquired.is_set()

    async def test_the_lock_is_released_when_the_transaction_rolls_back(
        self, engine: AsyncEngine, db_session: AsyncSession
    ) -> None:
        org_id, _ = await _org_with_owners(db_session, owners=1)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        with pytest.raises(RuntimeError, match="boom"):
            async with session_scoped_to_org(factory, org_id) as first:
                await SqlAlchemyOrganizationRepository(first).lock_for_membership_change(org_id)
                raise RuntimeError("boom")

        async with session_scoped_to_org(factory, org_id) as second:
            await asyncio.wait_for(
                SqlAlchemyOrganizationRepository(second).lock_for_membership_change(org_id),
                timeout=5,
            )

    async def test_locking_an_organization_that_is_not_visible_raises_lookup_error(
        self, engine: AsyncEngine, db_session: AsyncSession
    ) -> None:
        """Another tenant's organization row is invisible under RLS, so it
        cannot be locked (or even detected) from this session."""
        org_a, _ = await _org_with_owners(db_session, owners=1)
        org_b, _ = await _org_with_owners(db_session, owners=1)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async with session_scoped_to_org(factory, org_a) as session:
            repository = SqlAlchemyOrganizationRepository(session)
            with pytest.raises(LookupError):
                await repository.lock_for_membership_change(org_b)
            with pytest.raises(LookupError):
                await repository.lock_for_membership_change(UUID(int=0))


class TestRaceIsDeterministic:
    @pytest.mark.parametrize("kind", ["remove", "change"])
    async def test_mutual_race_exactly_one_wins_and_one_owner_remains(
        self, engine: AsyncEngine, db_session: AsyncSession, kind: str
    ) -> None:
        for _ in range(_RACE_ITERATIONS):
            org_id, (first, second) = await _org_with_owners(db_session)
            rendezvous = _Rendezvous()

            results = await asyncio.gather(
                _attempt(engine, rendezvous, kind, org_id, first, second),
                _attempt(engine, rendezvous, kind, org_id, second, first),
            )

            # The loser, once it gets the lock, finds itself no longer an
            # authorized owner (re-read under the lock).
            assert sorted(results) == ["done", "forbidden"]
            assert await _active_owners(db_session, org_id) == 1

            # M3: exactly the winner's mutation is audited -- the loser
            # (refused before any write) leaves no false-success record.
            winner_index = results.index("done")
            winner, loser = (first, second) if winner_index == 0 else (second, first)
            [entry] = await _audit_entries(db_session, org_id)
            expected = (
                MemberAuditAction.REMOVED if kind == "remove" else MemberAuditAction.ROLE_CHANGED
            )
            assert entry.action == expected.value
            assert entry.actor_user_id == winner.id
            assert entry.metadata is not None
            assert entry.metadata["target_user_id"] == str(loser.id)

    @pytest.mark.parametrize("kind", ["remove", "change"])
    async def test_self_race_exactly_one_wins_and_the_other_hits_the_last_owner_guard(
        self, engine: AsyncEngine, db_session: AsyncSession, kind: str
    ) -> None:
        for _ in range(_RACE_ITERATIONS):
            org_id, (first, second) = await _org_with_owners(db_session)
            rendezvous = _Rendezvous()

            results = await asyncio.gather(
                _attempt(engine, rendezvous, kind, org_id, first, first),
                _attempt(engine, rendezvous, kind, org_id, second, second),
            )

            assert sorted(results) == ["done", "last_owner"]
            assert await _active_owners(db_session, org_id) == 1

            winner = (first, second)[results.index("done")]
            [entry] = await _audit_entries(db_session, org_id)
            assert entry.actor_user_id == winner.id
            assert entry.metadata is not None
            assert entry.metadata["target_user_id"] == str(winner.id)  # acted on itself

    async def test_a_refused_racer_changes_nothing(
        self, engine: AsyncEngine, db_session: AsyncSession
    ) -> None:
        org_id, (first, second) = await _org_with_owners(db_session)
        rendezvous = _Rendezvous()

        results = await asyncio.gather(
            _attempt(engine, rendezvous, "remove", org_id, first, first),
            _attempt(engine, rendezvous, "remove", org_id, second, second),
        )

        survivor = first if results[0] == "last_owner" else second
        await set_org_context(db_session, org_id)
        member = await SqlAlchemyOrganizationRepository(db_session).get_member(org_id, survivor.id)
        await db_session.rollback()
        assert member is not None
        assert (member.role, member.status) == (OWNER, MembershipStatus.ACTIVE)
        assert len(await _audit_entries(db_session, org_id)) == 1  # the loser wrote nothing
