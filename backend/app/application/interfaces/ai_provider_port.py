"""AI provider port -- the abstraction the four named AI providers
(Anthropic, OpenAI, Ollama, OpenRouter -- PROJECT_STATE.md section 2)
sit behind. Milestone 6 builds this port and its first concrete
implementation (``AnthropicProvider``, ``app/infrastructure/
ai_providers/anthropic_provider.py``); the other three remain
unimplemented until a real second provider's shape is known, the same
"don't build it until a second real shape exists" reasoning
PROJECT_STATE.md section 3 already applies to deferring ``BaseAgent``.

Deliberately minimal and provider-agnostic, mirroring ``ScannerPort``'s
own division of responsibility: this port's job stops at "send one
prompt, return the provider's raw text response." Interpreting that text
(parsing it as JSON, validating it against the findings-analysis schema)
is ``AnalysisService``'s job (``app/ai_agents/analysis_service.py``), not
this port's -- the same boundary ``normalization.py`` draws relative to
``ScannerPort``/``ScanOutput`` (a port returns raw output; an
application-layer or outer-layer consumer interprets it). Keeping that
interpretation out of this port is also what keeps ``AIProviderPort``
implementable by every named provider without each one needing to agree
on a findings-specific response shape -- a provider adapter only ever
has to satisfy "return text for a prompt," a capability every one of the
four already has.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AICompletionResult:
    """One provider call's raw output, before ``AnalysisService``
    validates it into a ``FindingAnalysisResult``. Mirrors ``ScanOutput``
    's own shape (raw payload plus minimal typed metadata, no
    interpretation)."""

    text: str
    model: str


class AIProviderError(RuntimeError):
    """Raised by a concrete provider adapter on any failure reaching or
    getting a response from the underlying API (auth, network, rate
    limit, timeout). Callers depend on this port-level type, never a
    provider SDK's own exception classes -- the dependency rule
    (PROJECT_STATE.md section 10) applies to exceptions crossing a port
    boundary exactly as it does to the types those methods take and
    return."""


class AIProviderPort(ABC):
    """One provider's capability to turn a prompt into a text
    completion."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable identifier, e.g. ``"anthropic"`` -- matches
        ``Settings.ai_default_provider`` and is recorded in
        ``FindingAnalysis.model_metadata`` for provenance."""
        ...

    @abstractmethod
    async def complete(self, *, system_prompt: str, user_prompt: str) -> AICompletionResult:
        """Send one request to the underlying provider and return its
        text response.

        Raises ``AIProviderError`` if the provider cannot be reached or
        returns an error.
        """
        ...
