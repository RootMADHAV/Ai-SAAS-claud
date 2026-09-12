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
    """ "burp-xml" specifically: a real future scanner_engine/adapters/
    burp/ stub already exists (Phase 4 roadmap) but has no adapter or
    normalizer yet, making it a realistic not-yet-wired example rather
    than a nonsense string -- "nmap-xml" moved to its own supported-format
    test class below once TD #16 added a real normalizer for it."""
    with pytest.raises(UnsupportedScanOutputFormatError):
        normalize_scan_output(output_format="burp-xml", scanner_name="burp", raw_bytes=b"<xml/>")


def test_non_numeric_cvss_score_is_dropped_not_raised() -> None:
    line = _nuclei_line()
    line["info"]["classification"]["cvss-score"] = "not-a-number"  # type: ignore[index]
    raw = (json.dumps(line) + "\n").encode("utf-8")

    findings = normalize_scan_output(
        output_format="nuclei-jsonl", scanner_name="nuclei", raw_bytes=raw
    )

    assert findings[0].cvss_score is None


def _nmap_xml(
    *,
    host_status: str = "up",
    hostname: str | None = "example.com",
    address: str = "93.184.216.34",
    ports_xml: str = (
        '<port protocol="tcp" portid="80"><state state="open"/><service name="http"/></port>'
    ),
) -> bytes:
    hostnames_xml = (
        f'<hostnames><hostname name="{hostname}" type="user"/></hostnames>'
        if hostname
        else "<hostnames/>"
    )
    return (
        '<?xml version="1.0"?>'
        "<nmaprun>"
        "<host>"
        f'<status state="{host_status}"/>'
        f'<address addr="{address}" addrtype="ipv4"/>'
        f"{hostnames_xml}"
        f"<ports>{ports_xml}</ports>"
        "</host>"
        "</nmaprun>"
    ).encode()


def test_parses_a_single_open_port() -> None:
    raw = _nmap_xml(
        ports_xml=(
            '<port protocol="tcp" portid="80">'
            '<state state="open" reason="syn-ack"/>'
            '<service name="http" product="Apache httpd" version="2.4.41"/>'
            "</port>"
        )
    )

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.scanner_name == "nmap"
    assert finding.template_id == "open-port-tcp-80"
    assert finding.title == "Open port 80/tcp (http)"
    assert finding.host == "example.com"
    assert finding.matched_at == "example.com:80/tcp"
    assert finding.raw_severity == "info"
    assert finding.description == "Apache httpd 2.4.41"
    assert finding.cve_ids == ()
    assert finding.cvss_score is None
    assert finding.cvss_vector is None
    assert finding.raw_evidence["port"] == "80"
    assert finding.raw_evidence["service"] == {
        "name": "http",
        "product": "Apache httpd",
        "version": "2.4.41",
    }


def test_parses_multiple_open_ports_on_one_host() -> None:
    raw = _nmap_xml(
        ports_xml=(
            '<port protocol="tcp" portid="80"><state state="open"/><service name="http"/></port>'
            '<port protocol="tcp" portid="443"><state state="open"/><service name="https"/></port>'
        )
    )

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert [f.template_id for f in findings] == ["open-port-tcp-80", "open-port-tcp-443"]


def test_closed_and_filtered_ports_are_skipped() -> None:
    raw = _nmap_xml(
        ports_xml=(
            '<port protocol="tcp" portid="22"><state state="filtered"/></port>'
            '<port protocol="tcp" portid="23"><state state="closed"/></port>'
            '<port protocol="tcp" portid="80"><state state="open"/><service name="http"/></port>'
        )
    )

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert [f.template_id for f in findings] == ["open-port-tcp-80"]


def test_down_host_produces_no_findings() -> None:
    raw = _nmap_xml(
        host_status="down",
        ports_xml='<port protocol="tcp" portid="80"><state state="open"/></port>',
    )

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert findings == []


def test_falls_back_to_address_when_no_hostname() -> None:
    raw = _nmap_xml(hostname=None, address="198.51.100.7")

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert findings[0].host == "198.51.100.7"
    assert findings[0].matched_at == "198.51.100.7:80/tcp"


def test_prefers_hostname_over_address() -> None:
    raw = _nmap_xml(hostname="example.net", address="198.51.100.7")

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert findings[0].host == "example.net"


def test_missing_service_element_uses_unknown_label() -> None:
    raw = _nmap_xml(ports_xml='<port protocol="tcp" portid="8080"><state state="open"/></port>')

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    finding = findings[0]
    assert finding.title == "Open port 8080/tcp (unknown)"
    assert finding.description is None
    assert "service" not in finding.raw_evidence


def test_nmap_empty_output_is_no_findings() -> None:
    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=b"")
    assert findings == []


def test_malformed_nmap_xml_raises_normalization_error() -> None:
    with pytest.raises(NormalizationError):
        normalize_scan_output(
            output_format="nmap-xml", scanner_name="nmap", raw_bytes=b"<nmaprun><host>"
        )


def test_multiple_hosts_all_contribute_findings() -> None:
    raw = (
        b'<?xml version="1.0"?>'
        b"<nmaprun>"
        b"<host>"
        b'<status state="up"/>'
        b'<address addr="93.184.216.34" addrtype="ipv4"/>'
        b'<hostnames><hostname name="a.example.com" type="user"/></hostnames>'
        b'<ports><port protocol="tcp" portid="80"><state state="open"/></port></ports>'
        b"</host>"
        b"<host>"
        b'<status state="up"/>'
        b'<address addr="93.184.216.35" addrtype="ipv4"/>'
        b'<hostnames><hostname name="b.example.com" type="user"/></hostnames>'
        b'<ports><port protocol="tcp" portid="443"><state state="open"/></port></ports>'
        b"</host>"
        b"</nmaprun>"
    )

    findings = normalize_scan_output(output_format="nmap-xml", scanner_name="nmap", raw_bytes=raw)

    assert [(f.host, f.template_id) for f in findings] == [
        ("a.example.com", "open-port-tcp-80"),
        ("b.example.com", "open-port-tcp-443"),
    ]
