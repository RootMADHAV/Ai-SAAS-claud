"""Integration tests for the member-management routes (Phase 6 M2).

Real HTTP (httpx ASGI transport) against real PostgreSQL as the
non-superuser ``app_user``, so Row Level Security is genuinely enforced:
every role x operation combination, the ADMIN restrictions, last-owner
safety, add semantics (unknown / inactive / duplicate / reactivation),
cross-organization isolation, per-organization roles, access loss after
removal, and the Postgres-level RLS backstop.

The deterministic lock/race tests live in
``test_membership_concurrency.py``; ``TestConcurrentOwnerChanges`` here is
the natural (unsynchronized) HTTP-level version of the same property.
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import AuthConfig, get_auth_config, get_session_factory
from app.domain.identity.entities import User
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.security.token_service import ACCESS_TOKEN_COOKIE_NAME, create_access_token
from app.main import create_app
from tests.integration.support import (
    make_member,
    make_organization,
    make_user,
    set_org_context,
)

pytestmark = pytest.mark.integration

_SECRET = "members-integration-test-secret-value"
OWNER, ADMIN, MEMBER, VIEWER = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.MEMBER,
    OrganizationRole.VIEWER,
)
ACTIVE, REMOVED = MembershipStatus.ACTIVE, MembershipStatus.REMOVED
ALL_ROLES = [OWNER, ADMIN, MEMBER, VIEWER]


class Actor(NamedTuple):
    user: User
    cookies: dict[str, str]


@pytest.fixture
def wired_app(engine: AsyncEngine) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(
        jwt_secret=_SECRET, access_token_expire_minutes=15, refresh_token_expire_days=30
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _cookies(user: User) -> dict[str, str]:
    token = create_access_token(user_id=user.id, secret=_SECRET, expire_minutes=15)
    return {ACCESS_TOKEN_COOKIE_NAME: token}


def _url(org_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/organizations/{org_id}/members{suffix}"


async def _org(db: AsyncSession) -> UUID:
    organization = make_organization()
    await set_org_context(db, organization.id)
    await SqlAlchemyOrganizationRepository(db).add(organization)
    await db.commit()
    return organization.id


async def _user(db: AsyncSession, **overrides: object) -> User:
    user = make_user(**overrides)
    await SqlAlchemyUserRepository(db).add(user)
    await db.commit()
    return user


async def _join(
    db: AsyncSession,
    org_id: UUID,
    user: User,
    role: OrganizationRole,
    status: MembershipStatus = ACTIVE,
) -> None:
    await set_org_context(db, org_id)
    await SqlAlchemyOrganizationRepository(db).add_member(
        make_member(org_id, user.id, role=role, status=status)
    )
    await db.commit()


async def _actor(
    db: AsyncSession, org_id: UUID, role: OrganizationRole, status: MembershipStatus = ACTIVE
) -> Actor:
    user = await _user(db)
    await _join(db, org_id, user, role, status)
    return Actor(user, _cookies(user))


async def _state(
    db: AsyncSession, org_id: UUID, user: User
) -> tuple[OrganizationRole, MembershipStatus] | None:
    await set_org_context(db, org_id)
    member = await SqlAlchemyOrganizationRepository(db).get_member(org_id, user.id)
    await db.rollback()
    return None if member is None else (member.role, member.status)


async def _row_count(db: AsyncSession, org_id: UUID, user: User) -> int:
    await set_org_context(db, org_id)
    result = await db.execute(
        text("SELECT count(*) FROM organization_members WHERE user_id = :u"), {"u": user.id}
    )
    count: int = result.scalar_one()
    await db.rollback()
    return count


async def _active_owners(db: AsyncSession, org_id: UUID) -> int:
    await set_org_context(db, org_id)
    count = await SqlAlchemyOrganizationRepository(db).count_active_owners(org_id)
    await db.rollback()
    return count


async def _do(
    client: httpx.AsyncClient,
    org_id: UUID,
    cookies: dict[str, str],
    kind: str,
    target: User,
    role: OrganizationRole | None = None,
) -> httpx.Response:
    """kind: 'add' (by the target's email), 'change' (role) or 'remove'."""
    if kind == "add":
        assert role is not None
        return await client.post(
            _url(org_id), json={"email": target.email, "role": role.value}, cookies=cookies
        )
    if kind == "change":
        assert role is not None
        return await client.post(
            _url(org_id, f"/{target.id}/role"), json={"role": role.value}, cookies=cookies
        )
    return await client.post(_url(org_id, f"/{target.id}/remove"), cookies=cookies)


# =================================================================== listing


class TestListMembers:
    @pytest.mark.parametrize("role", ALL_ROLES)
    async def test_every_active_role_may_list_members(
        self, wired_app: FastAPI, db_session: AsyncSession, role: OrganizationRole
    ) -> None:
        org_id = await _org(db_session)
        reader = await _actor(db_session, org_id, role)
        other = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await client.get(_url(org_id), cookies=reader.cookies)

        assert response.status_code == 200
        ids = {row["user_id"] for row in response.json()}
        assert ids == {str(reader.user.id), str(other.user.id)}

    async def test_the_response_is_an_explicit_allow_list_with_no_password_hash(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            response = await client.get(_url(org_id), cookies=owner.cookies)

        [row] = response.json()
        assert set(row) == {"user_id", "email", "full_name", "role", "status", "joined_at"}
        assert "hashed_password" not in response.text
        assert row["email"] == owner.user.email

    async def test_removed_members_and_other_organizations_members_are_not_listed(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        owner = await _actor(db_session, org_a, OWNER)
        await _actor(db_session, org_a, MEMBER, REMOVED)
        outsider = await _actor(db_session, org_b, OWNER)

        async with _client(wired_app) as client:
            response = await client.get(_url(org_a), cookies=owner.cookies)

        assert [row["user_id"] for row in response.json()] == [str(owner.user.id)]
        assert str(outsider.user.id) not in response.text

    async def test_unauthenticated_is_401(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)

        async with _client(wired_app) as client:
            response = await client.get(_url(org_id))

        assert response.status_code == 401

    async def test_non_member_and_removed_member_are_403(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        await _actor(db_session, org_id, OWNER)
        stranger = Actor(await _user(db_session), {})
        stranger = Actor(stranger.user, _cookies(stranger.user))
        removed = await _actor(db_session, org_id, ADMIN, REMOVED)

        async with _client(wired_app) as client:
            as_stranger = await client.get(_url(org_id), cookies=stranger.cookies)
            as_removed = await client.get(_url(org_id), cookies=removed.cookies)

        assert as_stranger.status_code == 403
        assert as_removed.status_code == 403

    async def test_unknown_organization_is_404(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)

        async with _client(wired_app) as client:
            response = await client.get(_url(uuid4()), cookies=_cookies(user))

        assert response.status_code == 404


# ====================================================== role x operation matrix


class TestRoleOperationMatrix:
    """Every actor role x every mutation, on a MEMBER-level target -- the
    one target class every manager may touch, so only the actor's role
    decides the outcome."""

    @pytest.mark.parametrize(
        ("actor_role", "expected"), [(OWNER, 201), (ADMIN, 201), (MEMBER, 403), (VIEWER, 403)]
    )
    async def test_add_member(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        actor_role: OrganizationRole,
        expected: int,
    ) -> None:
        org_id = await _org(db_session)
        actor = await _actor(db_session, org_id, actor_role)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, actor.cookies, "add", newcomer, VIEWER)

        assert response.status_code == expected
        assert await _state(db_session, org_id, newcomer) == (
            (VIEWER, ACTIVE) if expected == 201 else None
        )

    @pytest.mark.parametrize(
        ("actor_role", "expected"), [(OWNER, 200), (ADMIN, 200), (MEMBER, 403), (VIEWER, 403)]
    )
    async def test_change_role(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        actor_role: OrganizationRole,
        expected: int,
    ) -> None:
        org_id = await _org(db_session)
        actor = await _actor(db_session, org_id, actor_role)
        target = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, actor.cookies, "change", target.user, VIEWER)

        assert response.status_code == expected
        assert await _state(db_session, org_id, target.user) == (
            (VIEWER, ACTIVE) if expected == 200 else (MEMBER, ACTIVE)
        )

    @pytest.mark.parametrize(
        ("actor_role", "expected"), [(OWNER, 204), (ADMIN, 204), (MEMBER, 403), (VIEWER, 403)]
    )
    async def test_remove_member(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        actor_role: OrganizationRole,
        expected: int,
    ) -> None:
        org_id = await _org(db_session)
        actor = await _actor(db_session, org_id, actor_role)
        target = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, actor.cookies, "remove", target.user)

        assert response.status_code == expected
        assert await _state(db_session, org_id, target.user) == (
            (MEMBER, REMOVED) if expected == 204 else (MEMBER, ACTIVE)
        )


# ============================================================== ADMIN limits


class TestAdminLimits:
    @pytest.mark.parametrize(
        ("kind", "target_role", "role"),
        [
            ("add", None, ADMIN),  # ADMIN cannot create ADMIN
            ("add", None, OWNER),  # ADMIN cannot create OWNER
            ("change", MEMBER, ADMIN),  # ADMIN cannot promote to ADMIN
            ("change", VIEWER, OWNER),  # ADMIN cannot promote to OWNER
            ("change", ADMIN, MEMBER),  # ADMIN cannot modify ADMIN
            ("change", OWNER, MEMBER),  # ADMIN cannot modify OWNER
            ("remove", ADMIN, None),  # ADMIN cannot remove ADMIN
            ("remove", OWNER, None),  # ADMIN cannot remove OWNER
        ],
    )
    async def test_admin_is_refused_and_nothing_changes(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        kind: str,
        target_role: OrganizationRole | None,
        role: OrganizationRole | None,
    ) -> None:
        org_id = await _org(db_session)
        await _actor(db_session, org_id, OWNER)  # a second owner: only the role policy can refuse
        admin = await _actor(db_session, org_id, ADMIN)
        if target_role is None:
            target = Actor(await _user(db_session), {})
            before = None
        else:
            target = await _actor(db_session, org_id, target_role)
            before = (target_role, ACTIVE)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, admin.cookies, kind, target.user, role)

        assert response.status_code == 403
        assert await _state(db_session, org_id, target.user) == before

    async def test_admin_cannot_remove_or_demote_itself(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        await _actor(db_session, org_id, OWNER)
        admin = await _actor(db_session, org_id, ADMIN)

        async with _client(wired_app) as client:
            removed = await _do(client, org_id, admin.cookies, "remove", admin.user)
            demoted = await _do(client, org_id, admin.cookies, "change", admin.user, MEMBER)

        assert (removed.status_code, demoted.status_code) == (403, 403)
        assert await _state(db_session, org_id, admin.user) == (ADMIN, ACTIVE)

    @pytest.mark.parametrize("actor_role", [MEMBER, VIEWER])
    @pytest.mark.parametrize("new_role", [ADMIN, OWNER])
    async def test_member_and_viewer_cannot_escalate_themselves(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        actor_role: OrganizationRole,
        new_role: OrganizationRole,
    ) -> None:
        org_id = await _org(db_session)
        await _actor(db_session, org_id, OWNER)
        actor = await _actor(db_session, org_id, actor_role)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, actor.cookies, "change", actor.user, new_role)

        assert response.status_code == 403
        assert await _state(db_session, org_id, actor.user) == (actor_role, ACTIVE)

    async def test_admin_manages_member_and_viewer_freely(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        admin = await _actor(db_session, org_id, ADMIN)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            added = await _do(client, org_id, admin.cookies, "add", newcomer, MEMBER)
            changed = await _do(client, org_id, admin.cookies, "change", newcomer, VIEWER)
            removed = await _do(client, org_id, admin.cookies, "remove", newcomer)

        assert (added.status_code, changed.status_code, removed.status_code) == (201, 200, 204)
        assert await _state(db_session, org_id, newcomer) == (VIEWER, REMOVED)


# ============================================================== owner safety


class TestOwnerSafety:
    async def test_sole_owner_cannot_demote_themselves(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "change", owner.user, ADMIN)

        assert response.status_code == 409
        assert "owner" in response.json()["detail"]
        assert await _state(db_session, org_id, owner.user) == (OWNER, ACTIVE)

    async def test_sole_owner_cannot_remove_themselves(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "remove", owner.user)

        assert response.status_code == 409
        assert await _state(db_session, org_id, owner.user) == (OWNER, ACTIVE)
        assert await _active_owners(db_session, org_id) == 1

    async def test_owner_may_demote_or_remove_themselves_while_another_owner_remains(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        first = await _actor(db_session, org_id, OWNER)
        second = await _actor(db_session, org_id, OWNER)
        third = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            demoted = await _do(client, org_id, first.cookies, "change", first.user, MEMBER)
            removed = await _do(client, org_id, second.cookies, "remove", second.user)
            refused = await _do(client, org_id, third.cookies, "remove", third.user)

        assert (demoted.status_code, removed.status_code, refused.status_code) == (200, 204, 409)
        assert await _active_owners(db_session, org_id) == 1
        assert await _state(db_session, org_id, third.user) == (OWNER, ACTIVE)

    async def test_owner_demotes_another_owner_then_is_the_protected_last_owner(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        first = await _actor(db_session, org_id, OWNER)
        second = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            demoted = await _do(client, org_id, first.cookies, "change", second.user, VIEWER)
            refused = await _do(client, org_id, first.cookies, "change", first.user, VIEWER)

        assert (demoted.status_code, refused.status_code) == (200, 409)
        assert await _active_owners(db_session, org_id) == 1

    async def test_admin_cannot_be_used_to_demote_the_sole_owner(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        admin = await _actor(db_session, org_id, ADMIN)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, admin.cookies, "change", owner.user, VIEWER)

        assert response.status_code == 403
        assert await _state(db_session, org_id, owner.user) == (OWNER, ACTIVE)

    async def test_a_removed_owner_does_not_count_so_the_remaining_owner_is_protected(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        sole = await _actor(db_session, org_id, OWNER)
        await _actor(db_session, org_id, OWNER, REMOVED)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, sole.cookies, "remove", sole.user)

        assert response.status_code == 409


# =============================================================== add semantics


class TestAddSemantics:
    async def test_a_created_membership_is_returned_and_active(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        newcomer = await _user(db_session, full_name="New Person")

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "add", newcomer, ADMIN)

        assert response.status_code == 201
        body = response.json()
        assert (body["user_id"], body["email"], body["full_name"]) == (
            str(newcomer.id),
            newcomer.email,
            "New Person",
        )
        assert (body["role"], body["status"]) == ("admin", "active")

    async def test_unknown_email_is_404_and_creates_nothing(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            response = await client.post(
                _url(org_id),
                json={"email": "nobody@example.com", "role": "viewer"},
                cookies=owner.cookies,
            )

        assert response.status_code == 404
        assert "nobody@example.com" not in response.text  # the address is not echoed

    async def test_inactive_user_is_404(self, wired_app: FastAPI, db_session: AsyncSession) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        dormant = await _user(db_session, is_active=False)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "add", dormant, VIEWER)

        assert response.status_code == 404
        assert await _state(db_session, org_id, dormant) is None

    async def test_already_active_member_is_409_and_unchanged(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        existing = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "add", existing.user, ADMIN)

        assert response.status_code == 409
        assert await _state(db_session, org_id, existing.user) == (MEMBER, ACTIVE)

    async def test_removed_member_is_reactivated_on_the_same_row(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        returning = await _actor(db_session, org_id, OWNER, REMOVED)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "add", returning.user, MEMBER)

        assert response.status_code == 201
        assert await _row_count(db_session, org_id, returning.user) == 1  # no duplicate row
        assert await _state(db_session, org_id, returning.user) == (MEMBER, ACTIVE)

    async def test_remove_then_re_add_round_trip(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        person = await _actor(db_session, org_id, VIEWER)

        async with _client(wired_app) as client:
            removed = await _do(client, org_id, owner.cookies, "remove", person.user)
            again = await _do(client, org_id, owner.cookies, "remove", person.user)
            added = await _do(client, org_id, owner.cookies, "add", person.user, ADMIN)

        assert (removed.status_code, again.status_code, added.status_code) == (204, 404, 201)
        assert await _state(db_session, org_id, person.user) == (ADMIN, ACTIVE)
        assert await _row_count(db_session, org_id, person.user) == 1

    async def test_change_or_remove_of_an_unknown_user_is_404(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        ghost = make_user()

        async with _client(wired_app) as client:
            changed = await _do(client, org_id, owner.cookies, "change", ghost, VIEWER)
            removed = await _do(client, org_id, owner.cookies, "remove", ghost)

        assert (changed.status_code, removed.status_code) == (404, 404)

    @pytest.mark.parametrize(
        "body",
        [{"email": "not-an-email", "role": "viewer"}, {"email": "a@example.com", "role": "god"}],
    )
    async def test_invalid_add_payload_is_422(
        self, wired_app: FastAPI, db_session: AsyncSession, body: dict[str, str]
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            response = await client.post(_url(org_id), json=body, cookies=owner.cookies)

        assert response.status_code == 422

    async def test_invalid_role_or_user_id_on_the_other_routes_is_422(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            bad_role = await client.post(
                _url(org_id, f"/{owner.user.id}/role"), json={"role": "god"}, cookies=owner.cookies
            )
            bad_id = await client.post(_url(org_id, "/not-a-uuid/remove"), cookies=owner.cookies)

        assert (bad_role.status_code, bad_id.status_code) == (422, 422)


# ================================================= authentication / isolation


class TestAuthenticationAndIsolation:
    @pytest.mark.parametrize("suffix", ["", f"/{uuid4()}/role", f"/{uuid4()}/remove"])
    async def test_unauthenticated_mutations_are_401(
        self, wired_app: FastAPI, db_session: AsyncSession, suffix: str
    ) -> None:
        org_id = await _org(db_session)

        async with _client(wired_app) as client:
            response = await client.post(
                _url(org_id, suffix), json={"email": "a@example.com", "role": "viewer"}
            )

        assert response.status_code == 401

    async def test_an_owner_of_one_organization_cannot_act_in_another(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        owner_a = await _actor(db_session, org_a, OWNER)
        owner_b = await _actor(db_session, org_b, OWNER)
        victim_b = await _actor(db_session, org_b, MEMBER)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            listed = await client.get(_url(org_b), cookies=owner_a.cookies)
            added = await _do(client, org_b, owner_a.cookies, "add", newcomer, VIEWER)
            changed = await _do(client, org_b, owner_a.cookies, "change", victim_b.user, ADMIN)
            removed = await _do(client, org_b, owner_a.cookies, "remove", victim_b.user)

        assert [r.status_code for r in (listed, added, changed, removed)] == [403] * 4
        assert await _state(db_session, org_b, victim_b.user) == (MEMBER, ACTIVE)
        assert await _state(db_session, org_b, newcomer) is None
        assert await _state(db_session, org_b, owner_b.user) == (OWNER, ACTIVE)

    async def test_a_member_of_another_organization_is_not_found_by_user_id(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        """The actor is a legitimate OWNER of org A and names a user id
        that is only a member of org B -- by A's path it does not exist."""
        org_a, org_b = await _org(db_session), await _org(db_session)
        owner_a = await _actor(db_session, org_a, OWNER)
        only_in_b = await _actor(db_session, org_b, MEMBER)

        async with _client(wired_app) as client:
            changed = await _do(client, org_a, owner_a.cookies, "change", only_in_b.user, ADMIN)
            removed = await _do(client, org_a, owner_a.cookies, "remove", only_in_b.user)

        assert (changed.status_code, removed.status_code) == (404, 404)
        assert await _state(db_session, org_b, only_in_b.user) == (MEMBER, ACTIVE)

    async def test_one_user_has_independent_roles_in_different_organizations(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        user = await _user(db_session)
        await _join(db_session, org_a, user, VIEWER)
        await _join(db_session, org_b, user, OWNER)
        newcomer = await _user(db_session)
        cookies = _cookies(user)

        async with _client(wired_app) as client:
            in_a = await _do(client, org_a, cookies, "add", newcomer, VIEWER)
            in_b = await _do(client, org_b, cookies, "add", newcomer, VIEWER)

        assert (in_a.status_code, in_b.status_code) == (403, 201)
        assert await _state(db_session, org_a, newcomer) is None
        assert await _state(db_session, org_b, newcomer) == (VIEWER, ACTIVE)

    async def test_member_role_in_one_organization_does_not_leak_into_another_via_changes(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        owner_a = await _actor(db_session, org_a, OWNER)
        user = await _user(db_session)
        await _join(db_session, org_a, user, MEMBER)
        await _join(db_session, org_b, user, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_a, owner_a.cookies, "change", user, ADMIN)

        assert response.status_code == 200
        assert await _state(db_session, org_a, user) == (ADMIN, ACTIVE)
        assert await _state(db_session, org_b, user) == (MEMBER, ACTIVE)

    async def test_a_removed_member_immediately_loses_access_through_the_existing_gate(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        member = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            created = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies=owner.cookies,
            )
            scan_url = f"/api/v1/organizations/{org_id}/scans/{created.json()['id']}"
            before = await client.get(scan_url, cookies=member.cookies)
            listed_before = await client.get(_url(org_id), cookies=member.cookies)
            await _do(client, org_id, owner.cookies, "remove", member.user)
            after_scan = await client.get(scan_url, cookies=member.cookies)
            after_list = await client.get(_url(org_id), cookies=member.cookies)

        assert (before.status_code, listed_before.status_code) == (200, 200)
        assert (after_scan.status_code, after_list.status_code) == (403, 403)
        assert "not an active member" in after_scan.json()["detail"]

    async def test_the_m1_scan_permissions_are_unchanged_by_member_management(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        viewer = Actor(await _user(db_session), {})

        async with _client(wired_app) as client:
            await _do(client, org_id, owner.cookies, "add", viewer.user, VIEWER)
            create = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies=_cookies(viewer.user),
            )

        assert create.status_code == 403  # VIEWER stays read-only for scans (M1)


# ======================================================= PostgreSQL RLS backstop


class TestPostgresRlsBackstop:
    async def test_postgres_rejects_a_membership_row_for_another_organization(
        self, db_session: AsyncSession
    ) -> None:
        """Even if application code were wrong, the database itself refuses
        to write a membership into an organization other than the session's
        (WITH CHECK on the tenant_isolation policy)."""
        org_a, org_b = await _org(db_session), await _org(db_session)
        user = await _user(db_session)

        await set_org_context(db_session, org_a)
        with pytest.raises(DBAPIError, match="row-level security"):
            await SqlAlchemyOrganizationRepository(db_session).add_member(
                make_member(org_b, user.id, role=OWNER)
            )
        await db_session.rollback()

    async def test_postgres_hides_another_organizations_members_from_the_session(
        self, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        await _actor(db_session, org_a, OWNER)
        await _actor(db_session, org_b, OWNER)

        await set_org_context(db_session, org_a)
        result = await db_session.execute(
            text("SELECT count(*) FROM organization_members WHERE organization_id = :b"),
            {"b": org_b},
        )
        assert result.scalar_one() == 0
        listed = await SqlAlchemyOrganizationRepository(db_session).list_members(org_b)
        assert listed == []
        await db_session.rollback()

    async def test_the_tests_run_as_a_non_superuser_so_rls_is_really_enforced(
        self, db_session: AsyncSession
    ) -> None:
        result = await db_session.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        assert result.scalar_one() is False


# ===================================================== natural HTTP-level race


class TestConcurrentOwnerChanges:
    """Two owners racing. The invariant (>= 1 ACTIVE OWNER) must hold, and
    the loser is refused for the *right* reason:

    - "mutual" (A acts on B while B acts on A): the winner has removed or
      demoted the loser by the time the loser holds the lock, and the actor
      is re-read under the lock, so the loser is no longer authorized: 403.
    - "self" (each owner acts on themselves): the loser is still an
      authorized owner but would be the last one: 409 (``LastOwnerError``).
    """

    @pytest.mark.parametrize("kind", ["remove", "change"])
    @pytest.mark.parametrize(("scenario", "refused_status"), [("mutual", 403), ("self", 409)])
    async def test_two_owners_racing_never_leave_zero_owners(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        kind: str,
        scenario: str,
        refused_status: int,
    ) -> None:
        ok_status = 204 if kind == "remove" else 200
        async with _client(wired_app) as client:
            for _ in range(15):
                org_id = await _org(db_session)
                first = await _actor(db_session, org_id, OWNER)
                second = await _actor(db_session, org_id, OWNER)
                first_target = second.user if scenario == "mutual" else first.user
                second_target = first.user if scenario == "mutual" else second.user

                one, two = await asyncio.gather(
                    _do(client, org_id, first.cookies, kind, first_target, VIEWER),
                    _do(client, org_id, second.cookies, kind, second_target, VIEWER),
                )

                assert sorted([one.status_code, two.status_code]) == [ok_status, refused_status]
                assert await _active_owners(db_session, org_id) == 1
