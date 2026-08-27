"""SQLAlchemy-backed implementations of the repository ports defined in
``app/application/interfaces/``. Re-exported here for a shorter import
path at call sites; the ports themselves remain the type callers should
depend on, not these concrete classes.
"""

from __future__ import annotations

from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.findings_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.reporting_repository import SqlAlchemyReportRepository
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository

__all__ = [
    "SqlAlchemyAssetRepository",
    "SqlAlchemyAuditLogRepository",
    "SqlAlchemyFindingRepository",
    "SqlAlchemyOrganizationRepository",
    "SqlAlchemyRefreshTokenRepository",
    "SqlAlchemyReportRepository",
    "SqlAlchemyScanRepository",
    "SqlAlchemyUserRepository",
]
