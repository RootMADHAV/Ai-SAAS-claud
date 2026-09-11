"""Unit tests for app.scanner_engine.adapters.nmap.adapter.NmapAdapter.

The real ``nmap`` binary is not installed in this test environment (or
required to be, for a unit test) -- ``run_scanner_subprocess`` and
``validate_target`` are patched so these tests exercise exactly
``NmapAdapter``'s own logic: argv construction (scan-type/target
safety), and its missing-binary/timeout/non-zero-exit/malformed-output/
empty-output classification. A separate, real-binary integration test is
future work once nmap is available in a CI/sandbox image (matching the
nuclei adapter's own documented technical debt).
"""

from __future__ import annotations

import pytest

from app.application.interfaces.scanner_port import ScanOutput
from app.infrastructure.security.target_validation import ValidatedTarget
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
from app.scanner_engine.base_scanner import (
    ScannerExecutionError,
    ScannerTimeoutError,
    SubprocessResult,
)

_SAMPLE_XML = (
    b'<?xml version="1.0"?>\n'
    b'<nmaprun scanner="nmap"><host><status state="up"/>'
    b'<address addr="93.184.216.34" addrtype="ipv4"/></host></nmaprun>\n'
)


@pytest.fixture(autouse=True)
def _patch_target_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.validate_target",
        lambda raw: ValidatedTarget(
            original=raw, hostname="example.com", resolved_ips=("93.184.216.34",)
        ),
    )


def test_name_and_output_format() -> None:
    adapter = NmapAdapter()

    assert adapter.name == "nmap"
    assert adapter.output_format == "nmap-xml"


async def test_execute_builds_the_expected_argument_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_argv: list[str] = []

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        captured_argv.extend(argv)
        return SubprocessResult(stdout=_SAMPLE_XML, stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    adapter = NmapAdapter(binary_path="/usr/local/bin/nmap", extra_args=("-p", "80,443"))
    result = await adapter.execute("https://example.com", timeout_seconds=30)

    # -sT/-Pn only (no -sS/-sU/-O/raw-socket flags), and the target is
    # always the single validated hostname appended last -- never a
    # range, never something extra_args could redefine.
    assert captured_argv == [
        "/usr/local/bin/nmap",
        "-sT",
        "-Pn",
        "-oX",
        "-",
        "-p",
        "80,443",
        "example.com",
    ]
    assert isinstance(result, ScanOutput)
    assert result.scanner_name == "nmap"
    assert result.output_format == "nmap-xml"
    assert result.raw_bytes == _SAMPLE_XML
    assert result.started_at.tzinfo is not None
    assert result.completed_at >= result.started_at


async def test_execute_defaults_to_no_extra_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_argv: list[str] = []

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        captured_argv.extend(argv)
        return SubprocessResult(stdout=_SAMPLE_XML, stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    await NmapAdapter().execute("example.com", timeout_seconds=30)

    assert captured_argv == ["nmap", "-sT", "-Pn", "-oX", "-", "example.com"]


async def test_execute_raises_cleanly_when_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        raise FileNotFoundError("no such file or directory: 'nmap'")

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="nmap binary not found"):
        await NmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_propagates_timeout_uncaught(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        raise ScannerTimeoutError("exceeded timeout")

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerTimeoutError):
        await NmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_raises_on_any_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=_SAMPLE_XML, stderr=b"permission denied", returncode=1)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    # Unlike nuclei, nmap non-zero exit is always a hard failure, even
    # with output present -- see the adapter's own comment on why this
    # differs from NucleiAdapter's classification.
    with pytest.raises(ScannerExecutionError, match="permission denied"):
        await NmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_raises_on_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="no output"):
        await NmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_raises_on_malformed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=b"not xml at all", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="does not look like nmap XML"):
        await NmapAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_validates_target_before_running_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.security.target_validation import TargetValidationError

    def _raise(raw: str) -> ValidatedTarget:
        raise TargetValidationError("rejected")

    monkeypatch.setattr("app.scanner_engine.adapters.nmap.adapter.validate_target", _raise)

    called = False

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        nonlocal called
        called = True
        return SubprocessResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nmap.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(TargetValidationError):
        await NmapAdapter().execute("127.0.0.1", timeout_seconds=30)

    assert called is False
