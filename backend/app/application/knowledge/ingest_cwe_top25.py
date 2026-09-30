"""CWE Top 25 ingestion use case (Phase 5 Milestone 3).

Static, one-time ingestion of the 2025 CWE Top 25 Most Dangerous
Software Weaknesses (MITRE CWE View-1435) into the vector store, for
later retrieval by ``AnalysisService`` (Milestone 4, not built yet).
Deliberately kept independent of ``AnalysisService`` -- this module
knows nothing about findings, scans, or prompts; it only turns the
vendored source file into embedded, searchable vectors.

No refresh/update mechanism -- this is a locked, one-off ingestion for
the MVP corpus (PROJECT_STATE.md's Phase 5 planning decision). Running
it again is safe (``VectorStorePort.upsert`` is insert-or-replace by
id, and this module's ids are deterministic -- see ``cwe_point_id``),
but nothing here schedules or triggers a re-run.

Three independently-testable pieces, matching this milestone's own
testing scope rather than one monolithic function:
- ``parse_cwe_top25_xml`` -- source parsing/filtering only.
- ``build_cwe_chunk`` -- chunk-text construction only, from an already-
  parsed ``CweEntry``.
- ``cwe_point_id`` -- deterministic id derivation only.
``IngestCweTop25UseCase`` orchestrates the three above plus
``EmbeddingPort``/``VectorStorePort`` (both dependency-injected, per
the same constructor-injection convention as every other use case in
this codebase, e.g. ``TriggerScanUseCase``), so it can be tested
without a real model or a real Qdrant server.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.application.interfaces.embedding_port import EmbeddingPort
from app.application.interfaces.vector_store_port import VectorPoint, VectorStorePort

CWE_XML_NAMESPACE = "http://cwe.mitre.org/cwe-7"
_NS = {"cwe": CWE_XML_NAMESPACE}

# Locked to the 2025 edition (PROJECT_STATE.md's Phase 5 corpus
# decision) -- not a configurable/rolling value. A future year's list
# is a new corpus decision, not a parameter of this one.
EXPECTED_ENTRY_COUNT = 25
EXPECTED_VIEW_NAME_MARKERS = ("2025", "Top 25")
CORPUS_NAME = "cwe_top25"
CORPUS_VERSION = "2025"


class CweSourceError(RuntimeError):
    """Raised when the vendored CWE source file is missing, not
    well-formed XML, does not look like the 2025 CWE Top 25 view, or
    does not contain exactly ``EXPECTED_ENTRY_COUNT`` weaknesses.
    Distinct from ``EmbeddingError``/``VectorStoreError`` so a failure's
    stage (bad source vs. embedding vs. storage) is never ambiguous to
    a caller."""


@dataclass(frozen=True, slots=True)
class CweEntry:
    """One parsed CWE Top 25 weakness -- the parser's output and the
    chunk-builder's input. ``mitigation_text`` is already flattened to
    one whitespace-normalized string (each mitigation's text, prefixed
    with its phase when the source gives one); it is ``""`` when the
    source has no mitigations for this weakness."""

    cwe_id: str
    name: str
    description: str
    mitigation_text: str


def parse_cwe_top25_xml(xml_bytes: bytes) -> list[CweEntry]:
    """Parse and filter the vendored CWE Top 25 source into exactly
    ``EXPECTED_ENTRY_COUNT`` entries.

    "Filtering" here is validating that the source -- a MITRE CWE
    View-1435 export, which is already scoped by MITRE's own export
    tool to just that view's members -- contains exactly the expected
    25 entries, not selecting a subset out of a larger catalog; a
    View-1435 export has nothing else to filter out.

    Raises ``CweSourceError`` if the XML is malformed, the root
    element's ``Name`` does not look like the 2025 Top 25 view, the
    entry count is not exactly ``EXPECTED_ENTRY_COUNT``, or any entry
    is missing its id, name, or description.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise CweSourceError(f"CWE source is not well-formed XML: {exc}") from exc

    view_name = root.get("Name", "")
    if not all(marker in view_name for marker in EXPECTED_VIEW_NAME_MARKERS):
        raise CweSourceError(
            "CWE source does not appear to be the 2025 CWE Top 25 view "
            f"(root Name attribute was {view_name!r})"
        )

    weakness_elements = root.findall("cwe:Weaknesses/cwe:Weakness", _NS)
    if len(weakness_elements) != EXPECTED_ENTRY_COUNT:
        raise CweSourceError(
            f"expected exactly {EXPECTED_ENTRY_COUNT} weaknesses in the CWE Top 25 source, "
            f"found {len(weakness_elements)}"
        )

    entries = []
    for element in weakness_elements:
        cwe_id = element.get("ID")
        name = element.get("Name")
        if not cwe_id or not name:
            raise CweSourceError("a Weakness element is missing its ID or Name attribute")

        description = _element_text(element.find("cwe:Description", _NS))
        if not description:
            raise CweSourceError(f"CWE-{cwe_id} is missing a Description")

        entries.append(
            CweEntry(
                cwe_id=cwe_id,
                name=name,
                description=description,
                mitigation_text=_extract_mitigation_text(element),
            )
        )
    return entries


def _extract_mitigation_text(weakness_element: ET.Element) -> str:
    """Flatten every ``Potential_Mitigations/Mitigation`` under
    ``weakness_element`` into one string, one mitigation per line,
    each prefixed with its phase when the source gives one. Returns
    ``""`` if the weakness has no mitigations."""
    lines = []
    for mitigation in weakness_element.findall("cwe:Potential_Mitigations/cwe:Mitigation", _NS):
        text = _element_text(mitigation.find("cwe:Description", _NS))
        if not text:
            continue
        phase = _element_text(mitigation.find("cwe:Phase", _NS))
        lines.append(f"({phase}) {text}" if phase else text)
    return "\n".join(lines)


def _element_text(element: ET.Element | None) -> str:
    """All text under ``element``, regardless of nested markup (the
    source mixes plain text and nested ``xhtml:p``/``xhtml:ul``
    content across different entries -- see this module's own
    inspection notes in PROJECT_STATE.md), with whitespace collapsed
    to single spaces so the result is deterministic regardless of the
    source's arbitrary indentation. ``""`` if ``element`` is ``None``."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def build_cwe_chunk(entry: CweEntry) -> str:
    """Build the one deterministic text chunk for ``entry`` -- CWE id,
    name, description, and mitigations (when the source has any).
    Deterministic: the same ``CweEntry`` always produces the exact same
    string.
    """
    parts = [f"CWE-{entry.cwe_id}: {entry.name}", "", "Description:", entry.description]
    if entry.mitigation_text:
        parts += ["", "Mitigations:", entry.mitigation_text]
    return "\n".join(parts)


def cwe_point_id(cwe_id: str) -> str:
    """Deterministic Qdrant-compatible point id for a CWE id (e.g.
    ``"79"``) -- UUID5 over the CWE's own canonical definition URL
    under the standard ``NAMESPACE_URL``, so the same CWE id always
    derives the same point id (required for ``upsert``'s insert-or-
    replace semantics to correctly replace rather than duplicate on a
    re-run) without this module inventing its own namespace UUID.
    """
    url = f"https://cwe.mitre.org/data/definitions/{cwe_id}.html"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, url))


class IngestCweTop25UseCase:
    """Reads the vendored CWE Top 25 source, embeds each entry's chunk,
    and upserts all of them into the vector store. ``embedding_port``/
    ``vector_store`` are constructor-injected, mirroring every other
    use case's convention (e.g. ``TriggerScanUseCase``), so this can be
    tested without a real model or a real Qdrant server.
    """

    def __init__(self, *, embedding_port: EmbeddingPort, vector_store: VectorStorePort) -> None:
        self._embedding_port = embedding_port
        self._vector_store = vector_store

    async def execute(self, *, source_path: Path) -> int:
        """Ingest the CWE Top 25 source at ``source_path``. Returns the
        number of entries ingested (always ``EXPECTED_ENTRY_COUNT`` on
        success).

        Raises ``CweSourceError`` if the source file cannot be read or
        parsed. Raises ``EmbeddingError``/``VectorStoreError`` unchanged
        if embedding or storage fails -- this use case does not
        translate or swallow either; only its own source-reading
        responsibility gets ``CweSourceError``.
        """
        try:
            xml_bytes = source_path.read_bytes()
        except OSError as exc:
            raise CweSourceError(f"could not read CWE source file {source_path}: {exc}") from exc

        entries = parse_cwe_top25_xml(xml_bytes)

        points = []
        for entry in entries:
            chunk = build_cwe_chunk(entry)
            embedding = await self._embedding_port.embed(chunk)
            points.append(
                VectorPoint(
                    id=cwe_point_id(entry.cwe_id),
                    vector=embedding.vector,
                    payload={
                        "cwe_id": f"CWE-{entry.cwe_id}",
                        "name": entry.name,
                        "corpus": CORPUS_NAME,
                        "corpus_version": CORPUS_VERSION,
                        "text": chunk,
                    },
                )
            )

        await self._vector_store.upsert(points)
        return len(points)
