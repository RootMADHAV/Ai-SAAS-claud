"""Unit tests for the small provider functions in
``app/api/dependencies.py`` whose own bodies are never exercised by the
integration suite: ``tests/integration/test_api_scans.py`` overrides
``get_session_factory``/``get_active_scanner``/``get_scan_dispatcher``
wholesale via ``app.dependency_overrides`` (by design -- see that
module's docstring), which means their *real* implementations are
otherwise never called by any test, only their *replacements*.

This gap is distinct from -- and should not be conflated with -- the
separate, previously-documented ``coverage``/SQLAlchemy-async-greenlet
measurement artifact (PROJECT_STATE.md/docs/session_state.md, Milestone
5) that affects lines *inside* an awaited async-session call
(``get_org_session``'s own body, and every route handler's). That gap is
a tool limitation on code that genuinely does run; this one is code that
genuinely does not run under the existing test suite's override pattern.
Closed directly here, the same way ``tests/integration/
test_main_lifespan.py`` was added in Milestone 5 to close an analogous
gap for ``_lifespan`` itself, rather than left unexamined.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    AppState,
    get_active_scanner,
    get_scan_dispatcher,
    get_session_factory,
)
from app.scanner_engine.adapters.nuclei.adapter import NucleiAdapter


def _fake_request(wired: AppState) -> Mock:
    request = Mock()
    request.app.state.wired = wired
    return request


def test_get_session_factory_reads_it_off_app_state() -> None:
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker()
    wired = AppState(session_factory=session_factory, active_scanner=NucleiAdapter())

    result = get_session_factory(_fake_request(wired))

    assert result is session_factory


def test_get_active_scanner_reads_it_off_app_state() -> None:
    scanner = NucleiAdapter()
    wired = AppState(session_factory=async_sessionmaker(), active_scanner=scanner)

    result = get_active_scanner(_fake_request(wired))

    assert result is scanner


def test_get_scan_dispatcher_returns_a_callable_that_enqueues_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.api.dependencies.run_scan_workflow_task.delay",
        lambda organization_id, scan_id: calls.append((organization_id, scan_id)),
    )
    organization_id, scan_id = uuid4(), uuid4()

    dispatch = get_scan_dispatcher()
    dispatch(organization_id, scan_id)

    assert calls == [(str(organization_id), str(scan_id))]
