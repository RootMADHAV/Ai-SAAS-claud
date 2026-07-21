"""A single place to ask what time it is.

Used instead of scattering datetime.now(UTC) calls through the codebase --
not because anything needs dependency injection today, but because if a
later milestone needs deterministic time in a test (e.g. "has this asset
been silent for 30 days"), there's exactly one function to patch instead of
an unknown number of call sites. Kept as a plain function rather than an
injectable Clock class: nothing in this milestone needs the extra ceremony
of injecting one. Revisit if that changes -- don't build the abstraction
before something actually needs it.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
