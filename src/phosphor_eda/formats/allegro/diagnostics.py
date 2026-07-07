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
