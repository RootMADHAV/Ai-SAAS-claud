"""Stable identity hash for cross-scan finding deduplication.

A finding's fingerprint answers "have we seen this exact issue before" --
independent of its row id, which would otherwise be different every time a
scan re-detects the same vulnerability. This is the concrete mechanism
behind the finding_occurrences redesign in docs/decisions.md: findings are
now keyed by (org_id, fingerprint), and a fingerprint match is what turns a
new scan result into an update of last_seen_at instead of a duplicate row.
"""

from __future__ import annotations

import hashlib

_SEPARATOR = "\x1f"  # ASCII unit separator -- won't collide with real values,
# unlike "-" or ":" which a hostname, path, or template id could contain.


def compute_fingerprint(*parts: str) -> str:
    """Deterministic hash over an ordered sequence of strings -- typically
    org_id, asset value, scanner source, and the scanner's own rule or
    template id. Order matters: callers must pass parts in the same order
    every time or the same logical finding will get different fingerprints."""
    if not parts:
        raise ValueError("compute_fingerprint requires at least one part")
    joined = _SEPARATOR.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
