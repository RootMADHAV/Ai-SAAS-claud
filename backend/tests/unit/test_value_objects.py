from __future__ import annotations

import pytest

from app.domain.findings.value_objects import CVSS, Severity
from app.domain.shared.enums import SeverityLevel

_VALID_VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


def test_severity_ordering() -> None:
    assert Severity(SeverityLevel.INFO) < Severity(SeverityLevel.LOW)
    assert Severity(SeverityLevel.LOW) < Severity(SeverityLevel.MEDIUM)
    assert Severity(SeverityLevel.MEDIUM) < Severity(SeverityLevel.HIGH)
    assert Severity(SeverityLevel.HIGH) < Severity(SeverityLevel.CRITICAL)
    assert Severity(SeverityLevel.CRITICAL) > Severity(SeverityLevel.INFO)


def test_severity_equality() -> None:
    assert Severity(SeverityLevel.HIGH) == Severity(SeverityLevel.HIGH)
    assert Severity(SeverityLevel.HIGH) != Severity(SeverityLevel.LOW)


def test_severity_str_returns_the_plain_value() -> None:
    assert str(Severity(SeverityLevel.HIGH)) == "high"


def test_severity_comparison_with_unrelated_type() -> None:
    """__eq__ and __lt__ both return NotImplemented for a non-Severity
    operand -- confirming they hand off to Python's normal fallback
    behavior rather than raising internally or silently comparing enum
    values against arbitrary objects."""
    assert Severity(SeverityLevel.HIGH) != "high"
    with pytest.raises(TypeError):
        _ = Severity(SeverityLevel.HIGH) < "high"  # type: ignore[operator]


def test_severity_sorts_a_list() -> None:
    levels = [SeverityLevel.HIGH, SeverityLevel.INFO, SeverityLevel.CRITICAL, SeverityLevel.LOW]
    ordered = sorted(Severity(level) for level in levels)
    assert [s.level for s in ordered] == [
        SeverityLevel.INFO,
        SeverityLevel.LOW,
        SeverityLevel.HIGH,
        SeverityLevel.CRITICAL,
    ]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, SeverityLevel.INFO),
        (0.1, SeverityLevel.LOW),
        (3.9, SeverityLevel.LOW),
        (4.0, SeverityLevel.MEDIUM),
        (6.9, SeverityLevel.MEDIUM),
        (7.0, SeverityLevel.HIGH),
        (8.9, SeverityLevel.HIGH),
        (9.0, SeverityLevel.CRITICAL),
        (10.0, SeverityLevel.CRITICAL),
    ],
)
def test_cvss_severity_band_boundaries(score: float, expected: SeverityLevel) -> None:
    cvss = CVSS(score=score, vector=_VALID_VECTOR)
    assert cvss.severity == Severity(expected)


@pytest.mark.parametrize("bad_score", [-0.1, 10.1, 11.0, -5.0])
def test_cvss_rejects_out_of_range_score(bad_score: float) -> None:
    with pytest.raises(ValueError, match="0.0-10.0"):
        CVSS(score=bad_score, vector=_VALID_VECTOR)


@pytest.mark.parametrize(
    "bad_vector",
    [
        "not-a-vector",
        "CVSS:2.0/AV:N/AC:L/Au:N/C:N/I:N/A:C",  # v2 format, not yet supported
        "CVSS:3.1/AV:N/AC:L",  # incomplete
        "",
    ],
)
def test_cvss_rejects_invalid_vector(bad_vector: str) -> None:
    with pytest.raises(ValueError, match="not a valid CVSS"):
        CVSS(score=7.5, vector=bad_vector)


def test_cvss_accepts_v30_and_v31() -> None:
    CVSS(score=9.8, vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    CVSS(score=9.8, vector="CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")


def test_cvss_is_immutable() -> None:
    cvss = CVSS(score=5.0, vector=_VALID_VECTOR)
    with pytest.raises(AttributeError):
        cvss.score = 9.9  # type: ignore[misc]
