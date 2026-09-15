"""SQLMap adapter -- Phase 4's third ``ActiveScanner`` implementation.

SQLMap is invoked as an argument-list subprocess (never ``shell=True``)
via ``run_scanner_subprocess``, against a target that has already passed
``validate_target``. Unlike nmap (``-oX``) and nuclei (``-jsonl``),
SQLMap has no official machine-readable output mode for its injection-
detection results -- it has no ``-oX``/``--format=json`` equivalent; its
own structured artifacts (the per-target session SQLite database, the
per-target log file under ``--output-dir``) live on disk in a
target-named subdirectory, not on a single subprocess's stdout, and its
data-extraction formats (``--dump-format``) are for exfiltrated table
rows, not for "is this parameter vulnerable" findings. Reading files
from a scan-specific output directory after the subprocess exits would
be a new mechanism this codebase's adapters don't have (nmap/nuclei both
rely purely on captured stdout) -- not attempted here, per the
instruction not to redesign the scanner/subprocess architecture.

Given that, this adapter captures exactly sqlmap's own stdout (the same
``run_scanner_subprocess``-captured-bytes model every other adapter
uses) and tags it honestly as ``"sqlmap-stdout"`` -- captured text, not
a documented contract. No normalizer exists for this format yet (calling
``normalize_scan_output`` with it raises
``UnsupportedScanOutputFormatError``, the same state nmap's own output
was in between its adapter and normalizer sessions) -- writing one would
mean parsing sqlmap's human-readable progress/summary text well enough
to claim confidence in specific injection techniques/payloads it
detected, which is not asserted here as a reliable contract. Per
explicit instruction, preserving the existing "unsupported format"
behavior is the honest choice over fabricating parsed vulnerability data
from an undocumented text format.

Conservative scan flags, mirroring nmap's own "no privileged/
exploitative behavior" stance adapted to SQL injection testing:
``--risk=1 --level=1`` (sqlmap's own least-aggressive payload settings,
pinned explicitly rather than relying on whatever sqlmap's own defaults
happen to be) and ``--technique=BEUT`` (boolean-blind, error-based,
UNION, time-blind -- excludes ``S``, stacked queries, which can execute
arbitrary additional SQL statements including destructive DDL/DML if a
target is vulnerable, and ``Q``, a rarer technique not enabled by
default here). ``--batch`` is required, not optional -- without it
sqlmap prompts interactively at multiple points and this adapter's
subprocess provides no stdin, which would hang until
``run_scanner_subprocess``'s own timeout kills it.
"""

from __future__ import annotations

from app.application.interfaces.scanner_port import ActiveScanner, ScanOutput
from app.domain.shared.clock import utcnow
from app.infrastructure.security.target_validation import validate_target
from app.scanner_engine.base_scanner import ScannerExecutionError, run_scanner_subprocess

DEFAULT_TIMEOUT_SECONDS = 600.0


class SqlmapAdapter(ActiveScanner):
    """Wraps the ``sqlmap`` CLI binary."""

    def __init__(self, *, binary_path: str = "sqlmap", extra_args: tuple[str, ...] = ()) -> None:
        # extra_args exists for dbms/tamper-script selection etc. at the
        # call site (e.g. --dbms=mysql, --tamper=space2comment) -- not a
        # way to smuggle in shell metacharacters. run_scanner_subprocess
        # only ever accepts a list[str] and never a shell string, so
        # nothing passed here can become a shell-injection vector.
        self._binary_path = binary_path
        self._extra_args = extra_args

    @property
    def name(self) -> str:
        return "sqlmap"

    @property
    def output_format(self) -> str:
        return "sqlmap-stdout"

    async def execute(
        self, target: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> ScanOutput:
        validated = validate_target(target)

        # sqlmap's -u takes a URL, not a bare host -- validated.original
        # (the exact string the caller supplied, whether a bare host or
        # a full URL with query parameters to actually test) is used
        # as-is, never reconstructed or defaulted to a guessed scheme:
        # validate_target has already resolved and safety-checked
        # whatever host is embedded in it, regardless of shape, and this
        # adapter does not invent URL-construction behavior it cannot
        # honestly claim sqlmap itself documents.
        argv = [
            self._binary_path,
            "-u",
            validated.original,
            "--batch",
            "--risk=1",
            "--level=1",
            "--technique=BEUT",
            *self._extra_args,
        ]

        started_at = utcnow()
        try:
            result = await run_scanner_subprocess(argv, timeout_seconds=timeout_seconds)
        except FileNotFoundError as exc:
            raise ScannerExecutionError(
                f"sqlmap binary not found at {self._binary_path!r}"
            ) from exc
        completed_at = utcnow()

        # Neither nmap's "any non-zero exit is a hard failure" nor
        # nuclei's "non-zero + empty output only" rule is adopted here:
        # sqlmap's own exit-code convention for "ran cleanly, nothing
        # vulnerable found" versus "the tool itself errored" is not
        # something this adapter asserts confident, verified knowledge
        # of, unlike nmap's well-documented convention. What sqlmap
        # reliably always does, on any real invocation, clean or not, is
        # print its own banner and progress/summary text to stdout --
        # unlike nuclei's deliberately silent -jsonl mode, sqlmap has no
        # equivalent "legitimately empty on a clean run" case. Truly
        # empty stdout is therefore always treated as a failure signal,
        # regardless of exit code; non-empty stdout is trusted and
        # returned regardless of exit code, rather than risking
        # misclassifying a legitimate "no injection found" run as a
        # failure based on an exit-code convention this adapter does not
        # confidently know.
        stripped = result.stdout.strip()
        if not stripped:
            raise ScannerExecutionError(
                f"sqlmap produced no output (exit {result.returncode}); stderr: "
                f"{result.stderr.decode(errors='replace')!r}"
            )

        return ScanOutput(
            scanner_name=self.name,
            output_format=self.output_format,
            raw_bytes=result.stdout,
            started_at=started_at,
            completed_at=completed_at,
        )
