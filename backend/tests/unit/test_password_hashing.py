"""Unit tests for app.infrastructure.security.password_hashing."""

from __future__ import annotations

import pytest

from app.infrastructure.security.password_hashing import hash_password, verify_password


def test_hash_password_returns_a_different_string_than_the_input() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert len(hashed) > 0


def test_hash_password_is_salted_and_nondeterministic() -> None:
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second


def test_verify_password_accepts_the_correct_password() -> None:
    hashed = hash_password("s3cr3t-passphrase")
    assert verify_password(password="s3cr3t-passphrase", hashed_password=hashed) is True


def test_verify_password_rejects_the_wrong_password() -> None:
    hashed = hash_password("s3cr3t-passphrase")
    assert verify_password(password="wrong-passphrase", hashed_password=hashed) is False


def test_verify_password_rejects_empty_password_against_real_hash() -> None:
    hashed = hash_password("s3cr3t-passphrase")
    assert verify_password(password="", hashed_password=hashed) is False


def test_hash_password_rejects_passwords_over_72_bytes() -> None:
    too_long = "a" * 73
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password(too_long)


def test_hash_password_accepts_password_at_exactly_72_bytes() -> None:
    exactly_72 = "a" * 72
    hashed = hash_password(exactly_72)
    assert verify_password(password=exactly_72, hashed_password=hashed) is True


def test_verify_password_returns_false_rather_than_raising_for_over_length_input() -> None:
    hashed = hash_password("normal-password")
    too_long = "a" * 100
    assert verify_password(password=too_long, hashed_password=hashed) is False
