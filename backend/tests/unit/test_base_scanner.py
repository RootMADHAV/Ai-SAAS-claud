"""Unit tests for app.scanner_engine.base_scanner."""

from __future__ import annotations

import os
import sys

import pytest

from app.scanner_engine.base_scanner import (
    NonRootExecutionError,
    ScannerTimeoutError,
    SubprocessResult,
    run_scanner_subprocess,
)


@pytest.fixture(autouse=True)
def _pretend_non_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every real scanner-worker container runs non-root, but this
    sandbox (and many CI containers) runs as uid 0 -- pretend otherwise
    by default so tests exercise the intended "normal" path. The one test
    that specifically wants to see the guard fire overrides this back to
    uid 0 itself."""
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)


async def test_run_scanner_subprocess_returns_captured_output() -> None:
    result = await run_scanner_subprocess(
        [sys.executable, "-c", "print('hello'); import sys; print('err', file=sys.stderr)"],
        timeout_seconds=5,
    )

    assert isinstance(result, SubprocessResult)
    assert result.stdout.strip() == b"hello"
    assert result.stderr.strip() == b"err"
    assert result.returncode == 0


async def test_run_scanner_subprocess_captures_nonzero_exit_code() -> None:
    result = await run_scanner_subprocess(
        [sys.executable, "-c", "import sys; sys.exit(3)"], timeout_seconds=5
    )

    assert result.returncode == 3


async def test_run_scanner_subprocess_raises_on_empty_argv() -> None:
    with pytest.raises(ValueError, match="argv must not be empty"):
        await run_scanner_subprocess([], timeout_seconds=5)


async def test_run_scanner_subprocess_enforces_timeout() -> None:
    with pytest.raises(ScannerTimeoutError):
        await run_scanner_subprocess(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=0.2
        )


async def test_run_scanner_subprocess_refuses_to_run_as_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)

    with pytest.raises(NonRootExecutionError):
        await run_scanner_subprocess(
            [sys.executable, "-c", "print('should not run')"], timeout_seconds=5
        )


async def test_run_scanner_subprocess_is_a_noop_guard_on_platforms_without_getuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "getuid", raising=False)

    result = await run_scanner_subprocess([sys.executable, "-c", "print('ok')"], timeout_seconds=5)

    assert result.stdout.strip() == b"ok"
