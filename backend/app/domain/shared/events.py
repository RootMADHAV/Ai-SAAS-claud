"""Base for domain events.

Concrete events -- FindingCreated, ScanCompleted, FindingStatusChanged --
live in the bounded context that raises them (domain/findings/,
domain/scanning/), not here, once those contexts are built. This module
only holds what every event has in common, per the event bus design in
docs/decisions.md: an id (for idempotency and tracing once the event bus
exists) and a timestamp.

Not slotted, unlike the value objects in findings/value_objects.py --
this class is meant to be subclassed, and dataclass inheritance with
__slots__ has enough sharp edges (field ordering, slot conflicts across
levels) that the small memory saving isn't worth it for something created
at "one event per finding," not "millions of times per second."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.shared.clock import utcnow
from app.domain.shared.ids import new_id


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    event_id: UUID = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=utcnow)
