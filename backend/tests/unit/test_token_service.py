"""Unit tests for app.infrastructure.security.token_service."""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest

from app.infrastructure.security.token_service import (
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

_SECRET = "test-jwt-secret"


def test_create_and_decode_access_token_roundtrips_the_user_id() -> None:
    user_id = uuid4()
    token = create_access_token(user_id=user_id, secret=_SECRET, expire_minutes=15)
    decoded = decode_access_token(token, secret=_SECRET)
    assert decoded == user_id


def test_decode_access_token_rejects_a_token_signed_with_a_different_secret() -> None:
    user_id = uuid4()
    token = create_access_token(user_id=user_id, secret=_SECRET, expire_minutes=15)
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, secret="a-different-secret")


def test_decode_access_token_rejects_an_expired_token() -> None:
    user_id = uuid4()
    token = create_access_token(user_id=user_id, secret=_SECRET, expire_minutes=-1)
    with pytest.raises(InvalidAccessTokenError, match="expired|Signature"):
        decode_access_token(token, secret=_SECRET)


def test_decode_access_token_rejects_malformed_input() -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token("not-a-real-jwt", secret=_SECRET)


def test_decode_access_token_rejects_a_token_missing_the_access_type_claim() -> None:
    # Simulates a token that was signed for some other purpose (e.g. a
    # future password-reset token) being replayed as an access token.
    now = int(time.time())
    other_token = jwt.encode(
        {"sub": str(uuid4()), "type": "not-access", "iat": now, "exp": now + 900},
        _SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError, match="not an access token"):
        decode_access_token(other_token, secret=_SECRET)


def test_decode_access_token_rejects_a_non_uuid_subject_claim() -> None:
    now = int(time.time())
    bad_token = jwt.encode(
        {"sub": "not-a-uuid", "type": "access", "iat": now, "exp": now + 900},
        _SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError, match="subject"):
        decode_access_token(bad_token, secret=_SECRET)


def test_generate_refresh_token_produces_unique_high_entropy_values() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()
    assert first != second
    assert len(first) >= 32


def test_hash_refresh_token_is_deterministic() -> None:
    raw = generate_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)


def test_hash_refresh_token_differs_for_different_inputs() -> None:
    assert hash_refresh_token("token-a") != hash_refresh_token("token-b")


def test_hash_refresh_token_never_equals_the_raw_token() -> None:
    raw = generate_refresh_token()
    assert hash_refresh_token(raw) != raw
