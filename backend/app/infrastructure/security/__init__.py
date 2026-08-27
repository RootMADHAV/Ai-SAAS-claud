"""Shared SSRF/target-validation guard used by every scanner adapter,
plus password-hashing and JWT/refresh-token primitives used by the
Identity & Access authentication use cases.

Milestone 3: ``target_validation.py`` (``validate_target``,
``ValidatedTarget``, ``TargetValidationError``).
``password_hashing.py`` (``hash_password``,
``verify_password``); ``token_service.py`` (``create_access_token``,
``decode_access_token``, ``generate_refresh_token``,
``hash_refresh_token``).
"""
