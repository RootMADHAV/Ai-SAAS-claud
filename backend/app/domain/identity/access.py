"""Role-based access policy for the Identity & Access bounded context --
Phase 6 Milestone 1 (RBAC enforcement on the existing Scanning routes).

Pure domain logic, zero framework imports: *which* ``OrganizationRole``
may do *what* is a business rule, not an HTTP concern. The API layer
(``app/api/dependencies.py``'s ``require_scan_write_access``) only
translates a denial into a 403; the rule itself lives here so it is
testable without FastAPI or a database.

Scope, deliberately narrow: only the capabilities that actually exist
today. The Scanning routes are the only role-gated surface, and they need
exactly two decisions:

  - **Read a scan** (``GET .../scans/{scan_id}``): every ``ACTIVE``
    member, whatever their role. That is exactly what
    ``require_organization_member`` already enforces, so no separate
    "read" policy object exists here -- encoding "all four roles" as a
    constant nothing consumes would be dead code. The role matrix test
    in ``tests/integration/test_api_scans.py`` asserts the VIEWER-can-read
    half end to end.
  - **Create or run a scan** (``POST .../scans``,
    ``POST .../scans/{scan_id}/run``): OWNER, ADMIN, and MEMBER. VIEWER
    is read-only.

Role matrix (decided explicitly for this milestone, not inferred from
earlier docs -- none defined what the four roles may do):

    ============  =====  ===========================
    role          read   create / run scans
    ============  =====  ===========================
    OWNER         yes    yes
    ADMIN         yes    yes
    MEMBER        yes    yes
    VIEWER        yes    no
    ============  =====  ===========================

Not built, by design: a general permission/capability framework. There
is no second role-gated action to generalize over yet (organization
management, member management, billing and teams are all still
unbuilt), so any new capability should add its own named constant here
when a real route needs it -- not a speculative permission registry now.
"""

from __future__ import annotations

from app.domain.shared.enums import OrganizationRole

#: Roles allowed to create or run a scan. VIEWER is deliberately absent.
SCAN_WRITE_ROLES: frozenset[OrganizationRole] = frozenset(
    {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.MEMBER,
    }
)


def can_write_scans(role: OrganizationRole) -> bool:
    """True if ``role`` may create or run a scan. Says nothing about
    membership *status* -- an ``INVITED``/``REMOVED`` member is rejected
    earlier, by ``require_organization_member``, before any role is
    consulted."""
    return role in SCAN_WRITE_ROLES
