"""Repository port for the Scanning bounded context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.scanning.entities import Scan, ScanScope, ScanWorkflowStep


class ScanRepositoryPort(ABC):
    """Persistence for the ``Scan`` aggregate (its ``ScanWorkflowStep``
    children) and the org-level ``ScanScope`` registry. ``ScanScope`` is
    not tied to a specific scan -- it is an authorization registry the
    Scanning context owns, per PROJECT_STATE.md section 3 -- but lives on
    this port rather than a separate one since no other bounded context
    reads or writes it.
    """

    @abstractmethod
    async def get_by_id(self, scan_id: UUID) -> Scan | None: ...

    @abstractmethod
    async def add(self, scan: Scan) -> None: ...

    @abstractmethod
    async def update(self, scan: Scan) -> None: ...

    @abstractmethod
    async def add_workflow_step(self, step: ScanWorkflowStep) -> None: ...

    @abstractmethod
    async def update_workflow_step(self, step: ScanWorkflowStep) -> None: ...

    @abstractmethod
    async def list_workflow_steps(self, scan_id: UUID) -> list[ScanWorkflowStep]:
        """Returns steps ordered by ``step_order`` -- callers rely on this
        ordering to find "the first non-completed step" when resuming a
        scan after a failure, per PROJECT_STATE.md section 3's
        resumability rationale."""
        ...

    @abstractmethod
    async def add_scope(self, scope: ScanScope) -> None: ...

    @abstractmethod
    async def list_scopes(self, organization_id: UUID) -> list[ScanScope]: ...
