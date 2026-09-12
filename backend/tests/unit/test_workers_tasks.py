"""Unit test for ``app.workers.tasks._select_active_scanner`` -- the one
piece of Phase 4's wiring change that is a pure function with no
external dependency (no Postgres, no Celery, no real adapter I/O), so it
is tested directly here rather than only indirectly through
``tests/integration/test_scan_worker_task.py``'s real-Postgres,
real-composition-root tests.

Everything else in ``app/workers/tasks.py`` (``execute_scan_workflow``,
``_run_scan_workflow_from_settings``, ``run_scan_workflow_task``) stays
covered exactly where it always was -- see that integration module's own
docstring for why those specific functions need a real database/
composition root to verify meaningfully, the same judgment call this
module makes in the other direction for a function that needs neither.
"""

from __future__ import annotations

import pytest

from app.application.scanning.run_scan_workflow import ScannerMismatchError
from app.scanner_engine.adapters.nmap.adapter import NmapAdapter
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter
from app.workers.tasks import _select_active_scanner


def test_select_active_scanner_returns_a_nuclei_adapter_for_nuclei() -> None:
    scanner = _select_active_scanner("nuclei")

    assert isinstance(scanner, NucleiAdapter)
    assert scanner.name == "nuclei"


def test_select_active_scanner_returns_an_nmap_adapter_for_nmap() -> None:
    scanner = _select_active_scanner("nmap")

    assert isinstance(scanner, NmapAdapter)
    assert scanner.name == "nmap"


def test_select_active_scanner_raises_for_an_unwired_name() -> None:
    """Same category of error ``RunScanWorkflowUseCase`` itself already
    raises for "this deployment has no adapter for this scanner_name" --
    reused here rather than a new exception type (see the helper's own
    docstring)."""
    with pytest.raises(ScannerMismatchError, match="no adapter wired"):
        _select_active_scanner("not-a-real-scanner")
