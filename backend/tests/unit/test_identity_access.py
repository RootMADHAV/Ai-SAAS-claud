"""Unit tests for the role-based access policy
(``app/domain/identity/access.py``) -- Phase 6 Milestone 1.

Pure-function tests, no FastAPI, no database: the policy is domain logic
(see that module's docstring), so it is verified at the domain tier. The
HTTP translation (403) is ``test_api_dependencies.py``'s job; the
end-to-end behavior against real Postgres/RLS is
``tests/integration/test_api_scans.py``'s.
"""

from __future__ import annotations

import pytest

from app.domain.identity.access import SCAN_WRITE_ROLES, can_write_scans
from app.domain.shared.enums import OrganizationRole


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OrganizationRole.OWNER, True),
        (OrganizationRole.ADMIN, True),
        (OrganizationRole.MEMBER, True),
        (OrganizationRole.VIEWER, False),
    ],
)
def test_can_write_scans_matches_the_role_matrix(role: OrganizationRole, expected: bool) -> None:
    assert can_write_scans(role) is expected


def test_scan_write_roles_is_exactly_owner_admin_member() -> None:
    assert set(SCAN_WRITE_ROLES) == {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.MEMBER,
    }


def test_viewer_is_never_a_scan_writer() -> None:
    assert OrganizationRole.VIEWER not in SCAN_WRITE_ROLES


def test_every_organization_role_has_an_explicit_scan_write_decision() -> None:
    """If a fifth ``OrganizationRole`` is ever added, this fails until
    someone consciously decides whether it may write scans -- rather
    than the new role silently falling into the "not in
    ``SCAN_WRITE_ROLES``" (deny) bucket by omission. Deny-by-default is
    the safe failure mode, but it should be a decision, not an accident."""
    decided_writers = {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.MEMBER,
    }
    decided_read_only = {OrganizationRole.VIEWER}

    assert set(OrganizationRole) == decided_writers | decided_read_only
    assert decided_writers == set(SCAN_WRITE_ROLES)
