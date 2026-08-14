"""Unit tests for AnalysisService.

Per PROJECT_STATE.md section 11 ("AI outputs: schema-validate the
response ... never assert exact text match against an LLM's output"),
these tests exercise schema validation against a FakeAIProviderPort
returning canned JSON text -- never a real provider call, and never an
assertion that pins down exact wording an LLM might produce.
"""

from __future__ import annotations

import pytest

from app.ai_agents.analysis_service import (
    PROMPT_VERSION,
    AnalysisError,
    AnalysisService,
    FindingAnalysisInput,
)
from app.application.interfaces.ai_provider_port import AICompletionResult, AIProviderPort
from app.domain.shared.enums import SeverityLevel


class _FakeAIProviderPort(AIProviderPort):
    def __init__(self, *, text: str, model: str = "claude-sonnet-4-5") -> None:
        self._text = text
        self._model = model
        self.calls: list[dict[str, str]] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(self, *, system_prompt: str, user_prompt: str) -> AICompletionResult:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return AICompletionResult(text=self._text, model=self._model)


def _finding_input(**overrides: object) -> FindingAnalysisInput:
    defaults: dict[str, object] = {
        "title": "Reflected XSS in search parameter",
        "raw_severity": "medium",
        "description": "The `q` parameter is reflected without encoding.",
        "cve_ids": (),
        "cvss_score": None,
        "cvss_vector": None,
    }
    defaults.update(overrides)
    return FindingAnalysisInput(**defaults)  # type: ignore[arg-type]


def test_prompt_version_is_a_non_empty_string() -> None:
    assert isinstance(PROMPT_VERSION, str)
    assert PROMPT_VERSION


@pytest.mark.asyncio
async def test_analyze_returns_validated_result_for_well_formed_json() -> None:
    provider = _FakeAIProviderPort(
        text='{"summary": "XSS risk.", "severity": "high", "remediation": "Encode output."}'
    )
    service = AnalysisService(provider=provider)

    result = await service.analyze(_finding_input())

    assert result.ai_summary == "XSS risk."
    assert result.ai_severity_estimate is SeverityLevel.HIGH
    assert result.remediation_advice == "Encode output."
    assert result.model_metadata == {"provider": "fake", "model": "claude-sonnet-4-5"}


@pytest.mark.asyncio
async def test_analyze_strips_markdown_code_fence() -> None:
    provider = _FakeAIProviderPort(
        text=('```json\n{"summary": "s", "severity": "low", "remediation": "r"}\n```')
    )
    service = AnalysisService(provider=provider)

    result = await service.analyze(_finding_input())

    assert result.ai_severity_estimate is SeverityLevel.LOW


@pytest.mark.asyncio
async def test_analyze_is_case_and_whitespace_tolerant_on_severity() -> None:
    provider = _FakeAIProviderPort(
        text='{"summary": "s", "severity": "  Critical  ", "remediation": "r"}'
    )
    service = AnalysisService(provider=provider)

    result = await service.analyze(_finding_input())

    assert result.ai_severity_estimate is SeverityLevel.CRITICAL


@pytest.mark.asyncio
async def test_analyze_raises_on_malformed_json() -> None:
    provider = _FakeAIProviderPort(text="not json at all")
    service = AnalysisService(provider=provider)

    with pytest.raises(AnalysisError, match="not valid JSON"):
        await service.analyze(_finding_input())


@pytest.mark.asyncio
async def test_analyze_raises_when_response_is_a_json_array_not_object() -> None:
    provider = _FakeAIProviderPort(text='["summary", "severity", "remediation"]')
    service = AnalysisService(provider=provider)

    with pytest.raises(AnalysisError, match="not an object"):
        await service.analyze(_finding_input())


@pytest.mark.asyncio
async def test_analyze_raises_on_missing_field() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "low"}')
    service = AnalysisService(provider=provider)

    with pytest.raises(AnalysisError, match="missing required field"):
        await service.analyze(_finding_input())


@pytest.mark.asyncio
async def test_analyze_raises_on_wrong_field_type() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": 5, "remediation": "r"}')
    service = AnalysisService(provider=provider)

    with pytest.raises(AnalysisError, match="must be strings"):
        await service.analyze(_finding_input())


@pytest.mark.asyncio
async def test_analyze_raises_on_severity_outside_the_modeled_vocabulary() -> None:
    provider = _FakeAIProviderPort(
        text='{"summary": "s", "severity": "catastrophic", "remediation": "r"}'
    )
    service = AnalysisService(provider=provider)

    with pytest.raises(AnalysisError, match="unrecognized severity value"):
        await service.analyze(_finding_input())


@pytest.mark.asyncio
async def test_analyze_never_asserts_exact_text_match_only_schema() -> None:
    """Regression guard for PROJECT_STATE.md section 11's rule: two
    different, equally-valid summaries must both validate successfully
    -- this service must never compare against one fixed expected
    string."""
    for summary in (
        "Short summary.",
        "A much longer, differently-worded summary of the same risk.",
    ):
        provider = _FakeAIProviderPort(
            text=f'{{"summary": {summary!r}, "severity": "info", "remediation": "r"}}'.replace(
                "'", '"'
            )
        )
        service = AnalysisService(provider=provider)
        result = await service.analyze(_finding_input())
        assert result.ai_summary == summary


@pytest.mark.asyncio
async def test_user_prompt_includes_cvss_and_cve_data_when_present() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    service = AnalysisService(provider=provider)

    await service.analyze(
        _finding_input(
            cve_ids=("CVE-2024-12345",),
            cvss_score=7.5,
            cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
        )
    )

    sent = provider.calls[0]["user_prompt"]
    assert "CVE-2024-12345" in sent
    assert "7.5" in sent
    assert "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N" in sent


@pytest.mark.asyncio
async def test_user_prompt_omits_cvss_and_cve_lines_when_absent() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "low", "remediation": "r"}')
    service = AnalysisService(provider=provider)

    await service.analyze(_finding_input(cve_ids=(), cvss_score=None, cvss_vector=None))

    sent = provider.calls[0]["user_prompt"]
    assert "CVE" not in sent
    assert "CVSS" not in sent


@pytest.mark.asyncio
async def test_provider_name_passthrough() -> None:
    provider = _FakeAIProviderPort(text="{}")
    service = AnalysisService(provider=provider)
    assert service.provider_name == "fake"
