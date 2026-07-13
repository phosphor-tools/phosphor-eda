"""Tests for Altium record parser."""

from pathlib import Path

from phosphor_eda.formats.altium.record_parser import (
    parse_record_payload,
    read_records,
    read_schematic_records,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
MCU_SCHDOC = UPSTREAM_FIXTURES / "qfsae-pcb/Debugger/MCU.SchDoc"


def test_parse_record_payload_simple():
    payload = b"|RECORD=1|LOCATION.X=100|LOCATION.Y=200\x00"
    props = parse_record_payload(payload)
    assert props["record"] == "1"
    assert props["location.x"] == "100"
    assert props["location.y"] == "200"


def test_parse_record_payload_empty():
    payload = b"\x00"
    props = parse_record_payload(payload)
    assert props == {}


def test_read_records_single():
    payload = b"|RECORD=31|SheetStyle=10\x00"
    length = len(payload)
    binary = length.to_bytes(2, "little") + b"\x00\x00" + payload
    records = read_records(binary)
    assert len(records) == 1
    assert records[0]["record"] == "31"
    assert records[0]["sheetstyle"] == "10"


def test_read_records_multiple():
    def make_record(text: str) -> bytes:
        payload = text.encode("ascii") + b"\x00"
        return len(payload).to_bytes(2, "little") + b"\x00\x00" + payload

    binary = make_record("|RECORD=31|SheetStyle=10") + make_record("|RECORD=1|LibReference=RES")
    records = read_records(binary)
    assert len(records) == 2
    assert records[0]["record"] == "31"
    assert records[1]["record"] == "1"


def test_parse_record_payload_cp1252():
    """cp1252 high bytes should decode correctly (not replaced with U+FFFD)."""
    # ± is 0xB1 in cp1252, ° is 0xB0, µ is 0xB5
    payload = b"|Name=R1|Description=10k\xb1 5% \xb0C \xb5F\x00"
    props = parse_record_payload(payload)
    assert props["description"] == "10k± 5% °C µF"


def test_parse_record_payload_utf8_prefix():
    """%UTF8% prefixed keys should be decoded as UTF-8."""
    # Ω is U+03A9, encoded as 0xCE 0xA9 in UTF-8
    payload = b"|Description=1 Ohm|%UTF8%Description=1\xce\xa9\x00"
    props = parse_record_payload(payload)
    assert props["%utf8%description"] == "1Ω"
    # Regular key gets cp1252 decoding
    assert props["description"] == "1 Ohm"


def test_read_real_schdoc():
    records = read_schematic_records(str(MCU_SCHDOC))
    assert len(records) > 100
    record_types = {r["record"] for r in records if "record" in r}
    assert "31" in record_types  # Sheet
    assert "1" in record_types  # Component
    assert "27" in record_types  # Wire
