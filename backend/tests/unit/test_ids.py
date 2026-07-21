from __future__ import annotations

import time
from uuid import UUID

import pytest
from ulid import ULID

from app.domain.shared.ids import id_from_string, new_id


def test_new_id_returns_a_uuid() -> None:
    assert isinstance(new_id(), UUID)


def test_new_ids_are_unique() -> None:
    assert new_id() != new_id()


def test_ids_are_time_ordered() -> None:
    """The whole point of using ULIDs: an id generated later sorts after
    one generated earlier, once both are stored as plain UUIDs."""
    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first < second


def test_id_from_string_round_trips_a_ulid_string() -> None:
    ulid = ULID()
    assert id_from_string(str(ulid)) == ulid.to_uuid()


def test_id_from_string_accepts_a_plain_uuid_string() -> None:
    original = new_id()
    assert id_from_string(str(original)) == original


def test_id_from_string_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        id_from_string("not-an-id")
