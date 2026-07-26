"""Domain entities for the Findings & Analysis bounded context.

Extends value_objects.py (Severity, CVSS -- Milestone 1, unchanged) with
the entities that use them. See app/domain/identity/entities.py's module
docstring for the shared scope rationale.

``Finding.effective_severity`` implements the rule named in
PROJECT_STATE.md section 5 verbatim: "effective_severity prefers CVSS
over an AI estimate." It is a computed property, not a persisted column
-- see the persistence model's docstring (app/infrastructure/db/models/
findings.py) for why storing a pre-computed value here would let it
drift from its own inputs. The finding status state machine
(``new -> triaged -> {confirmed, false_positive} -> ...``) is not
encoded as transition methods on this entity: no later milestone in the
current roadmap is named as the place that builds it, and PROJECT_STATE.md
section 12 requires a genuine blocker to be explained before adopting new
behavior -- inventing transition rules now, with no consuming use case to
validate them against, is exactly the kind of un-validated design this
project's workflow rules exist to prevent. ``status`` is a plain field,
settable by whichever future milestone implements the triage workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.findings.value_objects import CVSS, Severity
from app.domain.shared.enums import ConfidenceLevel, FindingStatus, SeverityLevel


@dataclass(slots=True)
class Finding:
    """Keyed by ``(organization_id, fingerprint)`` at the persistence
    layer -- a recurrence updates ``last_seen_at`` on the same row rather
    than creating a duplicate (PROJECT_STATE.md section 3)."""

    id: UUID
    organization_id: UUID
    asset_id: UUID
    fingerprint: str
    title: str
    status: FindingStatus
    confidence: ConfidenceLevel
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    ai_severity_level: SeverityLevel | None = None
    cvss_score: float | None = None
    cvss_vector: str | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def cvss(self) -> CVSS | None:
        """Reconstructs the validated ``CVSS`` value object from the
        persisted score/vector pair, or ``None`` if this finding has no
        CVSS data yet. Constructing it here (rather than storing a CVSS
        instance directly) re-validates on every access, which is
        appropriate for data that ultimately came from a database row an
        external process could have written to."""
        if self.cvss_score is None or self.cvss_vector is None:
            return None
        return CVSS(score=self.cvss_score, vector=self.cvss_vector)

    @property
    def effective_severity(self) -> Severity | None:
        """CVSS wins over an AI estimate whenever both exist
        (PROJECT_STATE.md section 5). Returns ``None`` only if neither a
        CVSS score nor an AI estimate has ever been recorded."""
        cvss = self.cvss
        if cvss is not None:
            return cvss.severity
        if self.ai_severity_level is not None:
            return Severity(self.ai_severity_level)
        return None


@dataclass(slots=True)
class FindingOccurrence:
    """Append-only per-scan history, mirroring ``AssetObservation``."""

    id: UUID
    organization_id: UUID
    finding_id: UUID
    scan_id: UUID
    asset_id: UUID
    detected_at: datetime
    created_at: datetime
    raw_evidence: dict[str, object] | None = None


@dataclass(slots=True)
class FindingAnalysis:
    """Append-only AI analysis record, never overwritten."""

    id: UUID
    organization_id: UUID
    finding_id: UUID
    prompt_version: str
    created_at: datetime
    kb_version: str | None = None
    model_metadata: dict[str, object] | None = None
    ai_summary: str | None = None
    ai_severity_estimate: SeverityLevel | None = None
    remediation_advice: str | None = None


@dataclass(slots=True)
class FindingStatusHistory:
    """Append-only triage-state audit trail entry."""

    id: UUID
    organization_id: UUID
    finding_id: UUID
    to_status: FindingStatus
    created_at: datetime
    from_status: FindingStatus | None = None
    changed_by_user_id: UUID | None = None
    reason: str | None = None
