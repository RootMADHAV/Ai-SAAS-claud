"""Repository port for the Findings & Analysis bounded context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.findings.entities import (
    Finding,
    FindingAnalysis,
    FindingOccurrence,
    FindingStatusHistory,
)


class FindingRepositoryPort(ABC):
    """Persistence for the ``Finding`` aggregate and its three append-only
    child histories (occurrences, analyses, status history)."""

    @abstractmethod
    async def get_by_id(self, finding_id: UUID) -> Finding | None: ...

    @abstractmethod
    async def get_by_fingerprint(self, organization_id: UUID, fingerprint: str) -> Finding | None:
        """Look up by the deduplication key (PROJECT_STATE.md section 3):
        ``(organization_id, fingerprint)``. A scan that re-detects an
        existing finding calls this to decide "bump last_seen_at" instead
        of inserting a duplicate row."""
        ...

    @abstractmethod
    async def add(self, finding: Finding) -> None: ...

    @abstractmethod
    async def update(self, finding: Finding) -> None: ...

    @abstractmethod
    async def add_occurrence(self, occurrence: FindingOccurrence) -> None: ...

    @abstractmethod
    async def list_occurrences(self, finding_id: UUID) -> list[FindingOccurrence]: ...

    @abstractmethod
    async def add_analysis(self, analysis: FindingAnalysis) -> None: ...

    @abstractmethod
    async def list_analyses(self, finding_id: UUID) -> list[FindingAnalysis]:
        """Returns every analysis ever recorded for this finding, oldest
        first -- ``finding_analyses`` is append-only specifically so this
        history is queryable (PROJECT_STATE.md section 3)."""
        ...

    @abstractmethod
    async def add_status_history(self, entry: FindingStatusHistory) -> None: ...

    @abstractmethod
    async def list_status_history(self, finding_id: UUID) -> list[FindingStatusHistory]: ...
