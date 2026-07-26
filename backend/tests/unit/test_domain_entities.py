"""Unit tests for the plain-dataclass domain entities added in Milestone 2
-- no database needed, these are pure Python behavior: the ``is_deleted``
convenience property every soft-delete-participating entity exposes, and
``Finding``'s CVSS-over-AI-estimate severity precedence rule.
"""

from __future__ import annotations

from app.domain.assets.entities import Asset
from app.domain.findings.entities import Finding
from app.domain.findings.value_objects import Severity
from app.domain.identity.entities import Organization, User
from app.domain.reporting.entities import Report
from app.domain.scanning.entities import Scan
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import (
    AssetStatus,
    AssetType,
    ConfidenceLevel,
    FindingStatus,
    ReportFormat,
    ScanStatus,
    SeverityLevel,
)
from app.domain.shared.ids import new_id

_NOW = utcnow()
_VALID_VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


def test_organization_is_deleted_reflects_deleted_at() -> None:
    org = Organization(id=new_id(), name="Acme", slug="acme", created_at=_NOW, updated_at=_NOW)
    assert org.is_deleted is False
    org.deleted_at = _NOW
    assert org.is_deleted is True


def test_user_is_deleted_reflects_deleted_at() -> None:
    user = User(
        id=new_id(),
        email="a@example.com",
        full_name="A",
        is_active=True,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert user.is_deleted is False
    user.deleted_at = _NOW
    assert user.is_deleted is True


def test_asset_is_deleted_reflects_deleted_at() -> None:
    asset = Asset(
        id=new_id(),
        organization_id=new_id(),
        asset_type=AssetType.DOMAIN,
        value="example.com",
        status=AssetStatus.ACTIVE,
        first_seen_at=_NOW,
        last_seen_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert asset.is_deleted is False
    asset.deleted_at = _NOW
    assert asset.is_deleted is True


def test_scan_is_deleted_reflects_deleted_at() -> None:
    scan = Scan(
        id=new_id(),
        organization_id=new_id(),
        target="example.com",
        scanner_name="nuclei",
        status=ScanStatus.QUEUED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert scan.is_deleted is False
    scan.deleted_at = _NOW
    assert scan.is_deleted is True


def test_report_is_deleted_reflects_deleted_at() -> None:
    report = Report(
        id=new_id(),
        organization_id=new_id(),
        format=ReportFormat.PDF,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert report.is_deleted is False
    report.deleted_at = _NOW
    assert report.is_deleted is True


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": new_id(),
        "asset_id": new_id(),
        "fingerprint": "fp",
        "title": "Test",
        "status": FindingStatus.NEW,
        "confidence": ConfidenceLevel.UNCONFIRMED,
        "first_seen_at": _NOW,
        "last_seen_at": _NOW,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_finding_effective_severity_is_none_with_no_data() -> None:
    assert _finding().effective_severity is None
    assert _finding().cvss is None


def test_finding_effective_severity_falls_back_to_ai_estimate() -> None:
    finding = _finding(ai_severity_level=SeverityLevel.MEDIUM)
    assert finding.cvss is None
    assert finding.effective_severity == Severity(SeverityLevel.MEDIUM)


def test_finding_effective_severity_prefers_cvss_over_ai_estimate() -> None:
    """The rule named verbatim in PROJECT_STATE.md section 5."""
    finding = _finding(
        ai_severity_level=SeverityLevel.LOW,
        cvss_score=9.8,
        cvss_vector=_VALID_VECTOR,
    )
    assert finding.effective_severity == Severity(SeverityLevel.CRITICAL)


def test_finding_is_deleted_reflects_deleted_at() -> None:
    finding = _finding()
    assert finding.is_deleted is False
    finding.deleted_at = _NOW
    assert finding.is_deleted is True
