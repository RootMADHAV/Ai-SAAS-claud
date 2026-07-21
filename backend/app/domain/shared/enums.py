"""Domain enums -- the concrete answer to refinement #1: prefer enums or
constrained types over free-text status fields.

Every value here was already settled during architecture review (see
decisions.md); this module just writes that settled vocabulary down as
code, ahead of the entities that will use most of it.

STAGING NOTE: this file is a temporary home, not a permanent shared
dumping ground. Only SeverityLevel is actually consumed by Milestone 1
code (Severity and CVSS in domain/findings/value_objects.py). The rest --
FindingStatus, ScanStatus, WorkflowStep*, AssetType, AssetStatus,
OrganizationRole, MembershipStatus, ReportFormat -- belong to bounded
contexts (findings/, scanning/, assets/, identity/, reporting/) that don't
exist as domain packages yet. As each of those gets built in its own
milestone, the enums it owns move out of this file and into that module's
own enums.py. Keeping them all here in the meantime, rather than creating
five near-empty package directories today, is the pragmatic call -- but
it's a call worth revisiting once those packages exist, not a permanent
architectural position.
"""

from __future__ import annotations

from enum import StrEnum


class SeverityLevel(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    FIXED = "fixed"
    ACCEPTED_RISK = "accepted_risk"
    WONT_FIX = "wont_fix"


class ConfidenceLevel(StrEnum):
    """Qualitative triage label on a finding -- distinct from the numeric
    0.0-1.0 confidence score on asset_observations and asset_relationships,
    which stays a plain float. Same word, two different concepts; kept
    separate rather than forced into one shape."""

    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStepName(StrEnum):
    """The approved scan processing pipeline, in order:
    Raw Output -> Normalization -> Deduplication -> Correlation ->
    Enrichment -> AI Analysis -> Persistence. VALIDATE_TARGET and
    EXECUTE_SCANNER precede "Raw Output" as preconditions; Reporting is a
    separate, user-triggered consumer of this pipeline's output, not a
    step within it -- see decisions.md."""

    VALIDATE_TARGET = "validate_target"
    EXECUTE_SCANNER = "execute_scanner"
    NORMALIZE = "normalize"
    DEDUPLICATE = "deduplicate"
    CORRELATE = "correlate"
    ENRICH = "enrich"
    AI_ANALYZE = "ai_analyze"
    PERSIST = "persist"


class AssetType(StrEnum):
    DOMAIN = "domain"
    SUBDOMAIN = "subdomain"
    IP = "ip"
    PORT = "port"
    TECHNOLOGY = "technology"
    CERTIFICATE = "certificate"
    ENDPOINT = "endpoint"


class AssetStatus(StrEnum):
    """Manual for now -- no auto-decay based on last_seen_at yet. See
    decisions.md on why that's deferred rather than built speculatively."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class OrganizationRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    REMOVED = "removed"


class ReportFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"
    DOCX = "docx"
