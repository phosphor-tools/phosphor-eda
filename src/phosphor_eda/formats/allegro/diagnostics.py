"""Shared diagnostic constructors for Allegro record extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.records import (
    AllegroRecordDiagnostic,
    payload_coords,
    payload_int,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.allegro.records import AllegroRecord


def drop_diagnostic(
    record: AllegroRecord,
    *,
    code: str,
    message: str,
    reference_key: int | None = None,
) -> AllegroRecordDiagnostic:
    return AllegroRecordDiagnostic(
        code=code,
        message=message,
        offset=record.offset,
        tag=record.tag,
        key=record.key,
        reference_key=reference_key,
    )


def dropped_stream_tail_diagnostic(
    data: bytes, offset: int, size: int
) -> AllegroRecordDiagnostic | None:
    """Diagnostic for a 0x00-tag forward scan that abandons the rest of the stream.

    Returns ``None`` when the abandoned tail is pure zero padding, since aligned
    end-of-stream padding drops no data.
    """
    if not any(data[offset:]):
        return None
    return AllegroRecordDiagnostic(
        code="dropped-record-stream-tail",
        message=f"record scan gave up at 0x{offset:X}; dropped {size - offset} trailing bytes",
        offset=offset,
        tag=0x00,
    )


def skipped_record_padding_diagnostic(*, offset: int, word_count: int) -> AllegroRecordDiagnostic:
    return AllegroRecordDiagnostic(
        code="skipped-record-padding-garbage",
        message=f"record scan skipped {word_count} nonzero padding word(s) at 0x{offset:X}",
        offset=offset,
    )


def unknown_field_subtype_diagnostic(
    *, offset: int, subtype: int, size: int
) -> AllegroRecordDiagnostic:
    return AllegroRecordDiagnostic(
        code="unknown-field-subtype",
        message=f"0x03 field subtype 0x{subtype:02X} is unknown; decoded {size}-byte value as u32",
        offset=offset,
        tag=0x03,
    )


def skipped_field_subtype_diagnostic(
    *, offset: int, subtype: int, size: int
) -> AllegroRecordDiagnostic:
    return AllegroRecordDiagnostic(
        code="skipped-unknown-field-subtype",
        message=f"0x03 field subtype 0x{subtype:02X} unhandled; skipped {size} bytes",
        offset=offset,
        tag=0x03,
    )


def scan_zero_padding(data: bytes, start: int) -> tuple[int, AllegroRecordDiagnostic | None]:
    """Advance over 4-byte zero-lead words, flagging nonzero padding garbage.

    Returns the first cursor whose byte is nonzero and, when any skipped word
    carried nonzero bytes past its zero lead, a diagnostic recording the run.
    """
    cursor = start
    garbage_start: int | None = None
    garbage_words = 0
    while cursor < len(data) and data[cursor] == 0:
        if any(data[cursor + 1 : cursor + 4]):
            if garbage_start is None:
                garbage_start = cursor
            garbage_words += 1
        cursor += 4
    if garbage_start is None:
        return cursor, None
    return cursor, skipped_record_padding_diagnostic(offset=garbage_start, word_count=garbage_words)


def missing_layer_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    class_id = payload_int(record, "layer_class_id")
    subclass_id = payload_int(record, "layer_subclass_id")
    return AllegroRecordDiagnostic(
        code="unresolved-graphic-layer",
        message=(
            f"graphic record {record.key} references missing Allegro layer {class_id}:{subclass_id}"
        ),
        offset=record.offset,
        tag=record.tag,
        key=record.key,
    )


def missing_header_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    return drop_diagnostic(
        record,
        code="missing-allegro-header",
        message=f"graphic record {record.key} cannot be converted without an Allegro header",
    )


def missing_payload_diagnostic(record: AllegroRecord, payload_key: str) -> AllegroRecordDiagnostic:
    return drop_diagnostic(
        record,
        code="missing-graphic-payload",
        message=f"graphic record {record.key} is missing payload field {payload_key}",
    )


def drc_marker_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    class_id = payload_int(record, "layer_class_id")
    subclass_id = payload_int(record, "layer_subclass_id")
    coords = payload_coords(record, "coords")
    coord_text = ",".join(str(coord) for coord in coords) if coords is not None else ""
    return AllegroRecordDiagnostic(
        code="drc-marker",
        message=(
            f"DRC marker {record.key} on Allegro layer {class_id}:{subclass_id}"
            + (f" has native coords {coord_text}" if coord_text else "")
        ),
        offset=record.offset,
        tag=record.tag,
        key=record.key,
    )


def build_diagnostic(
    record: AllegroRecord,
    *,
    code: str,
    message: str,
    reference_key: int | None = None,
) -> AllegroRecordDiagnostic:
    """Board-assembly diagnostic constructor.

    Unlike :func:`drop_diagnostic`, native key ``0`` is treated as "no
    reference" and collapses to ``None``. Board-assembly reference keys use 0 as
    the missing-key sentinel, so a zero here is not a real record reference.
    """
    return drop_diagnostic(
        record,
        code=code,
        message=message,
        reference_key=reference_key or None,
    )
