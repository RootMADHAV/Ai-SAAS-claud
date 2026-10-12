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

Phase 6 Milestone 2 adds the member-management rules (add / change role
/ remove a member) below the scan policy, as separate pure functions per
operation -- the scan policy above is unchanged. Unlike the scan rules,
these depend on *who the target is*, so they take the actor's role, the
target's current role, and/or the requested new role:

    ============  =======================  ==============================
    actor         add (new role)           change role / remove (target)
    ============  =======================  ==============================
    OWNER         any                      any (subject to last-owner)
    ADMIN         MEMBER or VIEWER only    target MEMBER/VIEWER, new role
                                           MEMBER/VIEWER only
    MEMBER        no                       no
    VIEWER        no                       no
    ============  =======================  ==============================

An ADMIN therefore can never grant ADMIN/OWNER and never touch an ADMIN or
OWNER (itself included). The "never leave an organization without an
ACTIVE OWNER" invariant is deliberately NOT here: it needs the current
owner count from the database, so it lives in the application layer
(``manage_members.py``) behind a transaction-level lock.

Not built, by design: a general permission/capability framework. Each
role-gated action gets its own named constant/function here when a real
route needs it -- not a speculative permission registry.
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


#: Roles that may use the member-management routes at all. ADMIN's
#: authority is further limited by the per-operation rules below.
MEMBER_MANAGER_ROLES: frozenset[OrganizationRole] = frozenset(
    {OrganizationRole.OWNER, OrganizationRole.ADMIN}
)

#: The only roles an ADMIN may grant, or modify on another member.
ADMIN_MANAGEABLE_ROLES: frozenset[OrganizationRole] = frozenset(
    {OrganizationRole.MEMBER, OrganizationRole.VIEWER}
)


def can_manage_members(role: OrganizationRole) -> bool:
    """True if ``role`` may use any member-management mutation at all
    (a necessary, not sufficient, condition -- see the per-operation
    functions). MEMBER and VIEWER never may."""
    return role in MEMBER_MANAGER_ROLES


def _may_act_on(actor_role: OrganizationRole, *roles: OrganizationRole) -> bool:
    """OWNER may act on anything; ADMIN only when *every* role involved
    (target's current role and/or the requested new role) is one an ADMIN
    manages; everyone else never."""
    if actor_role is OrganizationRole.OWNER:
        return True
    if actor_role is OrganizationRole.ADMIN:
        return all(role in ADMIN_MANAGEABLE_ROLES for role in roles)
    return False


def can_add_member(actor_role: OrganizationRole, new_role: OrganizationRole) -> bool:
    """May ``actor_role`` add (or reactivate) a member with ``new_role``?
    Only the requested role matters: a reactivated membership's stale
    previous role is irrelevant, because the result is whatever the actor
    explicitly grants now."""
    return _may_act_on(actor_role, new_role)


def can_change_member_role(
    actor_role: OrganizationRole,
    target_current_role: OrganizationRole,
    new_role: OrganizationRole,
) -> bool:
    """May ``actor_role`` change a member from ``target_current_role`` to
    ``new_role``? Both ends are checked, so an ADMIN can neither modify an
    ADMIN/OWNER nor promote anyone to ADMIN/OWNER."""
    return _may_act_on(actor_role, target_current_role, new_role)


def can_remove_member(actor_role: OrganizationRole, target_current_role: OrganizationRole) -> bool:
    """May ``actor_role`` remove a member currently holding
    ``target_current_role``?"""
    return _may_act_on(actor_role, target_current_role)
