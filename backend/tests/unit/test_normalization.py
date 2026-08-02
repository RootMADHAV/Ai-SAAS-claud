"""Unit tests for app/application/scanning/normalization.py (Milestone 4)."""

from __future__ import annotations

import json

import pytest

from app.application.scanning.normalization import (
    NormalizationError,
    UnsupportedScanOutputFormatError,
    normalize_scan_output,
)


def _nuclei_line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "template-id": "CVE-2021-12345",
        "info": {
            "name": "Example Vulnerability",
            "severity": "high",
            "description": "An example vulnerability.",
            "classification": {
                "cve-id": ["CVE-2021-12345"],
                "cvss-score": 7.5,
                "cvss-metrics": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H",
            },
        },
        "host": "example.com",
        "matched-at": "https://example.com/vuln",
    }
    line.update(overrides)
    return line


def test_parses_a_single_nuclei_match() -> None:
    raw = (json.dumps(_nuclei_line()) + "\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.scanner_name == "nuclei"
    assert finding.template_id == "CVE-2021-12345"
    assert finding.title == "Example Vulnerability"
    assert finding.host == "example.com"
    assert finding.matched_at == "https://example.com/vuln"
    assert finding.raw_severity == "high"
    assert finding.description == "An example vulnerability."
    assert finding.cve_ids == ("CVE-2021-12345",)
    assert finding.cvss_score == 7.5
    assert finding.cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"
    assert finding.raw_evidence == _nuclei_line()


def test_parses_multiple_lines() -> None:
    lines = [_nuclei_line(**{"template-id": f"template-{i}"}) for i in range(3)]
    raw = "\n".join(json.dumps(line) for line in lines).encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert [f.template_id for f in findings] == ["template-0", "template-1", "template-2"]


def test_empty_output_is_no_findings() -> None:
    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=b""
    )
    assert findings == []


def test_blank_lines_are_skipped() -> None:
    raw = ("\n" + json.dumps(_nuclei_line()) + "\n\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert len(findings) == 1


def test_missing_optional_fields_use_sane_defaults() -> None:
    minimal = {"template-id": "minimal-template", "host": "example.org"}
    raw = (json.dumps(minimal) + "\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    finding = findings[0]
    assert finding.title == "minimal-template"
    assert finding.raw_severity == "info"
    assert finding.description is None
    assert finding.cve_ids == ()
    assert finding.cvss_score is None
    assert finding.cvss_vector is None


def test_falls_back_to_ip_field_when_host_missing() -> None:
    line = {"template-id": "t", "ip": "203.0.113.5"}
    raw = (json.dumps(line) + "\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert findings[0].host == "203.0.113.5"


def test_malformed_json_line_raises_normalization_error() -> None:
    raw = b"{not valid json\n"

    with pytest.raises(NormalizationError):
        normalize_scan_output(output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw)


def test_unsupported_output_format_raises() -> None:
    with pytest.raises(UnsupportedScanOutputFormatError):
        normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=b"<xml/>")


def test_non_numeric_cvss_score_is_dropped_not_raised() -> None:
    line = _nuclei_line()
    line["info"]["classification"]["cvss-score"] = "not-a-number"  # type: ignore[index]
    raw = (json.dumps(line) + "\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert findings[0].cvss_score is None
