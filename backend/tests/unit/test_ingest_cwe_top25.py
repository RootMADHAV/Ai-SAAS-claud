"""Unit tests for app.application.knowledge.ingest_cwe_top25.

No real CWE source file, embedding model, or Qdrant server is used
anywhere in this module. XML fixtures are built programmatically to
mirror the real vendored source's structure (namespace, element
shapes) as directly inspected in backend/data/cwe/2025_top25.xml --
see PROJECT_STATE.md's Phase 5 Milestone 3 notes -- not copied from
it. ``EmbeddingPort``/``VectorStorePort`` are faked directly at the
port level (not their adapters' underlying SDKs), matching this
module's own constructor-injection design.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.application.interfaces.embedding_port import EmbeddingError, EmbeddingPort, EmbeddingResult
from app.application.interfaces.vector_store_port import (
    VectorMatch,
    VectorPoint,
    VectorStoreError,
    VectorStorePort,
)
from app.application.knowledge.ingest_cwe_top25 import (
    CORPUS_NAME,
    CORPUS_VERSION,
    EXPECTED_ENTRY_COUNT,
    CweEntry,
    CweSourceError,
    IngestCweTop25UseCase,
    build_cwe_chunk,
    cwe_point_id,
    parse_cwe_top25_xml,
)

DEFAULT_VIEW_NAME = (
    "VIEW LIST: CWE-1435: Weaknesses in the 2025 CWE Top 25 Most Dangerous Software Weaknesses"
)


# ---------------------------------------------------------------------------
# XML fixture builders
# ---------------------------------------------------------------------------


def _weakness_xml(
    cwe_id: str,
    name: str,
    description: str = "A filler description.",
    mitigations: Sequence[tuple[str | None, str]] = (),
) -> str:
    mitigation_xml = "".join(
        (f"<Mitigation><Phase>{phase}</Phase><Description>{text}</Description></Mitigation>")
        if phase
        else f"<Mitigation><Description>{text}</Description></Mitigation>"
        for phase, text in mitigations
    )
    return (
        f'<Weakness ID="{cwe_id}" Name="{name}">'
        f"<Description>{description}</Description>"
        f"<Potential_Mitigations>{mitigation_xml}</Potential_Mitigations>"
        f"</Weakness>"
    )


def _catalog_xml(weaknesses: Sequence[str], *, view_name: str = DEFAULT_VIEW_NAME) -> bytes:
    body = "".join(weaknesses)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Weakness_Catalog xmlns="http://cwe.mitre.org/cwe-7" '
        f'xmlns:xhtml="http://www.w3.org/1999/xhtml" '
        f'Name="{view_name}" Version="4.20" Date="2026-04-30">'
        f"<Weaknesses>{body}</Weaknesses>"
        f"</Weakness_Catalog>"
    )
    return xml.encode("utf-8")


def _fixture_xml(
    *,
    real: Sequence[str] = (),
    total: int = EXPECTED_ENTRY_COUNT,
    view_name: str = DEFAULT_VIEW_NAME,
) -> bytes:
    """A full catalog of ``total`` weaknesses: ``real`` verbatim, padded
    with minimal filler entries up to ``total``."""
    filler = [
        _weakness_xml(str(9000 + n), f"Filler Weakness {n}") for n in range(total - len(real))
    ]
    return _catalog_xml(list(real) + filler, view_name=view_name)


# ---------------------------------------------------------------------------
# Fakes for EmbeddingPort / VectorStorePort
# ---------------------------------------------------------------------------


@dataclass
class _FakeEmbeddingPort(EmbeddingPort):
    calls: list[str] = field(default_factory=list)
    error: Exception | None = None
    error_after: int | None = None

    @property
    def model_name(self) -> str:
        return "fake-embedding-model"

    async def embed(self, text: str) -> EmbeddingResult:
        self.calls.append(text)
        if self.error is not None and (
            self.error_after is None or len(self.calls) > self.error_after
        ):
            raise self.error
        return EmbeddingResult(vector=(0.1, 0.2, 0.3), model=self.model_name)


@dataclass
class _FakeVectorStorePort(VectorStorePort):
    upsert_calls: list[list[VectorPoint]] = field(default_factory=list)
    error: Exception | None = None

    async def ensure_collection(self) -> None:
        pass

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        if self.error is not None:
            raise self.error
        self.upsert_calls.append(list(points))

    async def search(self, query_vector: Sequence[float], *, limit: int = 5) -> list[VectorMatch]:
        raise NotImplementedError("not used by ingestion")


# ---------------------------------------------------------------------------
# Source parsing / filtering
# ---------------------------------------------------------------------------


def test_parse_extracts_exactly_25_entries() -> None:
    entries = parse_cwe_top25_xml(_fixture_xml())
    assert len(entries) == EXPECTED_ENTRY_COUNT


def test_parse_raises_cwe_source_error_when_count_is_not_25() -> None:
    with pytest.raises(CweSourceError, match="expected exactly 25"):
        parse_cwe_top25_xml(_fixture_xml(total=24))


def test_parse_raises_cwe_source_error_on_malformed_xml() -> None:
    with pytest.raises(CweSourceError, match="not well-formed XML"):
        parse_cwe_top25_xml(b"<Weakness_Catalog><Weaknesses>")


def test_parse_raises_cwe_source_error_when_view_name_is_wrong() -> None:
    xml = _fixture_xml(view_name="VIEW LIST: CWE-1000: Research Concepts")
    with pytest.raises(CweSourceError, match="does not appear to be the 2025 CWE Top 25 view"):
        parse_cwe_top25_xml(xml)


def test_parse_extracts_id_name_and_description() -> None:
    real = _weakness_xml(
        "79",
        "Cross-Site Scripting",
        description="The product does not neutralize user-controllable input.",
    )
    entries = parse_cwe_top25_xml(_fixture_xml(real=[real]))
    entry = next(e for e in entries if e.cwe_id == "79")
    assert entry.name == "Cross-Site Scripting"
    assert entry.description == "The product does not neutralize user-controllable input."


def test_parse_flattens_nested_markup_and_collapses_whitespace() -> None:
    real = _weakness_xml(
        "120",
        "Buffer Overflow",
        description="""
            <xhtml:p>  Use a vetted   library.  </xhtml:p>
            <xhtml:p>See also REF-56.</xhtml:p>
        """,
    )
    entries = parse_cwe_top25_xml(_fixture_xml(real=[real]))
    entry = next(e for e in entries if e.cwe_id == "120")
    assert entry.description == "Use a vetted library. See also REF-56."


def test_parse_joins_multiple_mitigations_with_phase_labels() -> None:
    real = _weakness_xml(
        "89",
        "SQL Injection",
        mitigations=[
            ("Architecture and Design", "Use parameterized queries."),
            ("Implementation", "Escape all input."),
            (None, "Also review input validation."),
        ],
    )
    entries = parse_cwe_top25_xml(_fixture_xml(real=[real]))
    entry = next(e for e in entries if e.cwe_id == "89")
    assert entry.mitigation_text == (
        "(Architecture and Design) Use parameterized queries.\n"
        "(Implementation) Escape all input.\n"
        "Also review input validation."
    )


def test_parse_handles_entry_with_no_mitigations() -> None:
    real = _weakness_xml("22", "Path Traversal", mitigations=[])
    entries = parse_cwe_top25_xml(_fixture_xml(real=[real]))
    entry = next(e for e in entries if e.cwe_id == "22")
    assert entry.mitigation_text == ""


def test_parse_raises_cwe_source_error_when_description_missing() -> None:
    real = '<Weakness ID="78" Name="OS Command Injection"><Potential_Mitigations/></Weakness>'
    with pytest.raises(CweSourceError, match="CWE-78 is missing a Description"):
        parse_cwe_top25_xml(_fixture_xml(real=[real]))


def test_parse_raises_cwe_source_error_when_id_attribute_missing() -> None:
    real = '<Weakness Name="No Id"><Description>desc</Description></Weakness>'
    with pytest.raises(CweSourceError, match="missing its ID or Name attribute"):
        parse_cwe_top25_xml(_fixture_xml(real=[real]))


def test_parse_skips_mitigations_with_no_description_text() -> None:
    real = (
        '<Weakness ID="94" Name="Code Injection">'
        "<Description>desc</Description>"
        "<Potential_Mitigations>"
        "<Mitigation><Phase>Implementation</Phase></Mitigation>"
        "<Mitigation><Description>Validate input.</Description></Mitigation>"
        "</Potential_Mitigations>"
        "</Weakness>"
    )
    entries = parse_cwe_top25_xml(_fixture_xml(real=[real]))
    entry = next(e for e in entries if e.cwe_id == "94")
    assert entry.mitigation_text == "Validate input."


# ---------------------------------------------------------------------------
# Chunk construction
# ---------------------------------------------------------------------------


def test_build_chunk_includes_id_name_description_and_mitigations() -> None:
    entry = CweEntry(
        cwe_id="79",
        name="Cross-Site Scripting",
        description="Does not neutralize input.",
        mitigation_text="(Implementation) Escape output.",
    )
    chunk = build_cwe_chunk(entry)
    assert chunk == (
        "CWE-79: Cross-Site Scripting\n"
        "\n"
        "Description:\n"
        "Does not neutralize input.\n"
        "\n"
        "Mitigations:\n"
        "(Implementation) Escape output."
    )


def test_build_chunk_omits_mitigations_section_when_empty() -> None:
    entry = CweEntry(
        cwe_id="22", name="Path Traversal", description="Allows traversal.", mitigation_text=""
    )
    chunk = build_cwe_chunk(entry)
    assert "Mitigations:" not in chunk
    assert chunk == "CWE-22: Path Traversal\n\nDescription:\nAllows traversal."


def test_build_chunk_is_deterministic() -> None:
    entry = CweEntry(cwe_id="79", name="XSS", description="desc", mitigation_text="mit")
    assert build_cwe_chunk(entry) == build_cwe_chunk(entry)


# ---------------------------------------------------------------------------
# Deterministic ids
# ---------------------------------------------------------------------------


def test_cwe_point_id_is_a_valid_uuid_string() -> None:
    point_id = cwe_point_id("79")
    assert uuid.UUID(point_id) is not None


def test_cwe_point_id_is_deterministic() -> None:
    assert cwe_point_id("79") == cwe_point_id("79")


def test_cwe_point_id_differs_for_different_cwe_ids() -> None:
    assert cwe_point_id("79") != cwe_point_id("89")


# ---------------------------------------------------------------------------
# Embedding / upsert orchestration
# ---------------------------------------------------------------------------


async def test_execute_embeds_and_upserts_all_25_entries(tmp_path: Path) -> None:
    source_path = tmp_path / "cwe.xml"
    source_path.write_bytes(_fixture_xml())
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort()
    use_case = IngestCweTop25UseCase(embedding_port=embedding_port, vector_store=vector_store)

    result = await use_case.execute(source_path=source_path)

    assert result == EXPECTED_ENTRY_COUNT
    assert len(embedding_port.calls) == EXPECTED_ENTRY_COUNT
    assert len(vector_store.upsert_calls) == 1
    assert len(vector_store.upsert_calls[0]) == EXPECTED_ENTRY_COUNT


async def test_execute_upserts_points_with_expected_id_vector_and_payload(tmp_path: Path) -> None:
    real = _weakness_xml("79", "Cross-Site Scripting", description="Does not neutralize input.")
    source_path = tmp_path / "cwe.xml"
    source_path.write_bytes(_fixture_xml(real=[real]))
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort()
    use_case = IngestCweTop25UseCase(embedding_port=embedding_port, vector_store=vector_store)

    await use_case.execute(source_path=source_path)

    points = vector_store.upsert_calls[0]
    point = next(p for p in points if p.payload["cwe_id"] == "CWE-79")
    assert point.id == cwe_point_id("79")
    assert point.vector == (0.1, 0.2, 0.3)
    assert point.payload["name"] == "Cross-Site Scripting"
    assert point.payload["corpus"] == CORPUS_NAME
    assert point.payload["corpus_version"] == CORPUS_VERSION
    assert point.payload["text"] == build_cwe_chunk(
        CweEntry(
            cwe_id="79",
            name="Cross-Site Scripting",
            description="Does not neutralize input.",
            mitigation_text="",
        )
    )


async def test_execute_raises_cwe_source_error_for_missing_file(tmp_path: Path) -> None:
    use_case = IngestCweTop25UseCase(
        embedding_port=_FakeEmbeddingPort(), vector_store=_FakeVectorStorePort()
    )
    with pytest.raises(CweSourceError, match="could not read CWE source file"):
        await use_case.execute(source_path=tmp_path / "does-not-exist.xml")


async def test_execute_raises_cwe_source_error_for_malformed_source(tmp_path: Path) -> None:
    source_path = tmp_path / "cwe.xml"
    source_path.write_bytes(b"not xml at all")
    use_case = IngestCweTop25UseCase(
        embedding_port=_FakeEmbeddingPort(), vector_store=_FakeVectorStorePort()
    )
    with pytest.raises(CweSourceError, match="not well-formed XML"):
        await use_case.execute(source_path=source_path)


async def test_execute_propagates_embedding_error_unchanged(tmp_path: Path) -> None:
    source_path = tmp_path / "cwe.xml"
    source_path.write_bytes(_fixture_xml())
    embedding_port = _FakeEmbeddingPort(error=EmbeddingError("model crashed"), error_after=3)
    vector_store = _FakeVectorStorePort()
    use_case = IngestCweTop25UseCase(embedding_port=embedding_port, vector_store=vector_store)

    with pytest.raises(EmbeddingError, match="model crashed"):
        await use_case.execute(source_path=source_path)
    assert vector_store.upsert_calls == []


async def test_execute_propagates_vector_store_error_unchanged(tmp_path: Path) -> None:
    source_path = tmp_path / "cwe.xml"
    source_path.write_bytes(_fixture_xml())
    embedding_port = _FakeEmbeddingPort()
    vector_store = _FakeVectorStorePort(error=VectorStoreError("qdrant unreachable"))
    use_case = IngestCweTop25UseCase(embedding_port=embedding_port, vector_store=vector_store)

    with pytest.raises(VectorStoreError, match="qdrant unreachable"):
        await use_case.execute(source_path=source_path)
    assert len(embedding_port.calls) == EXPECTED_ENTRY_COUNT
