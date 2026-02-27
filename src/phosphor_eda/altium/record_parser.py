"""Low-level parser for Altium SchDoc OLE FileHeader records.

Each record in the FileHeader stream has binary layout:
    [payload_length: 2 bytes LE] [0x00] [record_type: 1 byte] [payload]

For type 0 records, payload is null-terminated pipe-delimited ASCII:
    |KEY1=value1|KEY2=value2\0
"""

import olefile


def parse_record_payload(payload: bytes) -> dict[str, str]:
    """Parse a pipe-delimited payload into a property dict."""
    text = payload.rstrip(b"\x00").decode("ascii", errors="replace")
    props: dict[str, str] = {}
    for part in text.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            props[key] = value
    return props


def read_records(data: bytes) -> list[dict[str, str]]:
    """Read all type-0 records from a FileHeader binary stream."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 4 <= len(data):
        payload_len = int.from_bytes(data[pos : pos + 2], "little")
        # data[pos+2] is always 0x00
        # data[pos+3] is record type
        rec_type = data[pos + 3]
        payload = data[pos + 4 : pos + 4 + payload_len]
        if rec_type == 0 and payload:
            props = parse_record_payload(payload)
            if props:
                records.append(props)
        pos += 4 + payload_len
    return records


def read_schematic_records(schdoc_path: str) -> list[dict[str, str]]:
    """Open a .SchDoc OLE file and parse all records from its FileHeader stream."""
    ole = olefile.OleFileIO(schdoc_path)
    try:
        data = ole.openstream("FileHeader").read()
    finally:
        ole.close()
    return read_records(data)
