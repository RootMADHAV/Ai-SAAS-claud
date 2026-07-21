from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id


def test_domain_event_generates_a_unique_id() -> None:
    assert DomainEvent().event_id != DomainEvent().event_id


def test_domain_event_records_occurred_at_in_utc() -> None:
    assert DomainEvent().occurred_at.tzinfo is not None


def test_domain_event_is_immutable() -> None:
    event = DomainEvent()
    with pytest.raises(AttributeError):
        event.event_id = new_id()  # type: ignore[misc]


def test_domain_event_can_be_subclassed_with_extra_fields() -> None:
    """Proves the base composes correctly with a concrete event -- this is
    the actual shape FindingCreated and friends will take once the
    findings/scanning bounded contexts exist."""

    @dataclass(frozen=True, kw_only=True)
    class _ExampleEvent(DomainEvent):
        thing_id: UUID

    event = _ExampleEvent(thing_id=new_id())
    assert isinstance(event.event_id, UUID)
    assert isinstance(event.thing_id, UUID)
    assert event.event_id != event.thing_id
