"""Unit tests for app.scanner_engine.adapters.sqlmap.adapter.SqlmapAdapter.

The real ``sqlmap`` binary is not installed in this test environment (or
required to be, for a unit test) -- ``run_scanner_subprocess`` and
``validate_target`` are patched so these tests exercise exactly
``SqlmapAdapter``'s own logic: argv construction (conservative flag
defaults, target-as-URL passthrough), and its own
missing-binary/timeout/empty-output classification -- deliberately
different from both nmap's and nuclei's exit-code rules; see the
adapter's own comment for why. A separate, real-binary integration test
is future work once sqlmap is available in a CI/sandbox image (matching
the nmap/nuclei adapters' own documented technical debt).
"""

from __future__ import annotations

import pytest

from app.application.interfaces.scanner_port import ScanOutput
from app.infrastructure.security.target_validation import ValidatedTarget
from app.scanner_engine.adapters.sqlmap.adapter import SqlmapAdapter
from app.scanner_engine.base_scanner import (
    ScannerExecutionError,
    ScannerTimeoutError,
    SubprocessResult,
)

# A short, plausible stand-in for sqlmap's own stdout -- this adapter
# never parses it (see module docstring: no normalizer exists for
# "sqlmap-stdout" yet), so these tests only need *some* non-empty bytes,
# not a byte-perfect reproduction of real sqlmap output.
_SAMPLE_OUTPUT = (
    b"sqlmap identified the following injection point(s) "
    b"with a total of 12 HTTP(s) requests:\n"
    b"---\nParameter: id (GET)\n    Type: boolean-based blind\n---\n"
)


@pytest.fixture(autouse=True)
def _patch_target_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.validate_target",
        lambda raw: ValidatedTarget(
            original=raw, hostname="example.com", resolved_ips=("93.184.216.34",)
        ),
    )


def test_name_and_output_format() -> None:
    adapter = SqlmapAdapter()

    assert adapter.name == "sqlmap"
    assert adapter.output_format == "sqlmap-stdout"


async def test_execute_builds_the_expected_argument_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_argv: list[str] = []

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        captured_argv.extend(argv)
        return SubprocessResult(stdout=_SAMPLE_OUTPUT, stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    adapter = SqlmapAdapter(binary_path="/usr/local/bin/sqlmap", extra_args=("--dbms", "mysql"))
    result = await adapter.execute("https://example.com/page?id=1", timeout_seconds=30)

    # -u receives the *original* target string verbatim (a full URL with
    # its query parameter, not just the bare validated hostname) -- see
    # the adapter's own comment on why. --risk=1/--level=1/--technique=BEUT
    # are pinned explicitly, never left to sqlmap's own defaults, and
    # --batch is always present (required for non-interactive use).
    assert captured_argv == [
        "/usr/local/bin/sqlmap",
        "-u",
        "https://example.com/page?id=1",
        "--batch",
        "--risk=1",
        "--level=1",
        "--technique=BEUT",
        "--dbms",
        "mysql",
    ]
    assert isinstance(result, ScanOutput)
    assert result.scanner_name == "sqlmap"
    assert result.output_format == "sqlmap-stdout"
    assert result.raw_bytes == _SAMPLE_OUTPUT
    assert result.started_at.tzinfo is not None
    assert result.completed_at >= result.started_at


async def test_execute_defaults_to_no_extra_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_argv: list[str] = []

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        captured_argv.extend(argv)
        return SubprocessResult(stdout=_SAMPLE_OUTPUT, stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    await SqlmapAdapter().execute("example.com", timeout_seconds=30)

    assert captured_argv == [
        "sqlmap",
        "-u",
        "example.com",
        "--batch",
        "--risk=1",
        "--level=1",
        "--technique=BEUT",
    ]


async def test_execute_raises_cleanly_when_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        raise FileNotFoundError("no such file or directory: 'sqlmap'")

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="sqlmap binary not found"):
        await SqlmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_propagates_timeout_uncaught(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        raise ScannerTimeoutError("exceeded timeout")

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerTimeoutError):
        await SqlmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_returns_output_despite_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike NmapAdapter, a non-zero exit alone is not treated as a
    hard failure here -- sqlmap's own exit-code convention isn't
    confidently known, so real output present is trusted regardless of
    exit code (see the adapter's own comment)."""

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=_SAMPLE_OUTPUT, stderr=b"", returncode=1)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    result = await SqlmapAdapter().execute("example.com", timeout_seconds=30)

    assert result.raw_bytes == _SAMPLE_OUTPUT


async def test_execute_raises_on_empty_output_with_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike NucleiAdapter, a clean (zero) exit with empty output is
    still treated as a failure -- sqlmap always prints its own banner/
    status text on any real run, so true silence is inherently
    suspicious rather than a legitimate "nothing found" outcome (see the
    adapter's own comment contrasting this with nuclei's deliberately
    silent mode)."""

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="no output"):
        await SqlmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_raises_on_empty_output_with_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=b"", stderr=b"connection refused", returncode=1)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="connection refused"):
        await SqlmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_validates_target_before_running_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.security.target_validation import TargetValidationError

    def _raise(raw: str) -> ValidatedTarget:
        raise TargetValidationError("rejected")

    monkeypatch.setattr("app.scanner_engine.adapters.sqlmap.adapter.validate_target", _raise)

    called = False

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        nonlocal called
        called = True
        return SubprocessResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.sqlmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(TargetValidationError):
        await SqlmapAdapter().execute("127.0.0.1", timeout_seconds=30)

    assert called is False
