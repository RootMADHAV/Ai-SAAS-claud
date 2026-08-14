"""``AnalysisService`` -- the concrete AI Platform capability
PROJECT_STATE.md section 3 already named as planned: "One concrete
``AnalysisService`` exists with no formal interface; extract
``BaseAgent`` when a second agent's real shape is known, not before."
This is that service, and this milestone still does not add a
``BaseAgent`` -- YAGNI holds until a second agent genuinely exists.

Architecturally, ``ai_agents/`` sits at the same outer layer as
``scanner_engine/`` (PROJECT_STATE.md section 1: "``infrastructure``,
``scanner_engine``, and ``ai_agents`` are outer-layer adapters
implementing those ports"). ``AnalysisService`` is this bounded
context's own capability surface, deliberately shaped so it never has to
import anything from the Scanning or Findings bounded contexts --
``FindingAnalysisInput``/``FindingAnalysisResult`` below are AI
Platform's *own* input/output contract, not ``NormalizedFinding``
(Scanning) or ``Finding``/``FindingAnalysis`` (Findings & Analysis).
``RunScanWorkflowUseCase`` (application layer, already legitimately
depending on Scanning, Findings, and now AI Platform types, the same way
it already coordinates Scanning and Findings in ``_persist``) is what
translates between them -- matching PROJECT_STATE.md section 1's own
framing of AI Platform as "a capability, not a domain" that "reads
Findings data via injected context" rather than reaching into another
context's data itself.

The one non-``domain.shared`` type this module does import is
``SeverityLevel`` (from ``app.domain.shared.enums``, the deliberate
staging area PROJECT_STATE.md section 3 already documents as shared
vocabulary pending each bounded context "owning" its own slice) -- used
to schema-validate the provider's severity string into a real, bounded
value rather than accepting an arbitrary string, per PROJECT_STATE.md
section 11's rule: "AI outputs: schema-validate the response ... never
assert exact text match against an LLM's output." Importing this one
shared enum is not a dependency on the Findings bounded context's own
entities/value objects (``domain.findings.*``), which this module never
imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.application.interfaces.ai_provider_port import AICompletionResult, AIProviderPort
from app.domain.shared.enums import SeverityLevel

# Bumped whenever the prompt this service sends changes in a way that
# would make an old FindingAnalysis row's output incomparable to a new
# one -- FindingAnalysis.prompt_version exists specifically so a future
# reader can tell "what changed" apart from "the finding changed"
# (PROJECT_STATE.md's finding_analyses design intent).
PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = (
    "You are a security-analysis assistant reviewing one vulnerability "
    "finding detected by an automated scanner. Respond with a single "
    "JSON object and nothing else -- no prose, no markdown code fences -- "
    'containing exactly these three keys: "summary" (a concise, '
    "plain-language explanation of the vulnerability and its risk, 2-4 "
    'sentences), "severity" (your own severity estimate, exactly one of: '
    'info, low, medium, high, critical), and "remediation" (concrete, '
    "actionable steps to fix or mitigate this finding, 2-4 sentences)."
)


class AnalysisError(RuntimeError):
    """Raised when the provider's response cannot be validated into a
    usable ``FindingAnalysisResult`` -- malformed JSON, a missing field,
    or a severity value outside ``SeverityLevel``'s vocabulary. Never
    fabricated into a best-guess result on failure (PROJECT_STATE.md
    section 11's rule against asserting anything about an LLM's output
    that was not actually validated); the caller
    (``RunScanWorkflowUseCase._ai_analyze``) decides how to handle this,
    typically by leaving that finding's analysis absent rather than
    failing the whole scan over one provider hiccup."""


@dataclass(frozen=True, slots=True)
class FindingAnalysisInput:
    """Everything ``AnalysisService`` needs to analyze one finding-shaped
    item. Deliberately plain primitives (``float | None``/``str | None``
    for CVSS, not a ``CVSS`` value object) rather than any Findings- or
    Scanning-context type -- see module docstring."""

    title: str
    raw_severity: str
    description: str | None
    cve_ids: tuple[str, ...]
    cvss_score: float | None
    cvss_vector: str | None


@dataclass(frozen=True, slots=True)
class FindingAnalysisResult:
    """A validated analysis result, ready for
    ``RunScanWorkflowUseCase._persist`` to write onto ``Finding.
    ai_severity_level`` and into a new ``FindingAnalysis`` row."""

    ai_summary: str
    ai_severity_estimate: SeverityLevel
    remediation_advice: str
    model_metadata: dict[str, object]


class AnalysisService:
    """The one concrete AI agent this codebase has (see module
    docstring on why there is no ``BaseAgent`` yet)."""

    def __init__(self, *, provider: AIProviderPort) -> None:
        self._provider = provider

    @property
    def provider_name(self) -> str:
        """Passthrough for callers/tests that want to know which
        provider is wired in without reaching into a private attribute."""
        return self._provider.provider_name

    async def analyze(self, finding: FindingAnalysisInput) -> FindingAnalysisResult:
        """Ask the configured provider to analyze ``finding`` and return
        a schema-validated result.

        Raises ``AIProviderError`` (propagated from the provider, not
        caught here -- this service is not the layer that decides
        whether a provider failure should sink the whole scan) if the
        provider cannot be reached, and ``AnalysisError`` if it responds
        but its response cannot be validated into a usable result.
        """
        completion = await self._provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(finding),
        )
        return _parse_completion(completion, provider_name=self._provider.provider_name)


def _build_user_prompt(finding: FindingAnalysisInput) -> str:
    lines = [
        f"Title: {finding.title}",
        f"Scanner-reported severity: {finding.raw_severity}",
    ]
    if finding.description:
        lines.append(f"Description: {finding.description}")
    if finding.cve_ids:
        lines.append(f"CVE IDs: {', '.join(finding.cve_ids)}")
    if finding.cvss_score is not None:
        lines.append(f"CVSS score: {finding.cvss_score}")
    if finding.cvss_vector is not None:
        lines.append(f"CVSS vector: {finding.cvss_vector}")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    """Defensive parsing: the system prompt explicitly asks for no
    markdown fences, but LLMs occasionally wrap JSON in ```/```json
    fences regardless of instruction -- stripping one if present costs
    nothing when absent and avoids a spurious ``AnalysisError`` for an
    otherwise perfectly valid response."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_completion(
    completion: AICompletionResult, *, provider_name: str
) -> FindingAnalysisResult:
    cleaned = _strip_code_fence(completion.text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"provider response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise AnalysisError("provider response JSON was not an object")

    try:
        severity_raw = payload["severity"]
        summary = payload["summary"]
        remediation = payload["remediation"]
    except KeyError as exc:
        raise AnalysisError(f"provider response missing required field: {exc}") from exc

    if not all(isinstance(value, str) for value in (severity_raw, summary, remediation)):
        raise AnalysisError("provider response's summary/severity/remediation must be strings")

    try:
        severity = SeverityLevel(severity_raw.strip().lower())
    except ValueError as exc:
        raise AnalysisError(
            f"provider returned an unrecognized severity value: {severity_raw!r}"
        ) from exc

    return FindingAnalysisResult(
        ai_summary=summary,
        ai_severity_estimate=severity,
        remediation_advice=remediation,
        model_metadata={"provider": provider_name, "model": completion.model},
    )
