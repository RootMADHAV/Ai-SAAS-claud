"""Unit tests for app.api.v1.schemas.

Pure Pydantic validation -- no app, no database, no HTTP. The
``scanner_name`` Literal constraint and the ``target`` length bound are
this schema module's only real logic (see its module docstring for why
both are deliberately thin); everything else is a straight field-mapping
``from_domain`` classmethod, covered indirectly by the integration tests
in tests/integration/test_api_scans.py, which exercise it against real
domain objects end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.v1.schemas import ScanCreateRequest, ScanDetailResponse, WorkflowStepResponse
from app.domain.scanning.entities import Scan, ScanWorkflowStep
from app.domain.shared.enums import ScanStatus, WorkflowStepName, WorkflowStepStatus
from app.domain.shared.ids import new_id


def test_scan_create_request_accepts_the_only_wired_scanner() -> None:
    request = ScanCreateRequest(target="example.com", scanner_name="nuclei")
    assert request.scanner_name == "nuclei"


def test_scan_create_request_defaults_scanner_name_to_nuclei() -> None:
    request = ScanCreateRequest(target="example.com")
    assert request.scanner_name == "nuclei"


def test_scan_create_request_rejects_unknown_scanner_name() -> None:
    """The whole point of the Literal constraint (see the module
    docstring): a scanner this process has no adapter for should fail
    request validation (422), not create a Scan row that
    RunScanWorkflowUseCase could never execute."""
    with pytest.raises(ValidationError):
        ScanCreateRequest(target="example.com", scanner_name="nmap")  # type: ignore[arg-type]


def test_scan_create_request_rejects_empty_target() -> None:
    with pytest.raises(ValidationError):
        ScanCreateRequest(target="")


def test_scan_create_request_rejects_target_longer_than_a_dns_name() -> None:
    with pytest.raises(ValidationError):
        ScanCreateRequest(target="a" * 254)


def test_scan_create_request_accepts_a_target_at_the_dns_length_limit() -> None:
    request = ScanCreateRequest(target="a" * 253)
    assert len(request.target) == 253


def _make_scan(**overrides: object) -> Scan:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": new_id(),
        "target": "example.com",
        "scanner_name": "nuclei",
        "status": ScanStatus.QUEUED,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Scan(**defaults)  # type: ignore[arg-type]


def _make_step(**overrides: object) -> ScanWorkflowStep:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "id": new_id(),
        "organization_id": new_id(),
        "scan_id": new_id(),
        "step_name": WorkflowStepName.VALIDATE_TARGET,
        "step_order": 0,
        "status": WorkflowStepStatus.PENDING,
        "retry_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return ScanWorkflowStep(**defaults)  # type: ignore[arg-type]


def test_workflow_step_response_from_domain_maps_every_field() -> None:
    step = _make_step(status=WorkflowStepStatus.FAILED, retry_count=2, error_message="boom")

    response = WorkflowStepResponse.from_domain(step)

    assert response.id == step.id
    assert response.step_name is WorkflowStepName.VALIDATE_TARGET
    assert response.status is WorkflowStepStatus.FAILED
    assert response.retry_count == 2
    assert response.error_message == "boom"


def test_scan_detail_response_from_domain_nests_ordered_steps() -> None:
    scan = _make_scan(status=ScanStatus.RUNNING)
    steps = [
        _make_step(scan_id=scan.id, step_order=0, step_name=WorkflowStepName.VALIDATE_TARGET),
        _make_step(scan_id=scan.id, step_order=1, step_name=WorkflowStepName.EXECUTE_SCANNER),
    ]

    response = ScanDetailResponse.from_domain(scan, steps)

    assert response.id == scan.id
    assert response.status is ScanStatus.RUNNING
    assert [s.step_name for s in response.workflow_steps] == [
        WorkflowStepName.VALIDATE_TARGET,
        WorkflowStepName.EXECUTE_SCANNER,
    ]


def test_scan_detail_response_is_frozen() -> None:
    scan = _make_scan()
    response = ScanDetailResponse.from_domain(scan, [])

    with pytest.raises(ValidationError):
        response.target = "changed"
