"""Normalization -- the Milestone 4 pipeline step that turns a scanner's
raw output bytes into structured, scanner-agnostic data the rest of the
pipeline (deduplicate/correlate/enrich/persist) can work with.

Per PROJECT_STATE.md section 1, Scanning does not own findings -- so the
knowledge of what a nuclei JSON line even means (which of its fields map
to a title, a severity, a CVE) cannot live in
``app/scanner_engine/adapters/nuclei/``, which deliberately stops at
"here is exactly what nuclei said" (see ``NucleiAdapter``'s and
``ScanOutput``'s docstrings, both of which name this module's step as
where that mapping belongs). It lives here instead, in the application
layer, where Scanning-context data (``ScanOutput.output_format``) and
Findings-context concepts (severity, CVSS) are both legitimately in
scope for a use case that coordinates across bounded contexts.

Only nuclei's ``"nuclei-jsonl"`` format is supported today -- the only
adapter wired so far (Milestone 3). Dispatching on ``output_format`` via
an injectable ``NormalizerPort`` (mirroring ``ScannerPort``) is
deliberately not built yet: with exactly one real format to normalize, a
port with a single implementation would be an abstraction with nothing to
be abstract over, the same reasoning PROJECT_STATE.md section 3 already
applies to deferring ``BaseAgent`` until a second AI agent's shape is
known ("extract when a second agent's real shape is known, not before").
Revisit this module when Phase 4 adds a second scanner output format --
at that point ``normalize_scan_output``'s single if-branch becomes a
real dispatch problem worth a port for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_NUCLEI_JSONL_FORMAT = "nuclei-jsonl"


class NormalizationError(ValueError):
    """Raised when raw scanner output cannot be parsed into normalized
    findings -- malformed data, not a scanner execution failure (which
    ``ScannerExecutionError`` at the adapter layer already covers)."""


class UnsupportedScanOutputFormatError(ValueError):
    """Raised when asked to normalize an ``output_format`` no normalizer
    is registered for yet (see module docstring)."""


@dataclass(frozen=True, slots=True)
class NormalizedFinding:
    """One structured, pre-deduplication issue detected by a scan.

    Fields here are still scanner-native -- ``raw_severity`` is whatever
    string the scanner itself reported (e.g. nuclei template severity),
    not a validated ``SeverityLevel``; ``cvss_score``/``cvss_vector`` are
    unvalidated candidates, not yet turned into a ``CVSS`` value object.
    That validation happens in the enrich step
    (``RunScanWorkflowUseCase._enrich``), which is also why this
    dataclass has no ``CVSS``/``Severity`` fields of its own -- it
    predates enrichment, by pipeline order.
    """

    scanner_name: str
    template_id: str
    title: str
    host: str
    matched_at: str
    raw_severity: str
    description: str | None = None
    cve_ids: tuple[str, ...] = ()
    cvss_score: float | None = None
    cvss_vector: str | None = None
    raw_evidence: dict[str, object] = field(default_factory=dict)


def normalize_scan_output(
    *, output_format: str, scanner_name: str, raw_bytes: bytes
) -> list[NormalizedFinding]:
    """Dispatches on ``output_format`` -- the ``ScanOutput`` field this
    function exists specifically to consume (see its docstring in
    ``app/application/interfaces/scanner_port.py``)."""
    if output_format == _NUCLEI_JSONL_FORMAT:
        return _parse_nuclei_jsonl(raw_bytes, scanner_name=scanner_name)
    raise UnsupportedScanOutputFormatError(
        f"no normalizer registered for scan output format {output_format!r}"
    )


def _parse_nuclei_jsonl(raw_bytes: bytes, *, scanner_name: str) -> list[NormalizedFinding]:
    text = raw_bytes.decode("utf-8", errors="replace")
    findings: list[NormalizedFinding] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NormalizationError(f"malformed nuclei JSON on line {line_number}: {exc}") from exc
        findings.append(_nuclei_match_to_normalized(obj, scanner_name=scanner_name))
    return findings


def _nuclei_match_to_normalized(obj: dict[str, Any], *, scanner_name: str) -> NormalizedFinding:
    info: dict[str, Any] = obj.get("info") or {}
    classification: dict[str, Any] = info.get("classification") or {}
    template_id = str(obj.get("template-id") or "") or "unknown-template"
    host = str(obj.get("host") or obj.get("ip") or "")
    matched_at = str(obj.get("matched-at") or host)
    cve_ids = tuple(str(cve) for cve in (classification.get("cve-id") or []))
    cvss_score = classification.get("cvss-score")
    cvss_vector = classification.get("cvss-metrics")
    return NormalizedFinding(
        scanner_name=scanner_name,
        template_id=template_id,
        title=str(info.get("name") or template_id),
        host=host,
        matched_at=matched_at,
        raw_severity=str(info.get("severity") or "info"),
        description=info.get("description"),
        cve_ids=cve_ids,
        cvss_score=float(cvss_score) if isinstance(cvss_score, int | float) else None,
        cvss_vector=cvss_vector if isinstance(cvss_vector, str) else None,
        raw_evidence=obj,
    )
