"""Value objects for the findings bounded context.

Severity and CVSS are immutable and validate themselves on construction --
an invalid CVSS score or a malformed vector never exists as a live object,
it fails at the point of creation. This is the domain-layer half of "don't
let AI override authoritative data": CVSS.severity is the qualitative band
derived from a real, validated score, not a string an LLM produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from app.domain.shared.enums import SeverityLevel

_SEVERITY_ORDER: dict[SeverityLevel, int] = {
    SeverityLevel.INFO: 0,
    SeverityLevel.LOW: 1,
    SeverityLevel.MEDIUM: 2,
    SeverityLevel.HIGH: 3,
    SeverityLevel.CRITICAL: 4,
}

# CVSS v3.0/3.1 base vector, e.g. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H
# Temporal and environmental metric groups (E, RL, RC, CR, IR, AR, MAV, ...)
# are not validated here -- Nuclei templates report only the base vector in
# the overwhelming majority of cases. Extend this if that stops being true.
_CVSS_VECTOR_RE = re.compile(
    r"^CVSS:3\.[01]/AV:[NALP]/AC:[LH]/PR:[NLH]/UI:[NR]/S:[UC]"
    r"/C:[NLH]/I:[NLH]/A:[NLH]$"
)


@total_ordering
@dataclass(frozen=True, slots=True)
class Severity:
    """Ordered wrapper around SeverityLevel: info < low < medium < high <
    critical. Comparable directly (severity_a > severity_b) so callers
    never compare the raw enum values, which have no ordering of their
    own."""

    level: SeverityLevel

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return _SEVERITY_ORDER[self.level] < _SEVERITY_ORDER[other.level]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.level == other.level

    def __str__(self) -> str:
        return self.level.value


@dataclass(frozen=True, slots=True)
class CVSS:
    """A validated CVSS v3.x score and vector. Construction fails loudly on
    an out-of-range score or a vector that doesn't parse -- there is no
    such thing as an invalid CVSS object in this codebase."""

    score: float
    vector: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 10.0:
            raise ValueError(f"CVSS score must be 0.0-10.0, got {self.score}")
        if not _CVSS_VECTOR_RE.match(self.vector):
            raise ValueError(f"'{self.vector}' is not a valid CVSS v3.0/3.1 vector")

    @property
    def severity(self) -> Severity:
        """Qualitative band derived from the score, using the standard NVD
        thresholds. A finding's effective severity should prefer this over
        an AI-estimated severity whenever a CVE/CVSS score exists -- see
        decisions.md."""
        if self.score == 0.0:
            return Severity(SeverityLevel.INFO)
        if self.score < 4.0:
            return Severity(SeverityLevel.LOW)
        if self.score < 7.0:
            return Severity(SeverityLevel.MEDIUM)
        if self.score < 9.0:
            return Severity(SeverityLevel.HIGH)
        return Severity(SeverityLevel.CRITICAL)
