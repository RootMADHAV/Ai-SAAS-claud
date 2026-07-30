"""Scanner adapter port: the ActiveScanner/ImportScanner split.

Locked decision (PROJECT_STATE.md sections 1 and 3): not every scanner
adapter can be invoked -- Burp/ZAP manual exports have no ``execute()``
step, only a "here is a file someone exported" step. Forcing both shapes
into one interface would mean faking an ``execute()`` on import-only
tools (raise ``NotImplementedError``, or silently no-op) instead of the
type system simply not offering that method to callers that do not need
it. Hence two ABCs sharing one common parent, not one interface with an
optional method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ScanOutput:
    """Raw output from one scanner run or import, before normalization.

    Deliberately untyped beyond bytes plus a format tag: normalization
    (Milestone 4's pipeline step) is what turns this into structured
    findings. This port's responsibility stops at "here is exactly what
    the scanner said," so it does not need to know anything about
    ``Finding``, ``Severity``, or any other Findings & Analysis concept --
    keeping the Scanning/Findings bounded-context boundary intact
    (PROJECT_STATE.md section 1: Scanning does not own findings).
    """

    scanner_name: str
    output_format: str
    raw_bytes: bytes
    started_at: datetime
    completed_at: datetime


class ScannerPort(ABC):
    """Common identity/capability surface shared by every scanner
    adapter, active or import-only."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable adapter identifier, e.g. ``"nuclei"`` -- matches
        ``scans.scanner_name`` and the capability-registry key
        (PROJECT_STATE.md section 3: the registry is code-level, adapter
        name -> capability metadata, not a database table)."""
        ...

    @property
    @abstractmethod
    def output_format(self) -> str:
        """The raw output format this adapter produces, e.g.
        ``"nuclei-jsonl"``. Consumed by the normalize pipeline step
        (Milestone 4), not interpreted by this port itself."""
        ...


class ActiveScanner(ScannerPort):
    """A scanner this codebase can actually invoke (has an
    ``execute()``)."""

    @abstractmethod
    async def execute(self, target: str, *, timeout_seconds: float) -> ScanOutput:
        """Run the scanner against ``target`` and return its raw output.

        Implementations must validate ``target`` via
        ``app.infrastructure.security.target_validation.validate_target``
        before touching a subprocess, and must invoke any subprocess via
        ``app.scanner_engine.base_scanner.run_scanner_subprocess`` --
        never directly -- so the argument-list/timeout/non-root
        guarantees (PROJECT_STATE.md section 3) apply uniformly across
        every adapter rather than depending on each adapter author
        remembering to apply them.
        """
        ...


class ImportScanner(ScannerPort):
    """A scanner whose output only ever arrives as a file someone
    exported (Burp, ZAP manual export) -- there is no "run" step for this
    codebase to invoke, per the locked ActiveScanner/ImportScanner split.
    """

    @abstractmethod
    async def parse_import(self, raw_bytes: bytes) -> ScanOutput:
        """Wrap an already-exported file as a ``ScanOutput`` for the
        normalize pipeline step. No target validation applies here --
        nothing is executed against a target, so there is no SSRF surface
        to guard."""
        ...
