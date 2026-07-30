"""Unit tests for app.scanner_engine.adapters.nuclei.adapter.NucleiAdapter.

The real ``nuclei`` binary is not installed in this test environment (or
required to be, for a unit test) -- ``run_scanner_subprocess`` and
``validate_target`` are patched so these tests exercise exactly
``NucleiAdapter``'s own logic: argv construction, success/failure
classification, and ``ScanOutput`` shape. A separate, real-binary
integration test is future work once nuclei is available in a CI/sandbox
image (see docs/implementation_progress.md's Technical debt list).
"""

from __future__ import annotations

import pytest

from app.application.interfaces.scanner_port import ScanOutput
from app.infrastructure.security.target_validation import ValidatedTarget
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter
from app.scanner_engine.base_scanner import ScannerExecutionError, SubprocessResult


@pytest.fixture(autouse=True)
def _patch_target_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scanner_engine.adapters.nuclei.adapter.validate_target",
        lambda raw: ValidatedTarget(
            original=raw, hostname="example.com", resolved_ips=("93.184.216.34",)
        ),
    )


def test_name_and_output_format() -> None:
    adapter = NucleiAdapter()

    assert adapter.name == "nuclei"
    assert adapter.output_format == "nuclei-jsonl"


async def test_execute_builds_the_expected_argument_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_argv: list[str] = []

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        captured_argv.extend(argv)
        return SubprocessResult(stdout=b'{"match": true}\n', stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nuclei.adapter.run_scanner_subprocess", _fake_run
    )

    adapter = NucleiAdapter(binary_path="/usr/local/bin/nuclei", extra_args=("-severity", "high"))
    result = await adapter.execute("https://example.com", timeout_seconds=30)

    assert captured_argv == [
        "/usr/local/bin/nuclei",
        "-target",
        "example.com",
        "-jsonl",
        "-silent",
        "-severity",
        "high",
    ]
    assert isinstance(result, ScanOutput)
    assert result.scanner_name == "nuclei"
    assert result.output_format == "nuclei-jsonl"
    assert result.raw_bytes == b'{"match": true}\n'
    assert result.started_at.tzinfo is not None
    assert result.completed_at >= result.started_at


async def test_execute_treats_nonzero_exit_with_output_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(
            stdout=b'{"match": true}\n', stderr=b"template warning", returncode=1
        )

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nuclei.adapter.run_scanner_subprocess", _fake_run
    )

    result = await NucleiAdapter().execute("example.com", timeout_seconds=30)

    assert result.raw_bytes == b'{"match": true}\n'


async def test_execute_raises_on_nonzero_exit_with_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        return SubprocessResult(stdout=b"", stderr=b"binary not found", returncode=127)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nuclei.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(ScannerExecutionError, match="binary not found"):
        await NucleiAdapter().execute("example.com", timeout_seconds=30)


async def test_execute_validates_target_before_running_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.security.target_validation import TargetValidationError

    def _raise(raw: str) -> ValidatedTarget:
        raise TargetValidationError("rejected")

    monkeypatch.setattr("app.scanner_engine.adapters.nuclei.adapter.validate_target", _raise)

    called = False

    async def _fake_run(argv: list[str], *, timeout_seconds: float) -> SubprocessResult:
        nonlocal called
        called = True
        return SubprocessResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "app.scanner_engine.adapters.nuclei.adapter.run_scanner_subprocess", _fake_run
    )

    with pytest.raises(TargetValidationError):
        await NucleiAdapter().execute("127.0.0.1", timeout_seconds=30)

    assert called is False
