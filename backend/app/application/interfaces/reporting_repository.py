"""Repository port for the Reporting bounded context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.reporting.entities import Report


class ReportRepositoryPort(ABC):
    """Persistence for the ``Report`` entity."""

    @abstractmethod
    async def get_by_id(self, report_id: UUID) -> Report | None: ...

    @abstractmethod
    async def add(self, report: Report) -> None: ...

    @abstractmethod
    async def update(self, report: Report) -> None: ...

    @abstractmethod
    async def list_by_scan(self, scan_id: UUID) -> list[Report]: ...
