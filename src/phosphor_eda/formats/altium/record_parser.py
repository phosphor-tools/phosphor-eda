"""Low-level parser for Altium SchDoc OLE compound-document streams.

A .SchDoc file is an OLE container with up to three streams:

- **FileHeader** — main schematic records (components, wires, pins, …)
- **Storage** — embedded images / icons
- **Additional** (optional) — signal-harness records (215–218)

Each stream is a sequence of binary records:
    [payload_length: 2 bytes LE] [0x00] [record_type: 1 byte] [payload]

For type-0 records the payload is null-terminated pipe-delimited ASCII:
    |KEY1=value1|KEY2=value2\\0
"""

import olefile

from phosphor_eda.formats.altium.errors import require_ole_file


def parse_record_payload(payload: bytes) -> dict[str, str]:
    """Parse a pipe-delimited payload into a property dict.

    Altium uses Windows-1252 (cp1252) encoding for record values.  Keys
    prefixed with ``%UTF8%`` contain UTF-8 encoded values for characters
    outside the cp1252 range.  We split on raw bytes so each value can be
    decoded with the correct codec.
    """
    raw = payload.rstrip(b"\x00")
    props: dict[str, str] = {}
    for part in raw.split(b"|"):
        if b"=" in part:
            key_bytes, value_bytes = part.split(b"=", 1)
            # Keys are always ASCII-safe; lowercase for case-insensitive lookup
            key = key_bytes.decode("ascii", errors="replace").lower()
            if key.startswith("%utf8%"):
                value = value_bytes.decode("utf-8", errors="replace")
            else:
                value = value_bytes.decode("cp1252", errors="replace")
            props[key] = value
    return props


def read_records(data: bytes) -> list[dict[str, str]]:
    """Read all type-0 records from a binary stream."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 4 <= len(data):
        payload_len = int.from_bytes(data[pos : pos + 2], "little")
        # data[pos+2] is always 0x00
        # data[pos+3] is record type
        if pos + 4 + payload_len > len(data):
            break  # Malformed trailing record
        rec_type = data[pos + 3]
        payload = data[pos + 4 : pos + 4 + payload_len]
        if rec_type == 0 and payload:
            props = parse_record_payload(payload)
            if props:
                records.append(props)
        pos += 4 + payload_len
    return records


def read_schematic_records(schdoc_path: str) -> list[dict[str, str]]:
    """Open a .SchDoc OLE file and return records from FileHeader + Additional.

    The Additional stream (when present) contains signal-harness objects
    (RECORD 215–218).  Its records are appended after those from FileHeader
    so that OWNERINDEX references resolve correctly across both streams.
    """
    require_ole_file(schdoc_path)
    ole = olefile.OleFileIO(schdoc_path)
    try:
        data = ole.openstream("FileHeader").read()
        records = read_records(data)
        if ole.exists("Additional"):
            additional_data = ole.openstream("Additional").read()
            records.extend(read_records(additional_data))
    finally:
        ole.close()
    return records
