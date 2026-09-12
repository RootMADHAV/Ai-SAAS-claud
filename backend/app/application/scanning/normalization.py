"""Normalization -- the Milestone 4 pipeline step that turns a scanner's
raw output bytes into structured, scanner-agnostic data the rest of the
pipeline (deduplicate/correlate/enrich/persist) can work with.

Per PROJECT_STATE.md section 1, Scanning does not own findings -- so the
knowledge of what a nuclei JSON line (or an nmap XML `<port>` element)
even means (which of its fields map to a title, a severity, a CVE)
cannot live in `app/scanner_engine/adapters/`, which deliberately stops
at "here is exactly what the scanner said" (see `NucleiAdapter`'s/
`NmapAdapter`'s and `ScanOutput`'s docstrings, all of which name this
module's step as where that mapping belongs). It lives here instead, in
the application layer, where Scanning-context data
(`ScanOutput.output_format`) and Findings-context concepts (severity,
CVSS) are both legitimately in scope for a use case that coordinates
across bounded contexts.

Two formats are supported: nuclei's `"nuclei-jsonl"` (Milestone 3) and
nmap's `"nmap-xml"` (Phase 4, TD #16). `normalize_scan_output` still
dispatches via a plain if/elif over these two known formats, not an
injectable `NormalizerPort` (mirroring `ScannerPort`) -- this module's
own prior version flagged "a second format" as the natural point to
reconsider that, but an explicit instruction for this specific piece of
work was not to introduce new pipeline/scanner abstractions, so the
if/elif is kept exactly as extensible as the existing
`_select_active_scanner` precedent in `app/workers/tasks.py` (an
explicit branch per known concrete case, not a registry) rather than
built out further here. A third real format is the more natural next
trigger to revisit this specific choice.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

_NUCLEI_JSONL_FORMAT = "nuclei-jsonl"
_NMAP_XML_FORMAT = "nmap-xml"

# nmap XML port <state> values other than this one (closed, filtered,
# open|filtered, unfiltered, closed|filtered) are not surfaced as
# findings -- see _parse_nmap_xml's own comment on why.
_NMAP_OPEN_PORT_STATE = "open"


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
    if output_format == _NMAP_XML_FORMAT:
        return _parse_nmap_xml(raw_bytes, scanner_name=scanner_name)
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


def _parse_nmap_xml(raw_bytes: bytes, *, scanner_name: str) -> list[NormalizedFinding]:
    """Turns nmap's own `-oX -` XML (`NmapAdapter`'s output format) into
    `NormalizedFinding`s -- one per open port, across every `<host>` in
    the document that nmap reported as up.

    Only `state="open"` ports become findings. `NmapAdapter` is pinned to
    `-sT -Pn` with no NSE vulnerability scripts (see that adapter's own
    module docstring on why), so this parser has no CVE/CVSS data to
    extract -- an open port by itself is a reconnaissance fact, not a
    scored vulnerability, so every nmap-derived finding is reported at
    `raw_severity="info"` with no CVE ids and no CVSS candidate. This is
    an honest reflection of what a plain TCP-connect port scan actually
    tells you, not a placeholder pending more parsing effort -- closed/
    filtered/`open|filtered` ports are excluded as noise, not partially
    parsed and dropped later; a down host (`<status state!="up">`)
    contributes no findings at all, the same "no signal, no findings"
    treatment nuclei's own empty-output case already gets.

    Uses the stdlib `xml.etree.ElementTree`, not a new dependency -- the
    input is `NmapAdapter`'s own subprocess output (already sanity-checked
    there for a `<nmaprun` root marker before this function ever sees
    it), not arbitrary attacker-supplied XML, so the classic XXE/external-
    entity concern a new parsing dependency might exist to guard against
    does not apply here; stdlib `ElementTree` does not resolve external
    entities by default regardless.
    """
    if not raw_bytes.strip():
        return []
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        raise NormalizationError(f"malformed nmap XML: {exc}") from exc

    findings: list[NormalizedFinding] = []
    for host_el in root.findall("host"):
        status_el = host_el.find("status")
        if status_el is None or status_el.get("state") != "up":
            continue
        host_value = _nmap_host_value(host_el)
        for port_el in host_el.findall("ports/port"):
            state_el = port_el.find("state")
            if state_el is None or state_el.get("state") != _NMAP_OPEN_PORT_STATE:
                continue
            findings.append(
                _nmap_port_to_normalized(port_el, host_value, scanner_name=scanner_name)
            )
    return findings


def _nmap_host_value(host_el: ET.Element) -> str:
    """A hostname if nmap resolved/was given one, else the scanned IP --
    the same "prefer the human-readable name, fall back to the address"
    precedent `_nuclei_match_to_normalized` already sets for its own
    `host`/`ip` fallback, so `RunScanWorkflowUseCase._infer_asset_type`
    (which only exists downstream of this function) sees the same shape
    of value regardless of which scanner produced it."""
    hostname_el = host_el.find("hostnames/hostname")
    if hostname_el is not None and hostname_el.get("name"):
        return str(hostname_el.get("name"))
    for addrtype in ("ipv4", "ipv6"):
        for address_el in host_el.findall("address"):
            if address_el.get("addrtype") == addrtype and address_el.get("addr"):
                return str(address_el.get("addr"))
    any_address_el = host_el.find("address")
    if any_address_el is not None and any_address_el.get("addr"):
        return str(any_address_el.get("addr"))
    return ""


def _nmap_port_to_normalized(
    port_el: ET.Element, host_value: str, *, scanner_name: str
) -> NormalizedFinding:
    protocol = port_el.get("protocol") or "tcp"
    port_id = port_el.get("portid") or "0"
    service_el = port_el.find("service")
    service_name = service_el.get("name") if service_el is not None else None

    # A stable, per-port-type identifier -- fingerprinting
    # (compute_fingerprint, called by RunScanWorkflowUseCase._deduplicate
    # with this exact field) needs the *same* open port recurring across
    # scans to map to the *same* Finding row, mirroring how nuclei's own
    # template-id already plays this role there.
    template_id = f"open-port-{protocol}-{port_id}"
    matched_at = f"{host_value}:{port_id}/{protocol}"
    title = f"Open port {port_id}/{protocol} ({service_name or 'unknown'})"

    description = _nmap_service_description(service_el)

    raw_evidence: dict[str, object] = {
        "host": host_value,
        "protocol": protocol,
        "port": port_id,
        "state": _NMAP_OPEN_PORT_STATE,
    }
    if service_el is not None:
        raw_evidence["service"] = dict(service_el.attrib)

    return NormalizedFinding(
        scanner_name=scanner_name,
        template_id=template_id,
        title=title,
        host=host_value,
        matched_at=matched_at,
        # See _parse_nmap_xml's own docstring: a plain open port has no
        # scanner-assigned severity or CVE/CVSS data to carry -- "info"
        # is the honest classification, not a placeholder.
        raw_severity="info",
        description=description,
        cve_ids=(),
        cvss_score=None,
        cvss_vector=None,
        raw_evidence=raw_evidence,
    )


def _nmap_service_description(service_el: ET.Element | None) -> str | None:
    if service_el is None:
        return None
    parts = [
        value for attr in ("product", "version", "extrainfo") if (value := service_el.get(attr))
    ]
    return " ".join(parts) if parts else None
