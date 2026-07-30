"""Nuclei adapter -- Milestone 3's ``ActiveScanner`` implementation.

Nuclei is invoked as an argument-list subprocess (never ``shell=True``)
via ``run_scanner_subprocess``, against a target that has already passed
``validate_target``. Output format is nuclei's own JSON-lines mode
(``-jsonl``), one match per line -- parsing those lines into domain
``Finding`` objects is normalization (Milestone 4's pipeline step), not
this adapter's job; per PROJECT_STATE.md section 1, Scanning does not own
findings.
"""

from __future__ import annotations

from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.domain.shared.clock import utcnow
from app.infrastructure.security.target_validation import validate_target
from app.scanner_engine.base_scanner import ScannerExecutionError, run_scanner_subprocess

DEFAULT_TIMEOUT_SECONDS = 600.0


class NucleiAdapter(ActiveScanner):
    """Wraps the ``nuclei`` CLI binary."""

    def __init__(self, *, binary_path: str = "nuclei", extra_args: tuple[str, ...] = ()) -> None:
        # extra_args exists for template-set selection etc. at the call
        # site (e.g. -severity, -tags) -- not a way to smuggle in shell
        # metacharacters. run_scanner_subprocess only ever accepts a
        # list[str] and never a shell string, so nothing passed here can
        # become a shell-injection vector regardless of its content.
        self._binary_path = binary_path
        self._extra_args = extra_args

    @property
    def name(self) -> str:
        return "nuclei"

    @property
    def output_format(self) -> str:
        return "nuclei-jsonl"

    async def execute(
        self, target: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> ScanOutput:
        validated = validate_target(target)

        argv = [
            self._binary_path,
            "-target",
            validated.hostname,
            "-jsonl",
            "-silent",
            *self._extra_args,
        ]

        started_at = utcnow()
        result = await run_scanner_subprocess(argv, timeout_seconds=timeout_seconds)
        completed_at = utcnow()

        # nuclei can exit non-zero for reasons unrelated to "the scan
        # failed" (e.g. template warnings), so a non-zero exit code alone
        # is not treated as failure. Only a non-zero exit *combined with*
        # empty stdout is treated as a hard failure -- "ran cleanly and
        # matched nothing" must never be misreported as an error.
        if result.returncode != 0 and not result.stdout.strip():
            raise ScannerExecutionError(
                f"nuclei exited {result.returncode} with no output; stderr: "
                f"{result.stderr.decode(errors='replace')!r}"
            )

        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=result.stdout,
            started_at=started_at,
            completed_at=completed_at,
        )
