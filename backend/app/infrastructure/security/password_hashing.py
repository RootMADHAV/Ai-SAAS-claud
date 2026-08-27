"""Password hashing -- a single-implementation infrastructure utility,
imported directly by the Identity & Access application-layer use cases
the same way ``target_validation.validate_target`` already is by
``run_scan_workflow.py`` (see that module's own comment on why: zero
framework/DB imports, exactly one real implementation, so a port with a
single implementation would be an abstraction with nothing to be
abstract over -- PROJECT_STATE.md section 3's "don't build it until a
second real shape exists" reasoning, applied here to password hashing).

Wraps the ``bcrypt`` library directly rather than ``passlib`` --
``passlib`` is effectively unmaintained against current ``bcrypt``
releases (its own compatibility shim breaks against bcrypt>=4's
stricter ``__about__`` removal), and this codebase needs exactly one
hashing scheme, not passlib's multi-scheme abstraction over one that is
never used.
"""

from __future__ import annotations

import bcrypt

# bcrypt's underlying algorithm only uses the first 72 bytes of its input
# and raises ValueError beyond that (bcrypt>=4) -- enforced explicitly
# here, with a clear message, rather than letting a library-specific
# ValueError surface uncaught at an API boundary that has no idea what
# "72 bytes" means. Registration/login request schemas
# (app/api/v1/auth_schemas.py) additionally cap password length at the
# HTTP-validation boundary so this rarely fires in practice; this check
# is defense-in-depth for any other caller of this module.
_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """Hash ``password`` for storage in ``User.hashed_password``.

    Raises ``ValueError`` if ``password`` exceeds bcrypt's 72-byte input
    limit.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {_MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(*, password: str, hashed_password: str) -> bool:
    """Constant-time comparison of ``password`` against a previously
    hashed value (``bcrypt.checkpw`` itself is constant-time)."""
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, hashed_password.encode("utf-8"))
