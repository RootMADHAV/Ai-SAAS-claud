"""Nmap adapter -- Phase 4's ``ActiveScanner`` implementation.

Nmap is invoked as an argument-list subprocess (never ``shell=True``) via
``run_scanner_subprocess``, against a target that has already passed
``validate_target``. Output format is nmap's own XML mode (``-oX -``,
written to stdout) -- parsing that XML into domain ``Finding``/``Asset``
objects is normalization (a future pipeline step), not this adapter's
job; per PROJECT_STATE.md section 1, Scanning does not own findings.

Scan type is pinned to ``-sT`` (TCP connect scan) with ``-Pn`` (skip host
discovery): both work fully unprivileged, unlike ``-sS``/``-sU``/``-O``/
``--traceroute``, which need raw sockets and would either silently
degrade or require root. This is the "no privileged/raw-packet scanning"
requirement made concrete in code, on top of (not instead of)
``run_scanner_subprocess``'s own non-root guard -- two independent layers
rather than relying on either alone. The target argument is always
exactly the single already-validated, already-resolved hostname/IP
``validate_target`` returns -- never a CIDR range or host list -- so no
target expansion is reachable through this adapter regardless of what a
caller passes to ``execute()``.
"""

from __future__ import annotations

from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.domain.shared.clock import utcnow
from app.infrastructure.security.target_validation import validate_target
from app.scanner_engine.base_scanner import ScannerExecutionError, run_scanner_subprocess

DEFAULT_TIMEOUT_SECONDS = 600.0

# A truncated or noise-wrapped process can still produce *some* stdout
# without producing valid nmap XML. This is a deliberately shallow sanity
# check (a substring match, not XML parsing) -- turning real output into
# structured findings is normalization's job, not this adapter's (see
# module docstring).
_XML_ROOT_MARKER = b"<nmaprun"


class NmapAdapter(ActiveScanner):
    """Wraps the ``nmap`` CLI binary."""

    def __init__(self, *, binary_path: str = "nmap", extra_args: tuple[str, ...] = ()) -> None:
        # extra_args exists for port-range/timing selection etc. at the
        # call site (e.g. -p, -T4) -- not a way to smuggle in shell
        # metacharacters or a second target. run_scanner_subprocess only
        # ever accepts a list[str] and never a shell string, and the
        # validated target is always appended last by execute() below, so
        # nothing passed here can become a shell-injection vector or
        # redefine what host actually gets scanned.
        self._binary_path = binary_path
        self._extra_args = extra_args

    @property
    def name(self) -> str:
        return "nmap"

    @property
    def output_format(self) -> str:
        return "nmap-xml"

    async def execute(
        self, target: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> ScanOutput:
        validated = validate_target(target)

        argv = [
            self._binary_path,
            "-sT",
            "-Pn",
            "-oX",
            "-",
            *self._extra_args,
            validated.hostname,
        ]

        started_at = utcnow()
        try:
            result = await run_scanner_subprocess(argv, timeout_seconds=timeout_seconds)
        except FileNotFoundError as exc:
            # run_scanner_subprocess does not itself catch this --
            # asyncio.create_subprocess_exec raises it directly when the
            # binary does not exist on PATH. Caught here, not in shared
            # base_scanner.py, since "missing binary" is reported the
            # same way as every other adapter-level failure
            # (ScannerExecutionError) rather than as a raw OSError
            # leaking out of this port.
            raise ScannerExecutionError(f"nmap binary not found at {self._binary_path!r}") from exc
        completed_at = utcnow()

        # Unlike nuclei (which can exit non-zero on benign template
        # warnings while still producing complete, usable output), nmap
        # has no equivalent "ran fine, minor warning" non-zero case --
        # any non-zero exit is treated as a hard failure outright, not
        # conditioned on whether stdout is empty.
        if result.returncode != 0:
            raise ScannerExecutionError(
                f"nmap exited {result.returncode}; stderr: "
                f"{result.stderr.decode(errors='replace')!r}"
            )

        stripped = result.stdout.strip()
        if not stripped:
            raise ScannerExecutionError("nmap produced no output")
        if _XML_ROOT_MARKER not in stripped:
            raise ScannerExecutionError(
                "nmap output does not look like nmap XML (missing "
                f"{_XML_ROOT_MARKER.decode()!r}): {stripped[:200]!r}"
            )

        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=result.stdout,
            started_at=started_at,
            completed_at=completed_at,
        )
