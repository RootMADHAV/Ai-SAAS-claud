"""Unit tests for AnalysisService.

Per PROJECT_STATE.md section 11 ("AI outputs: schema-validate the
response ... never assert exact text match against an LLM's output"),
these tests exercise schema validation against a FakeAIProviderPort
returning canned JSON text -- never a real provider call, and never an
assertion that pins down exact wording an LLM might produce.

Phase 5 Milestone 4 adds CWE-retrieval tests below the original
Milestone 6 tests (kept unchanged) -- ``EmbeddingPort``/
``VectorStorePort`` are faked directly at the port level, the same
pattern already established for ``IngestCweTop25UseCase``'s own tests
(``test_ingest_cwe_top25.py``). No real model or Qdrant server is used
anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from app.ai_agents.analysis_service import (
    PROMPT_VERSION,
    AnalysisError,
    AnalysisService,
    FindingAnalysisInput,
    _build_retrieval_query,
    _format_retrieved_context,
)
from app.application.interfaces.ai_provider_port import AICompletionResult, AIProviderPort
from app.application.interfaces.embedding_port import EmbeddingError, EmbeddingPort, EmbeddingResult
from app.application.interfaces.vector_store_port import (
    VectorMatch,
    VectorPoint,
    VectorStoreError,
    VectorStorePort,
)
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


# ---------------------------------------------------------------------------
# Phase 5 Milestone 4 -- CWE retrieval
# ---------------------------------------------------------------------------


@dataclass
class _FakeEmbeddingPort(EmbeddingPort):
    calls: list[str] = field(default_factory=list)
    vector: tuple[float, ...] = (0.1, 0.2, 0.3)
    error: Exception | None = None

    @property
    def model_name(self) -> str:
        return "fake-embedding-model"

    async def embed(self, text: str) -> EmbeddingResult:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return EmbeddingResult(vector=self.vector, model=self.model_name)


@dataclass
class _FakeVectorStorePort(VectorStorePort):
    results: list[VectorMatch] = field(default_factory=list)
    error: Exception | None = None
    search_calls: list[dict[str, object]] = field(default_factory=list)

    async def ensure_collection(self) -> None:
        pass

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        raise NotImplementedError("not used by AnalysisService")

    async def search(self, query_vector: Sequence[float], *, limit: int = 5) -> list[VectorMatch]:
        self.search_calls.append({"query_vector": list(query_vector), "limit": limit})
        if self.error is not None:
            raise self.error
        return self.results


def _match(
    cwe_id: str, score: float, *, text: str | None = None, **extra_payload: object
) -> VectorMatch:
    payload: dict[str, object] = {
        "cwe_id": cwe_id,
        "corpus": "cwe_top25",
        "corpus_version": "2025",
        "text": text if text is not None else f"CWE-{cwe_id.removeprefix('CWE-')}: Some Weakness",
    }
    payload.update(extra_payload)
    return VectorMatch(id=cwe_id, score=score, payload=payload)


# --- query construction -----------------------------------------------------


def test_build_retrieval_query_includes_title_and_description() -> None:
    finding = _finding_input(title="Reflected XSS", description="Unescaped q parameter.")
    assert _build_retrieval_query(finding) == "Reflected XSS\nUnescaped q parameter."


def test_build_retrieval_query_omits_description_when_absent() -> None:
    finding = _finding_input(title="Reflected XSS", description=None)
    assert _build_retrieval_query(finding) == "Reflected XSS"


def test_build_retrieval_query_is_deterministic() -> None:
    finding = _finding_input()
    assert _build_retrieval_query(finding) == _build_retrieval_query(finding)


# --- context formatting ------------------------------------------------------


def test_format_retrieved_context_joins_match_texts_with_separator() -> None:
    matches = [_match("CWE-79", 0.9, text="XSS text"), _match("CWE-89", 0.8, text="SQLi text")]
    assert _format_retrieved_context(matches) == "XSS text\n\n---\n\nSQLi text"


def test_format_retrieved_context_skips_matches_missing_text_payload() -> None:
    matches = [_match("CWE-79", 0.9, text="XSS text"), VectorMatch(id="x", score=0.1, payload={})]
    assert _format_retrieved_context(matches) == "XSS text"


def test_format_retrieved_context_is_empty_for_no_matches() -> None:
    assert _format_retrieved_context([]) == ""


# --- embedding invocation / Qdrant search invocation / orchestration -------


@pytest.mark.asyncio
async def test_analyze_embeds_the_retrieval_query_when_configured() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9)])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )
    finding = _finding_input(title="Reflected XSS", description="Unescaped q parameter.")

    await service.analyze(finding)

    assert embedding_port.calls == ["Reflected XSS\nUnescaped q parameter."]


@pytest.mark.asyncio
async def test_analyze_searches_the_vector_store_with_embedded_vector_and_configured_limit() -> (
    None
):
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort(vector=(0.5, 0.6, 0.7))
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9)])
    service = AnalysisService(
        provider=provider,
        embedding_port=embedding_port,
        vector_store=vector_store,
        retrieval_limit=7,
    )

    await service.analyze(_finding_input())

    assert vector_store.search_calls == [{"query_vector": [0.5, 0.6, 0.7], "limit": 7}]


@pytest.mark.asyncio
async def test_analyze_uses_the_default_retrieval_limit_when_not_overridden() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    await service.analyze(_finding_input())

    from app.ai_agents.analysis_service import DEFAULT_RETRIEVAL_LIMIT

    assert vector_store.search_calls[0]["limit"] == DEFAULT_RETRIEVAL_LIMIT


@pytest.mark.asyncio
async def test_analyze_does_not_retrieve_when_ports_are_not_configured() -> None:
    """Default AnalysisService construction (just ``provider=``) --
    every pre-Milestone-4 test above already exercises this implicitly;
    this test names the guarantee explicitly."""
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    service = AnalysisService(provider=provider)

    result = await service.analyze(_finding_input())

    assert "retrieval" not in result.model_metadata
    assert result.model_metadata == {"provider": "fake", "model": "claude-sonnet-4-5"}


# --- prompt/context injection ------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_includes_retrieved_context_clearly_labeled_as_reference_only() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(
        results=[_match("CWE-79", 0.9, text="CWE-79: Cross-Site Scripting reference text")]
    )
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    await service.analyze(_finding_input())

    sent = provider.calls[0]["user_prompt"]
    assert "CWE-79: Cross-Site Scripting reference text" in sent
    assert "not an instruction" in sent
    assert "background information only" in sent


@pytest.mark.asyncio
async def test_prompt_still_contains_all_existing_finding_fields_alongside_context() -> None:
    """Retrieved context supplements the prompt, it never replaces any
    existing finding information (this milestone's own constraint)."""
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9, text="context")])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    await service.analyze(
        _finding_input(
            title="Reflected XSS",
            cve_ids=("CVE-2024-1",),
            cvss_score=6.1,
            cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N",
        )
    )

    sent = provider.calls[0]["user_prompt"]
    assert "Title: Reflected XSS" in sent
    assert "CVE-2024-1" in sent
    assert "6.1" in sent
    assert "context" in sent


@pytest.mark.asyncio
async def test_prompt_has_no_context_section_when_no_matches_are_returned() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    await service.analyze(_finding_input())

    sent = provider.calls[0]["user_prompt"]
    assert "Reference context" not in sent


@pytest.mark.asyncio
async def test_prompt_is_unchanged_from_pre_milestone_4_shape_when_not_configured() -> None:
    """Exact regression check: with no retrieval configured, the prompt
    is byte-identical to what a pre-Milestone-4 caller would have sent."""
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    service = AnalysisService(provider=provider)
    finding = _finding_input(cve_ids=("CVE-2024-1",), cvss_score=6.1, cvss_vector="CVSS:3.1/X")

    await service.analyze(finding)

    sent = provider.calls[0]["user_prompt"]
    assert sent == (
        "Title: Reflected XSS in search parameter\n"
        "Scanner-reported severity: medium\n"
        "Description: The `q` parameter is reflected without encoding.\n"
        "CVE IDs: CVE-2024-1\n"
        "CVSS score: 6.1\n"
        "CVSS vector: CVSS:3.1/X"
    )


# --- provenance metadata ------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_metadata_identifies_retrieved_cwe_ids_scores_and_config() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.91), _match("CWE-89", 0.72)])
    service = AnalysisService(
        provider=provider,
        embedding_port=embedding_port,
        vector_store=vector_store,
        retrieval_limit=2,
    )

    result = await service.analyze(_finding_input())

    assert result.model_metadata["retrieval"] == {
        "embedding_model": "fake-embedding-model",
        "limit": 2,
        "corpus": "cwe_top25",
        "corpus_version": "2025",
        "matches": [
            {"cwe_id": "CWE-79", "score": 0.91},
            {"cwe_id": "CWE-89", "score": 0.72},
        ],
    }
    # provider/model provenance is preserved alongside retrieval, not
    # replaced by it.
    assert result.model_metadata["provider"] == "fake"
    assert result.model_metadata["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_provenance_metadata_never_includes_a_raw_vector() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort(vector=(0.123, 0.456, 0.789))
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9)])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    serialized = repr(result.model_metadata)
    assert "0.123" not in serialized
    assert "0.456" not in serialized
    assert "vector" not in result.model_metadata["retrieval"]  # type: ignore[operator]


# --- kb_version (Phase 5 Milestone 5) ----------------------------------------


@pytest.mark.asyncio
async def test_kb_version_is_populated_as_corpus_and_version_when_matches_exist() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9)])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert result.kb_version == "cwe_top25:2025"


@pytest.mark.asyncio
async def test_kb_version_is_none_when_no_matches_are_returned() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert result.kb_version is None


@pytest.mark.asyncio
async def test_kb_version_is_none_when_retrieval_is_not_configured() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    service = AnalysisService(provider=provider)

    result = await service.analyze(_finding_input())

    assert result.kb_version is None


@pytest.mark.asyncio
async def test_kb_version_is_none_when_match_payload_is_missing_corpus_fields() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    malformed_match = VectorMatch(id="x", score=0.9, payload={"cwe_id": "CWE-79", "text": "t"})
    vector_store = _FakeVectorStorePort(results=[malformed_match])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert result.kb_version is None


# --- empty retrieval results --------------------------------------------------


@pytest.mark.asyncio
async def test_empty_retrieval_results_preserve_existing_analysis_behavior() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(results=[])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert "retrieval" not in result.model_metadata
    assert "Reference context" not in provider.calls[0]["user_prompt"]
    assert result.ai_severity_estimate is SeverityLevel.HIGH  # analysis still succeeds normally


# --- embedding / vector-store failure behavior --------------------------------


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_no_context_and_analysis_still_succeeds() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort(error=EmbeddingError("model unavailable"))
    vector_store = _FakeVectorStorePort(results=[_match("CWE-79", 0.9)])
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert result.ai_severity_estimate is SeverityLevel.HIGH
    assert "retrieval" not in result.model_metadata
    assert vector_store.search_calls == []  # never reached search


@pytest.mark.asyncio
async def test_vector_store_failure_falls_back_to_no_context_and_analysis_still_succeeds() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(error=VectorStoreError("qdrant unreachable"))
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    result = await service.analyze(_finding_input())

    assert result.ai_severity_estimate is SeverityLevel.HIGH
    assert "retrieval" not in result.model_metadata
    assert "Reference context" not in provider.calls[0]["user_prompt"]


@pytest.mark.asyncio
async def test_retrieval_failure_does_not_prevent_the_provider_from_being_called() -> None:
    provider = _FakeAIProviderPort(text='{"summary": "s", "severity": "high", "remediation": "r"}')
    embedding_port = _FakeEmbeddingPort(error=EmbeddingError("boom"))
    vector_store = _FakeVectorStorePort()
    service = AnalysisService(
        provider=provider, embedding_port=embedding_port, vector_store=vector_store
    )

    await service.analyze(_finding_input())

    assert len(provider.calls) == 1
