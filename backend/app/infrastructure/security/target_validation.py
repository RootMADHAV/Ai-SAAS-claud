"""SSRF guard for scan targets, shared by every scanner adapter.

Locked decision (PROJECT_STATE.md section 3, "Scanner execution
isolation"): code-level protections belong here and in
``app.scanner_engine.base_scanner`` (argument-list subprocess calls,
enforced timeouts, non-root execution) *in addition to* -- not instead
of -- the Docker Compose network segmentation that keeps the
scanner-worker container off the internal network. This module owns
exactly one concern: is the resolved target technically safe to send a
scanner at, before any scanner ever sees it. It rejects private,
loopback, link-local, and cloud-metadata addresses by default, and
resolves hostnames before checking -- checking the hostname string alone
is not enough, since "attacker.example.com" can resolve to 127.0.0.1
(DNS rebinding), and a scanner subprocess only ever sees an IP address
once DNS has already happened somewhere.

Per PROJECT_STATE.md section 3, target-*ownership* authorization (does
this organization actually own this target, checked against
``scan_scopes``) is deferred to a later milestone. This module answers a
narrower, always-on question: is the target technically safe to scan at
all, regardless of which organization is asking.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

# 169.254.169.254 (the cloud-metadata address on AWS/GCP/Azure) is already
# covered by link-local (169.254.0.0/16), but PROJECT_STATE.md section 3
# names it explicitly as its own bullet point, so it is named explicitly
# in the error message here too -- a reader should not have to know that
# link-local subsumes it to understand why a given target was rejected.
_CLOUD_METADATA_ADDRESS = ipaddress.ip_address("169.254.169.254")


class TargetValidationError(ValueError):
    """Raised when a scan target fails the SSRF/safety guard.

    A ``ValueError`` subclass, not a bespoke exception hierarchy root --
    this represents "this input is invalid," not a new domain concept
    that needs its own taxonomy.
    """


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    """The result of a successful validation.

    Carries every IP address the hostname actually resolved to, not just
    the original input, so a caller (an ``ActiveScanner.execute()``
    implementation, ultimately a ``scan_workflow_steps`` audit trail) can
    record what was actually checked -- important for a target that
    round-robins across multiple IPs, where "it resolved to something
    safe once" is not the same claim as "every address it could resolve
    to is safe."
    """

    original: str
    hostname: str
    resolved_ips: tuple[str, ...]


def validate_target(raw_target: str) -> ValidatedTarget:
    """Validate a scan target (a bare hostname, a bare IP, or a URL).

    Raises ``TargetValidationError`` if the target is empty, unparseable,
    fails to resolve, or resolves (directly or via DNS) to a private,
    loopback, link-local, reserved, multicast, or cloud-metadata address.
    Returns a ``ValidatedTarget`` on success.
    """
    if not raw_target or not raw_target.strip():
        raise TargetValidationError("target must not be empty")

    hostname = _extract_hostname(raw_target.strip())
    if not hostname:
        raise TargetValidationError(f"could not extract a hostname from target: {raw_target!r}")

    resolved_ips = _resolve(hostname)
    if not resolved_ips:
        raise TargetValidationError(f"target does not resolve to any address: {hostname!r}")

    for ip_str in resolved_ips:
        _reject_if_unsafe(ip_str, hostname)

    return ValidatedTarget(original=raw_target, hostname=hostname, resolved_ips=tuple(resolved_ips))


def _extract_hostname(raw_target: str) -> str:
    # A bare hostname/IP has no "://" and urlparse would put the whole
    # thing in .path rather than .netloc, so only route through urlparse
    # when the input actually looks like a URL.
    if "://" in raw_target:
        parsed = urlparse(raw_target)
        return parsed.hostname or ""
    if _looks_like_host_port(raw_target):
        return raw_target.rsplit(":", 1)[0]
    return raw_target.split("/", 1)[0]


def _looks_like_host_port(value: str) -> bool:
    # IPv6 literals contain multiple colons and are not a "host:port"
    # string -- leave them untouched rather than splitting on the wrong
    # colon and mangling the address.
    if value.count(":") != 1:
        return False
    _, _, maybe_port = value.rpartition(":")
    return maybe_port.isdigit()


def _resolve(hostname: str) -> list[str]:
    # A bare IP literal resolves to itself with no DNS lookup. Checked
    # first so the common IP-target case never pays for a real DNS
    # round-trip and never depends on getaddrinfo's platform-inconsistent
    # handling of IP-literal input.
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise TargetValidationError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    # getaddrinfo returns one tuple per (family, socktype, proto)
    # combination, so the same address commonly repeats several times;
    # de-duplicate while preserving first-seen order for a stable,
    # reproducible error message if one of them turns out unsafe.
    seen: dict[str, None] = {}
    for info in infos:
        # sockaddr is a 2-tuple for IPv4 and a 4-tuple for IPv6; typeshed
        # types this as a union across both shapes, so index [0] loses the
        # precise element type -- the address itself is always the first
        # element and is always a str, hence the explicit cast.
        address = str(info[4][0])
        seen.setdefault(address, None)
    return list(seen)


def _reject_if_unsafe(ip_str: str, hostname: str) -> None:
    ip = ipaddress.ip_address(ip_str)
    if ip == _CLOUD_METADATA_ADDRESS:
        raise TargetValidationError(
            f"target {hostname!r} resolves to the cloud metadata address {ip_str} -- rejected"
        )
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise TargetValidationError(
            f"target {hostname!r} resolves to a disallowed address {ip_str} "
            "(private/loopback/link-local/reserved/multicast) -- rejected"
        )
