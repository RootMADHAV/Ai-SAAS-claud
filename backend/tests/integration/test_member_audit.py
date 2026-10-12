"""Integration tests for the membership audit trail (Phase 6 M3).

Real HTTP against real PostgreSQL as the non-superuser ``app_user`` (RLS
genuinely enforced). The important group is ``TestAtomicity``: the
membership mutation and its audit row must commit or roll back TOGETHER,
and an audit failure must fail the request, never be swallowed. Two
different failure injections are used on purpose:

  * the audit INSERT itself fails (``SqlAlchemyAuditLogRepository.add``
    raises) -- catches an audit error being swallowed (fail-open);
  * a failure AFTER the use case has succeeded (mutation AND audit row
    written, commit not yet reached) -- catches an audit row that was
    committed independently of the membership transaction.
"""

from __future__ import annotations

import asyncio
from typing import Any, NamedTuple
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import (
    AuthConfig,
    get_add_member_use_case,
    get_auth_config,
    get_change_member_role_use_case,
    get_org_audit_log_repository,
    get_org_organization_repository,
    get_org_user_repository,
    get_remove_member_use_case,
    get_session_factory,
)
from app.application.identity.manage_members import (
    AddMemberUseCase,
    ChangeMemberRoleUseCase,
    RemoveMemberUseCase,
)
from app.application.interfaces.identity_repository import (
    AuditLogRepositoryPort,
    OrganizationRepositoryPort,
    UserRepositoryPort,
)
from app.domain.identity.entities import AuditLogEntry, User
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import MembershipStatus, OrganizationRole
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.security.token_service import ACCESS_TOKEN_COOKIE_NAME, create_access_token
from app.main import create_app
from tests.integration.support import make_member, make_organization, make_user, set_org_context

pytestmark = pytest.mark.integration

_SECRET = "member-audit-integration-test-secret"
OWNER, ADMIN, MEMBER, VIEWER = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.MEMBER,
    OrganizationRole.VIEWER,
)
ACTIVE, REMOVED = MembershipStatus.ACTIVE, MembershipStatus.REMOVED


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


def _client(app: FastAPI, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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
    db: AsyncSession,
    org_id: UUID,
    role: OrganizationRole,
    status: MembershipStatus = ACTIVE,
    **user_overrides: object,
) -> Actor:
    user = await _user(db, **user_overrides)
    await _join(db, org_id, user, role, status)
    return Actor(user, _cookies(user))


async def _state(
    db: AsyncSession, org_id: UUID, user: User
) -> tuple[OrganizationRole, MembershipStatus] | None:
    await set_org_context(db, org_id)
    member = await SqlAlchemyOrganizationRepository(db).get_member(org_id, user.id)
    await db.rollback()
    return None if member is None else (member.role, member.status)


async def _row_id(db: AsyncSession, org_id: UUID, user: User) -> UUID:
    await set_org_context(db, org_id)
    member = await SqlAlchemyOrganizationRepository(db).get_member(org_id, user.id)
    await db.rollback()
    assert member is not None
    return member.id


async def _audit(db: AsyncSession, org_id: UUID) -> list[AuditLogEntry]:
    """Audit entries of ``org_id``, newest first, read through the same
    RLS-scoped path production code uses."""
    await set_org_context(db, org_id)
    entries = await SqlAlchemyAuditLogRepository(db).list_for_organization(org_id)
    await db.rollback()
    return entries


async def _do(
    client: httpx.AsyncClient,
    org_id: UUID,
    cookies: dict[str, str],
    kind: str,
    target: User,
    role: OrganizationRole | None = None,
) -> httpx.Response:
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


# ================================================== successful mutations


class TestSuccessfulMutationsAreAudited:
    async def test_add_creates_exactly_one_member_added_row_with_the_right_fields(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "add", newcomer, VIEWER)

        assert response.status_code == 201
        [entry] = await _audit(db_session, org_id)
        assert entry.action == "member.added"
        assert entry.organization_id == org_id
        assert entry.actor_user_id == owner.user.id
        assert entry.resource_type == "organization_member"
        assert entry.resource_id == await _row_id(db_session, org_id, newcomer)
        assert entry.metadata == {"target_user_id": str(newcomer.id), "role": "viewer"}
        assert entry.created_at is not None

    async def test_reactivation_creates_a_distinct_member_reactivated_row_on_the_same_membership(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        person = await _actor(db_session, org_id, MEMBER)
        row_id = await _row_id(db_session, org_id, person.user)

        async with _client(wired_app) as client:
            removed = await _do(client, org_id, owner.cookies, "remove", person.user)
            readded = await _do(client, org_id, owner.cookies, "add", person.user, ADMIN)

        assert (removed.status_code, readded.status_code) == (204, 201)
        reactivated, removal = await _audit(db_session, org_id)  # newest first
        assert (reactivated.action, removal.action) == ("member.reactivated", "member.removed")
        assert reactivated.resource_id == removal.resource_id == row_id
        assert reactivated.metadata == {"target_user_id": str(person.user.id), "role": "admin"}

    async def test_role_change_creates_one_member_role_changed_row(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        target = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "change", target.user, ADMIN)

        assert response.status_code == 200
        [entry] = await _audit(db_session, org_id)
        assert entry.action == "member.role_changed"
        assert entry.actor_user_id == owner.user.id
        assert entry.resource_id == await _row_id(db_session, org_id, target.user)
        assert entry.metadata == {
            "target_user_id": str(target.user.id),
            "previous_role": "member",
            "new_role": "admin",
        }

    async def test_removal_creates_one_member_removed_row(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        target = await _actor(db_session, org_id, VIEWER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "remove", target.user)

        assert response.status_code == 204
        [entry] = await _audit(db_session, org_id)
        assert entry.action == "member.removed"
        assert entry.metadata == {"target_user_id": str(target.user.id), "previous_role": "viewer"}
        assert await _state(db_session, org_id, target.user) == (VIEWER, REMOVED)

    async def test_an_admins_mutation_is_attributed_to_the_admin(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        admin = await _actor(db_session, org_id, ADMIN)
        target = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            await _do(client, org_id, admin.cookies, "remove", target.user)

        [entry] = await _audit(db_session, org_id)
        assert entry.actor_user_id == admin.user.id

    async def test_a_sequence_of_mutations_is_recorded_in_order_one_row_each(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        person = await _user(db_session)

        async with _client(wired_app) as client:
            await _do(client, org_id, owner.cookies, "add", person, MEMBER)
            await _do(client, org_id, owner.cookies, "change", person, VIEWER)
            await _do(client, org_id, owner.cookies, "remove", person)

        entries = await _audit(db_session, org_id)
        assert [e.action for e in entries] == [
            "member.removed",
            "member.role_changed",
            "member.added",
        ]  # newest first

    async def test_no_email_or_name_is_stored_anywhere_in_the_audit_row(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(
            db_session, org_id, OWNER, email="owner.secret@example.com", full_name="Olive Ownername"
        )
        added = await _user(db_session, email="added.secret@example.com", full_name="Adam Addname")
        changed = await _actor(
            db_session,
            org_id,
            MEMBER,
            email="changed.secret@example.com",
            full_name="Cher Changename",
        )
        removed = await _actor(
            db_session,
            org_id,
            VIEWER,
            email="removed.secret@example.com",
            full_name="Rob Removename",
        )

        async with _client(wired_app) as client:
            await _do(client, org_id, owner.cookies, "add", added, MEMBER)
            await _do(client, org_id, owner.cookies, "change", changed.user, ADMIN)
            await _do(client, org_id, owner.cookies, "remove", removed.user)

        await set_org_context(db_session, org_id)
        result = await db_session.execute(text("SELECT row_to_json(a)::text FROM audit_logs a"))
        rows: list[str] = list(result.scalars().all())
        await db_session.rollback()
        assert len(rows) == 3
        for row in rows:
            assert "@" not in row
            assert "secret" not in row
            assert "name" not in row.lower().replace("target_user_id", "")  # no *name* values
            for forbidden in ("Olive", "Adam", "Cher", "Rob"):
                assert forbidden not in row

    async def test_change_and_audit_row_are_visible_together_to_an_independent_connection(
        self, wired_app: FastAPI, db_session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        target = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            await _do(client, org_id, owner.cookies, "change", target.user, ADMIN)

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as independent:
            await set_org_context(independent, org_id)
            member = await SqlAlchemyOrganizationRepository(independent).get_member(
                org_id, target.user.id
            )
            entries = await SqlAlchemyAuditLogRepository(independent).list_for_organization(org_id)
        assert member is not None and member.role is ADMIN
        assert [e.action for e in entries] == ["member.role_changed"]


# ================================== nothing is audited unless it succeeded


class TestRefusedAndNoOpRequestsWriteNothing:
    async def test_every_kind_of_refusal_leaves_zero_audit_rows(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        admin = await _actor(db_session, org_id, ADMIN)
        viewer = await _actor(db_session, org_id, VIEWER)
        member = await _actor(db_session, org_id, MEMBER)
        newcomer = await _user(db_session)
        ghost = make_user()

        async with _client(wired_app) as client:
            responses = [
                await _do(client, org_id, viewer.cookies, "add", newcomer, VIEWER),  # 403
                await _do(client, org_id, admin.cookies, "add", newcomer, OWNER),  # 403
                await _do(client, org_id, admin.cookies, "remove", owner.user),  # 403
                await _do(client, org_id, owner.cookies, "add", ghost, VIEWER),  # 404 unknown
                await _do(client, org_id, owner.cookies, "add", member.user, ADMIN),  # 409 active
                await _do(client, org_id, owner.cookies, "remove", ghost),  # 404 member
                await _do(client, org_id, owner.cookies, "change", ghost, VIEWER),  # 404 member
                await _do(client, org_id, owner.cookies, "change", owner.user, ADMIN),  # 409 last
                await _do(client, org_id, owner.cookies, "remove", owner.user),  # 409 last
            ]

        assert [r.status_code for r in responses] == [403, 403, 403, 404, 409, 404, 404, 409, 409]
        assert await _audit(db_session, org_id) == []

    async def test_a_no_op_role_change_succeeds_but_writes_nothing(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        member = await _actor(db_session, org_id, MEMBER)

        async with _client(wired_app) as client:
            response = await _do(client, org_id, owner.cookies, "change", member.user, MEMBER)

        assert response.status_code == 200
        assert await _audit(db_session, org_id) == []


# ===================================================== RLS / tenant isolation


class TestAuditTenantIsolation:
    async def test_one_organizations_audit_rows_are_invisible_from_another(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        owner_a = await _actor(db_session, org_a, OWNER)
        await _actor(db_session, org_b, OWNER)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            await _do(client, org_a, owner_a.cookies, "add", newcomer, VIEWER)

        assert len(await _audit(db_session, org_a)) == 1
        assert await _audit(db_session, org_b) == []
        # org B's RLS context cannot read org A's rows even when it asks for them by id
        await set_org_context(db_session, org_b)
        leaked = await SqlAlchemyAuditLogRepository(db_session).list_for_organization(org_a)
        await db_session.rollback()
        assert leaked == []

    async def test_an_entry_always_carries_the_organization_of_the_request(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        both = await _user(db_session)
        await _join(db_session, org_a, both, OWNER)
        await _join(db_session, org_b, both, OWNER)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            await _do(client, org_b, _cookies(both), "add", newcomer, VIEWER)

        assert await _audit(db_session, org_a) == []
        [entry] = await _audit(db_session, org_b)
        assert entry.organization_id == org_b

    async def test_postgres_rejects_an_audit_row_for_another_organization(
        self, db_session: AsyncSession
    ) -> None:
        org_a, org_b = await _org(db_session), await _org(db_session)
        actor = await _user(db_session)

        await set_org_context(db_session, org_a)
        with pytest.raises(DBAPIError, match="row-level security"):
            await SqlAlchemyAuditLogRepository(db_session).add(
                AuditLogEntry(
                    id=new_id(),
                    action="member.added",
                    created_at=utcnow(),
                    organization_id=org_b,
                    actor_user_id=actor.id,
                )
            )
        await db_session.rollback()
        assert await _audit(db_session, org_b) == []


# ================================================ atomicity / fail-closed

_FAILURE_CASES = ["add", "reactivate", "change", "remove"]


async def _prepare(
    db: AsyncSession, org_id: UUID, case: str
) -> tuple[str, User, OrganizationRole | None, tuple[OrganizationRole, MembershipStatus] | None]:
    """-> (kind for ``_do``, target user, role to send, the target's state BEFORE)."""
    if case == "add":
        return "add", await _user(db), VIEWER, None
    if case == "reactivate":
        removed = await _actor(db, org_id, MEMBER, REMOVED)
        return "add", removed.user, VIEWER, (MEMBER, REMOVED)
    member = await _actor(db, org_id, MEMBER)
    if case == "change":
        return "change", member.user, VIEWER, (MEMBER, ACTIVE)
    return "remove", member.user, None, (MEMBER, ACTIVE)


class _FailAfterSuccess:
    """Runs the real use case to completion -- membership mutation AND audit
    row both written inside the request's transaction -- then raises, i.e.
    a failure after the work but before the commit."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def execute(self, **kwargs: Any) -> None:
        await self._inner.execute(**kwargs)
        raise RuntimeError("failure after the use case succeeded")


def _install_fail_after_success(app: FastAPI) -> None:
    def add_use_case(
        organizations: OrganizationRepositoryPort = Depends(get_org_organization_repository),  # noqa: B008
        users: UserRepositoryPort = Depends(get_org_user_repository),  # noqa: B008
        audit: AuditLogRepositoryPort = Depends(get_org_audit_log_repository),  # noqa: B008
    ) -> _FailAfterSuccess:
        return _FailAfterSuccess(AddMemberUseCase(organizations, users, audit))

    def change_use_case(
        organizations: OrganizationRepositoryPort = Depends(get_org_organization_repository),  # noqa: B008
        users: UserRepositoryPort = Depends(get_org_user_repository),  # noqa: B008
        audit: AuditLogRepositoryPort = Depends(get_org_audit_log_repository),  # noqa: B008
    ) -> _FailAfterSuccess:
        return _FailAfterSuccess(ChangeMemberRoleUseCase(organizations, users, audit))

    def remove_use_case(
        organizations: OrganizationRepositoryPort = Depends(get_org_organization_repository),  # noqa: B008
        audit: AuditLogRepositoryPort = Depends(get_org_audit_log_repository),  # noqa: B008
    ) -> _FailAfterSuccess:
        return _FailAfterSuccess(RemoveMemberUseCase(organizations, audit))

    app.dependency_overrides[get_add_member_use_case] = add_use_case
    app.dependency_overrides[get_change_member_role_use_case] = change_use_case
    app.dependency_overrides[get_remove_member_use_case] = remove_use_case


class TestAtomicity:
    @pytest.mark.parametrize("case", _FAILURE_CASES)
    async def test_a_forced_audit_insert_failure_rolls_the_membership_mutation_back(
        self,
        wired_app: FastAPI,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        kind, target, role, before = await _prepare(db_session, org_id, case)

        async def failing_add(self: SqlAlchemyAuditLogRepository, entry: AuditLogEntry) -> None:
            raise RuntimeError("forced audit insert failure")

        monkeypatch.setattr(SqlAlchemyAuditLogRepository, "add", failing_add)

        async with _client(wired_app, raise_app_exceptions=False) as client:
            response = await _do(client, org_id, owner.cookies, kind, target, role)

        assert response.status_code == 500  # the API did not report success
        assert await _state(db_session, org_id, target) == before  # no partial change
        monkeypatch.undo()
        assert await _audit(db_session, org_id) == []

    @pytest.mark.parametrize("case", _FAILURE_CASES)
    async def test_a_failure_after_the_use_case_rolls_back_the_mutation_and_the_audit_row_together(
        self, wired_app: FastAPI, db_session: AsyncSession, case: str
    ) -> None:
        """The use case ran to completion (both writes issued) and only then
        did the request fail. If the audit row were committed in its own
        transaction it would survive here; it must not."""
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        kind, target, role, before = await _prepare(db_session, org_id, case)
        _install_fail_after_success(wired_app)

        async with _client(wired_app, raise_app_exceptions=False) as client:
            response = await _do(client, org_id, owner.cookies, kind, target, role)

        assert response.status_code == 500
        assert await _state(db_session, org_id, target) == before
        assert await _audit(db_session, org_id) == []

    async def test_the_same_requests_succeed_normally_without_any_injected_failure(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        """Control for the two tests above: the setups used there do succeed
        and DO leave an audit row when nothing is forced to fail."""
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)

        async with _client(wired_app) as client:
            for case in _FAILURE_CASES:
                kind, target, role, _ = await _prepare(db_session, org_id, case)
                response = await _do(client, org_id, owner.cookies, kind, target, role)
                assert response.status_code in (200, 201, 204), case

        assert len(await _audit(db_session, org_id)) == len(_FAILURE_CASES)


# ====================================================== concurrency + M1/M2


class TestRacesAndRegression:
    @pytest.mark.parametrize(
        ("kind", "expected_action"),
        [
            ("remove", "member.removed"),
            ("change", "member.role_changed"),
        ],
    )
    async def test_a_two_owner_http_race_audits_exactly_the_winner(
        self, wired_app: FastAPI, db_session: AsyncSession, kind: str, expected_action: str
    ) -> None:
        async with _client(wired_app) as client:
            for _ in range(10):
                org_id = await _org(db_session)
                first = await _actor(db_session, org_id, OWNER)
                second = await _actor(db_session, org_id, OWNER)

                one, two = await asyncio.gather(
                    _do(client, org_id, first.cookies, kind, second.user, VIEWER),
                    _do(client, org_id, second.cookies, kind, first.user, VIEWER),
                )

                statuses = sorted([one.status_code, two.status_code])
                assert statuses == [204 if kind == "remove" else 200, 403]
                winner, loser = (first, second) if one.status_code < 300 else (second, first)
                [entry] = await _audit(db_session, org_id)  # the loser wrote nothing
                assert entry.action == expected_action
                assert entry.actor_user_id == winner.user.id
                assert entry.metadata is not None
                assert entry.metadata["target_user_id"] == str(loser.user.id)

    async def test_m1_scan_rbac_and_m2_member_matrix_are_unchanged_by_auditing(
        self, wired_app: FastAPI, db_session: AsyncSession
    ) -> None:
        org_id = await _org(db_session)
        owner = await _actor(db_session, org_id, OWNER)
        admin = await _actor(db_session, org_id, ADMIN)
        viewer = await _actor(db_session, org_id, VIEWER)
        newcomer = await _user(db_session)

        async with _client(wired_app) as client:
            # M1: VIEWER reads but cannot create scans; ADMIN can create
            scan = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies=admin.cookies,
            )
            viewer_create = await client.post(
                f"/api/v1/organizations/{org_id}/scans",
                json={"target": "example.com"},
                cookies=viewer.cookies,
            )
            viewer_read = await client.get(
                f"/api/v1/organizations/{org_id}/scans/{scan.json()['id']}", cookies=viewer.cookies
            )
            # M2: ADMIN may add a MEMBER but not an ADMIN/OWNER; VIEWER may list only
            admin_adds_member = await _do(client, org_id, admin.cookies, "add", newcomer, MEMBER)
            admin_adds_admin = await _do(client, org_id, admin.cookies, "change", newcomer, ADMIN)
            viewer_lists = await client.get(_url(org_id), cookies=viewer.cookies)
            owner_lists = await client.get(_url(org_id), cookies=owner.cookies)

        assert (scan.status_code, viewer_create.status_code, viewer_read.status_code) == (
            201,
            403,
            200,
        )
        assert (admin_adds_member.status_code, admin_adds_admin.status_code) == (201, 403)
        assert (viewer_lists.status_code, owner_lists.status_code) == (200, 200)
        assert [e.action for e in await _audit(db_session, org_id)] == ["member.added"]
