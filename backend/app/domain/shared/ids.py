"""Shared ID generation. ULIDs, stored as native Postgres UUID columns.

A ULID is 128 bits, the same width as a UUID: 48 bits of millisecond
timestamp plus 80 bits of randomness. Storing it in a UUID column costs
nothing (same 16 bytes, same index type) and buys two things over
uuid4(): rows insert in roughly time order, which keeps B-tree index pages
from fragmenting on high-insert-rate tables (scan_workflow_steps,
asset_observations, audit_logs); and IDs are naturally sortable by
creation time without a separate created_at comparison. No compatibility
cost was found in the python-ulid library -- hence "consider ULIDs if no
compatibility concerns" resolved to yes. See docs/decisions.md.
"""

from __future__ import annotations

from uuid import UUID

from ulid import ULID


def new_id() -> UUID:
    """Generate a new identifier. Returns a stdlib UUID so every other
    layer -- SQLAlchemy columns, Pydantic schemas, FastAPI path params --
    keeps using plain UUID and never has to know a ULID was involved."""
    return ULID().to_uuid()


def id_from_string(value: str) -> UUID:
    """Parse either a 26-character ULID string or a standard 36-character
    UUID string into a UUID. Accepts both because migrations, fixtures, and
    API clients may hand either form. The two formats never collide -- a
    ULID string is always 26 characters with no hyphens, a UUID string is
    always 36 characters with hyphens -- so trying ULID first is safe."""
    try:
        return ULID.from_str(value).to_uuid()
    except (ValueError, TypeError, AttributeError):
        # AttributeError is included defensively: python-ulid added from_str
        # in v2/v3; if an older version is installed, the AttributeError would
        # otherwise propagate instead of falling through to UUID(value).  The
        # correct package is python-ulid>=3.0 (see pyproject.toml) -- running
        # ``pip install -e '.[dev]'`` from backend/ will install the right
        # version.  The UUID() call below will raise ValueError on garbage
        # input, which is the documented behaviour of id_from_string.
        return UUID(value)
