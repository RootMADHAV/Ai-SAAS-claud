from __future__ import annotations

from app.domain.shared.clock import utcnow


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo is not None


def test_utcnow_is_utc() -> None:
    offset = utcnow().utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


def test_utcnow_advances() -> None:
    first = utcnow()
    second = utcnow()
    assert second >= first
