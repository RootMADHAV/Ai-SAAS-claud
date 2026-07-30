"""Shared scanner-adapter infrastructure.

This module is where the locked "scanner execution isolation" decision
(PROJECT_STATE.md section 3) becomes code exactly once: argument-list
subprocess calls only (never shell interpolation), an enforced timeout on
every call, and a refusal to launch as root. Every ``ActiveScanner``
adapter must route its subprocess call through
:func:`run_scanner_subprocess` rather than calling
``asyncio.create_subprocess_exec``/``subprocess`` directly, so no
individual adapter can quietly reintroduce ``shell=True`` or an unbounded
wait -- the guarantee lives in one place, not in adapter-author
discipline repeated N times.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


class NonRootExecutionError(RuntimeError):
    """Raised if a scanner subprocess is about to be launched from a
    process running as root (uid 0). Fails loudly instead of quietly
    running a third-party scanner binary with root privileges -- see the
    non-root decision in PROJECT_STATE.md section 3."""


class ScannerTimeoutError(RuntimeError):
    """Raised when a scanner subprocess exceeds its enforced timeout."""


class ScannerExecutionError(RuntimeError):
    """Raised when a scanner subprocess fails in a way an adapter
    considers unrecoverable (adapter-specific; this base module does not
    raise it itself, since "what counts as failure" -- e.g. a scanner
    that exits non-zero on template errors but still produced usable
    output -- varies per tool)."""


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    stdout: bytes
    stderr: bytes
    returncode: int


def _assert_not_root() -> None:
    # os.getuid() does not exist on Windows -- guarded so this check is a
    # deliberate no-op there rather than an AttributeError. Scanner
    # adapters only ever run for real inside the Linux scanner-worker
    # container defined in docker-compose.yml; this is a defense-in-depth
    # check for that container, not something meaningful to enforce on a
    # developer's Windows machine.
    getuid = getattr(os, "getuid", None)
    if getuid is not None and getuid() == 0:
        raise NonRootExecutionError(
            "refusing to launch a scanner subprocess while running as root (uid 0)"
        )


async def run_scanner_subprocess(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
    """Run a scanner binary and capture its output.

    ``argv`` must be a full argument list, e.g.
    ``["nuclei", "-target", target, "-jsonl"]`` -- never a single shell
    string. The parameter type (``list[str]``, no ``shell`` option
    exposed) is what makes ``shell=True`` unreachable through this
    function, not a comment asking callers not to do it.
    """
    if not argv:
        raise ValueError("argv must not be empty")

    _assert_not_root()

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ScannerTimeoutError(
            f"scanner subprocess {argv[0]!r} exceeded its {timeout_seconds}s timeout"
        ) from exc

    return SubprocessResult(stdout=stdout, stderr=stderr, returncode=process.returncode or 0)
