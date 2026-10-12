"""Audit vocabulary for organization membership changes -- Phase 6 M3.

Pure domain naming, nothing else: no framework, no event bus, no generic
audit-event abstraction. The audit *record* is the existing
``AuditLogEntry`` (``audit_logs`` table); this module only fixes the names
a membership audit entry uses, because those strings are a contract: they
are stored, and a future audit reader will query by them. Renaming one is
a data migration, so a unit test pins the exact values.

Action names follow the project's ``resource.verb`` convention (the
existing audit-repository test already uses ``organization.created``).

Scope: SUCCESSFUL membership mutations only -- see
``app/application/identity/manage_members.py`` for what is written, when,
and in which transaction. Authentication events, scan actions, denied or
failed attempts, and organization creation are NOT audited by this module.
"""

from __future__ import annotations

from enum import StrEnum

#: ``AuditLogEntry.resource_type`` for every membership audit entry; the
#: entry's ``resource_id`` is the membership row's id.
MEMBER_AUDIT_RESOURCE_TYPE = "organization_member"


class MemberAuditAction(StrEnum):
    """The four membership events that are audited."""

    ADDED = "member.added"
    REACTIVATED = "member.reactivated"
    ROLE_CHANGED = "member.role_changed"
    REMOVED = "member.removed"
