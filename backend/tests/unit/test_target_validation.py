"""Unit tests for app.infrastructure.security.target_validation."""

from __future__ import annotations

import socket

import pytest

from app.infrastructure.security.target_validation import (
    TargetValidationError,
    ValidatedTarget,
    validate_target,
)


def test_validate_target_accepts_a_public_ip_literal() -> None:
    result = validate_target("8.8.8.8")

    assert result == ValidatedTarget(
        original="8.8.8.8", hostname="8.8.8.8", resolved_ips=("8.8.8.8",)
    )


def test_validate_target_accepts_a_host_port_ip_literal() -> None:
    result = validate_target("8.8.8.8:443")

    assert result.hostname == "8.8.8.8"
    assert result.resolved_ips == ("8.8.8.8",)


def test_validate_target_accepts_a_url_and_extracts_the_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.infrastructure.security.target_validation._resolve",
        lambda hostname: ["93.184.216.34"],
    )

    result = validate_target("https://example.com/path?query=1")

    assert result.hostname == "example.com"
    assert result.resolved_ips == ("93.184.216.34",)


def test_validate_target_accepts_a_bare_hostname_via_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.security.target_validation._resolve",
        lambda hostname: ["93.184.216.34"],
    )

    result = validate_target("example.com")

    assert result.hostname == "example.com"
    assert result.resolved_ips == ("93.184.216.34",)


@pytest.mark.parametrize("empty", ["", "   "])
def test_validate_target_rejects_empty_input(empty: str) -> None:
    with pytest.raises(TargetValidationError, match="must not be empty"):
        validate_target(empty)


@pytest.mark.parametrize(
    "target",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_validate_target_rejects_disallowed_addresses(target: str) -> None:
    with pytest.raises(TargetValidationError):
        validate_target(target)


def test_validate_target_rejects_cloud_metadata_address_with_specific_message() -> None:
    with pytest.raises(TargetValidationError, match="cloud metadata address"):
        validate_target("169.254.169.254")


def test_validate_target_rejects_when_any_resolved_ip_is_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulates DNS rebinding / a round-robin hostname where only one of
    # several resolved addresses is unsafe -- every address must pass, not
    # just the first one checked.
    monkeypatch.setattr(
        "app.infrastructure.security.target_validation._resolve",
        lambda hostname: ["93.184.216.34", "127.0.0.1"],
    )

    with pytest.raises(TargetValidationError):
        validate_target("attacker.example.com")


def test_validate_target_raises_on_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_gaierror(hostname: str, port: object) -> None:
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)

    with pytest.raises(TargetValidationError, match="DNS resolution failed"):
        validate_target("this-domain-does-not-exist.invalid")


def test_validate_target_raises_when_resolution_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.infrastructure.security.target_validation._resolve", lambda hostname: []
    )

    with pytest.raises(TargetValidationError, match="does not resolve"):
        validate_target("example.com")


def test_validate_target_rejects_a_url_with_no_extractable_hostname() -> None:
    with pytest.raises(TargetValidationError, match="could not extract a hostname"):
        validate_target("file:///etc/passwd")


def test_validated_target_is_frozen() -> None:
    target = ValidatedTarget(original="8.8.8.8", hostname="8.8.8.8", resolved_ips=("8.8.8.8",))

    with pytest.raises(AttributeError):
        target.hostname = "changed"  # type: ignore[misc]
