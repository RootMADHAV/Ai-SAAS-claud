"""Shared factories and helpers for the repository integration suite.

Not a conftest module -- these are plain importable helpers, not
fixtures, kept separate so each integration test module only pulls in
exactly the factories it needs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.assets.entities import Asset
from app.domain.findings.entities import Finding
from app.domain.identity.entities import Organization, User
from app.domain.scanning.entities import Scan
from app.domain.shared.clock import utcnow
from app.domain.shared.enums import (
    AssetStatus,
    AssetType,
    ConfidenceLevel,
    FindingStatus,
    ScanStatus,
)
from app.domain.shared.ids import new_id


async def set_org_context(session: AsyncSession, organization_id: UUID) -> None:
    """Mirrors what ``session_scoped_to_org``
    (app/infrastructure/db/session.py) does for production code, for
    tests that work with a ``db_session`` fixture directly rather than
    going through that context manager."""
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(organization_id)},
    )


def make_organization(**overrides: object) -> Organization:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "name": "Test Org",
        "slug": f"test-org-{new_id()}",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Organization(**defaults)  # type: ignore[arg-type]


def make_user(**overrides: object) -> User:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "email": f"{new_id()}@example.com",
        "full_name": "Test User",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def make_asset(organization_id: UUID, **overrides: object) -> Asset:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": organization_id,
        "asset_type": AssetType.DOMAIN,
        "value": f"{new_id()}.example.com",
        "status": AssetStatus.ACTIVE,
        "first_seen_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Asset(**defaults)  # type: ignore[arg-type]


def make_scan(organization_id: UUID, **overrides: object) -> Scan:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": organization_id,
        "target": "example.com",
        "scanner_name": "nuclei",
        "status": ScanStatus.QUEUED,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Scan(**defaults)  # type: ignore[arg-type]


def make_finding(organization_id: UUID, asset_id: UUID, **overrides: object) -> Finding:
    now = utcnow()
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": organization_id,
        "asset_id": asset_id,
        "fingerprint": new_id().hex,
        "title": "Test Finding",
        "status": FindingStatus.NEW,
        "confidence": ConfidenceLevel.UNCONFIRMED,
        "first_seen_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def utcnow_stable() -> datetime:
    """Re-exported for test modules that need "a" timestamp without
    caring about the exact value, without importing the clock module
    directly and looking like they're testing clock behavior."""
    return utcnow()
