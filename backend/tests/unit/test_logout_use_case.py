"""Unit tests for LogoutUseCase, with a fake repository."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.application.identity.logout import LogoutUseCase
from app.application.interfaces.identity_repository import RefreshTokenRepositoryPort
from app.domain.identity.entities import RefreshToken
from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id
from app.infrastructure.security.token_service import generate_refresh_token, hash_refresh_token


class FakeRefreshTokenRepository(RefreshTokenRepositoryPort):
    def __init__(self) -> None:
        self.tokens: dict[UUID, RefreshToken] = {}
        self.revoke_calls: list[UUID] = []

    async def add(self, token: RefreshToken) -> None:
        self.tokens[token.id] = token

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        for token in self.tokens.values():
            if token.token_hash == token_hash:
                return token
        return None

    async def revoke(self, token_id: UUID) -> None:
        self.revoke_calls.append(token_id)
        self.tokens[token_id].revoked_at = utcnow()


async def _seed_refresh_token(
    repo: FakeRefreshTokenRepository, user_id: UUID, **overrides: object
) -> str:
    raw = generate_refresh_token()
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "user_id": user_id,
        "token_hash": hash_refresh_token(raw),
        "expires_at": now + timedelta(days=30),
        "created_at": now,
        "revoked_at": None,
    }
    defaults.update(overrides)
    token = RefreshToken(**defaults)  # type: ignore[arg-type]
    await repo.add(token)
    return raw


async def test_logout_revokes_a_valid_token() -> None:
    repo = FakeRefreshTokenRepository()
    user_id = new_id()
    raw = await _seed_refresh_token(repo, user_id)
    use_case = LogoutUseCase(refresh_token_repository=repo)

    await use_case.execute(raw_refresh_token=raw)

    stored = await repo.get_by_token_hash(hash_refresh_token(raw))
    assert stored is not None
    assert stored.revoked_at is not None
    assert len(repo.revoke_calls) == 1


async def test_logout_is_a_no_op_for_an_unknown_token_not_an_error() -> None:
    repo = FakeRefreshTokenRepository()
    use_case = LogoutUseCase(refresh_token_repository=repo)

    # Must not raise -- an unknown token means the caller is already
    # logged out, which is the desired end state, not a failure.
    await use_case.execute(raw_refresh_token="a-token-that-was-never-issued")

    assert repo.revoke_calls == []


async def test_logout_is_a_no_op_for_an_already_revoked_token() -> None:
    repo = FakeRefreshTokenRepository()
    user_id = new_id()
    raw = await _seed_refresh_token(repo, user_id, revoked_at=utcnow())
    use_case = LogoutUseCase(refresh_token_repository=repo)

    await use_case.execute(raw_refresh_token=raw)

    # revoke() must not be called again on an already-revoked token --
    # see LogoutUseCase's own docstring on why (preserves the original
    # revocation timestamp as an accurate record).
    assert repo.revoke_calls == []


async def test_logout_is_a_no_op_for_an_expired_but_not_yet_revoked_token() -> None:
    """Unlike RefreshTokenUseCase (which must reject an expired token),
    LogoutUseCase still revokes an expired-but-not-yet-revoked token --
    see the use case's own docstring on why this keeps revoked_at an
    accurate "deliberately ended" record rather than leaving it alone
    just because the token was already unusable either way."""
    repo = FakeRefreshTokenRepository()
    user_id = new_id()
    raw = await _seed_refresh_token(repo, user_id, expires_at=utcnow() - timedelta(seconds=1))
    use_case = LogoutUseCase(refresh_token_repository=repo)

    await use_case.execute(raw_refresh_token=raw)

    stored = await repo.get_by_token_hash(hash_refresh_token(raw))
    assert stored is not None
    assert stored.revoked_at is not None
