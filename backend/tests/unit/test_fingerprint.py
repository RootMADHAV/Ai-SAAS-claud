from __future__ import annotations

import pytest

from app.domain.shared.fingerprint import compute_fingerprint


def test_fingerprint_is_deterministic() -> None:
    assert compute_fingerprint("org1", "example.com", "nuclei", "cve-2024-1234") == (
        compute_fingerprint("org1", "example.com", "nuclei", "cve-2024-1234")
    )


def test_fingerprint_is_order_sensitive() -> None:
    assert compute_fingerprint("a", "b") != compute_fingerprint("b", "a")


def test_fingerprint_distinguishes_part_boundaries() -> None:
    """Without a real separator, ("ab", "c") and ("a", "bc") would hash the
    same way if parts were just concatenated. This is what the unit
    separator in fingerprint.py actually buys."""
    assert compute_fingerprint("ab", "c") != compute_fingerprint("a", "bc")


def test_fingerprint_is_a_hex_sha256() -> None:
    result = compute_fingerprint("x")
    assert len(result) == 64
    int(result, 16)  # raises ValueError if not valid hex


def test_fingerprint_requires_at_least_one_part() -> None:
    with pytest.raises(ValueError):
        compute_fingerprint()
