"""Import every ORM model module so ``Base.metadata`` (app/infrastructure/
db/base.py) reflects the full schema in one place. Alembic's
``env.py`` imports this package -- not each module individually -- so
adding a new model module here is the one step that makes autogenerate
see it.
"""

from __future__ import annotations

from app.infrastructure.db.models.assets import Asset, AssetObservation, AssetRelationship
from app.infrastructure.db.models.findings import (
    Finding,
    FindingAnalysis,
    FindingOccurrence,
    FindingStatusHistory,
)
from app.infrastructure.db.models.identity import (
    AuditLog,
    OAuthAccount,
    Organization,
    OrganizationMember,
    RefreshToken,
    User,
)
from app.infrastructure.db.models.platform import FeatureFlag, SystemSetting
from app.infrastructure.db.models.reporting import Report
from app.infrastructure.db.models.scanning import Scan, ScanScope, ScanWorkflowStep

__all__ = [
    "Asset",
    "AssetObservation",
    "AssetRelationship",
    "AuditLog",
    "FeatureFlag",
    "Finding",
    "FindingAnalysis",
    "FindingOccurrence",
    "FindingStatusHistory",
    "OAuthAccount",
    "Organization",
    "OrganizationMember",
    "RefreshToken",
    "Report",
    "Scan",
    "ScanScope",
    "ScanWorkflowStep",
    "SystemSetting",
    "User",
]
