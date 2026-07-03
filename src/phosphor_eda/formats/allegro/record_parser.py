"""Record stream parsing for native Allegro binary containers."""

from __future__ import annotations

from types import MappingProxyType

from phosphor_eda.formats.allegro.binary import BoundedBinaryReader
from phosphor_eda.formats.allegro.constants import AllegroVersion, version_at_least
from phosphor_eda.formats.allegro.errors import AllegroParseError
from phosphor_eda.formats.allegro.records import (
    AllegroHeader,
    AllegroLayerListEntry,
    AllegroPadstackComponent,
    AllegroPayloadValue,
    AllegroRecord,
    AllegroRecordSet,
    AllegroStringTable,
)

_MAX_DYNAMIC_RECORD_ITEMS = 1_000_000
_MAX_PADSTACK_LAYER_COUNT = 256

# Text-parameter (0x36 code 0x08) items carry 8 native int32 fields on V16.x.
# Index 2 is char height, 3 char width, 4 inter-char spacing, 5 line spacing,
# 7 stroke width. V17.2+ items are larger; the leading 8 int32 are read as a
# hypothesis and the extra bytes are skipped to keep the stream aligned.
_TEXT_PARAM_LEADING_INT32 = 8


def parse_allegro_record_stream(
    data: bytes,
    *,
    header: AllegroHeader,
    string_table: AllegroStringTable,
    source_name: str,
) -> AllegroRecordSet:
    reader = BoundedBinaryReader(data, source_name=source_name)
    reader.seek(_align4(string_table.end_offset))

    records: list[AllegroRecord] = []
    while reader.offset < reader.size:
        offset = reader.offset
        tag = reader.read_uint8()
        if tag == 0x00:
            next_offset = _next_aligned_record_offset(data, reader.offset)
            if next_offset is not None:
                reader.seek(next_offset)
                continue
            break
        if tag > 0x3C:
            # Allegro record lengths are tag-specific and implicit, so an
            # unknown tag cannot be skipped without risking stream corruption.
            raise AllegroParseError(
                f"unknown Allegro record tag 0x{tag:02X}; record lengths are implicit",
                code="unknown-record-tag",
                offset=offset,
                source_name=source_name,
            )

        record = _parse_known_record(
            reader,
            tag=tag,
            offset=offset,
            version=header.version,
            record_0x27_end=header.record_0x27_end,
        )
        records.append(record)

    return AllegroRecordSet(
        header=header,
        string_table=string_table,
        records=tuple(records),
        end_offset=reader.offset,
    )


def _parse_known_record(
    reader: BoundedBinaryReader,
    *,
    tag: int,
    offset: int,
    version: AllegroVersion,
    record_0x27_end: int,
) -> AllegroRecord:
    payload: dict[str, AllegroPayloadValue] = {}
    key: int | None = None
    next_key: int | None = None

    if tag == 0x01:
        reader.skip(1)
        reader.skip(1)
        payload["subtype"] = reader.read_uint8()
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["parent_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["width"] = reader.read_uint32()
        payload["start_x"] = reader.read_int32()
        payload["start_y"] = reader.read_int32()
        payload["end_x"] = reader.read_int32()
        payload["end_y"] = reader.read_int32()
        payload["center_x"] = reader.read_allegro_float()
        payload["center_y"] = reader.read_allegro_float()
        payload["radius"] = reader.read_allegro_float()
        payload["bbox"] = (
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
        )
    elif tag == 0x03:
        reader.skip(1)
        payload["field_key"] = reader.read_uint16()
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        subtype = reader.read_uint8()
        reader.skip(1)
        size = reader.read_uint16()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["subtype"] = subtype
        payload["size"] = size
        _parse_field_substruct(reader, subtype=subtype, size=size, payload=payload, offset=offset)
    elif tag == 0x04:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["net_key"] = reader.read_uint32()
        payload["connected_item_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x05:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(9 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["first_segment_key"] = reader.read_uint32()
        reader.skip(2 * 4)
    elif tag == 0x06:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["component_device_type_key"] = reader.read_uint32()
        payload["symbol_name_key"] = reader.read_uint32()
        payload["first_instance_key"] = reader.read_uint32()
        reader.skip(3 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
    elif tag == 0x07:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172, count=3)
        payload["footprint_instance_key"] = reader.read_uint32()
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(4)
        payload["refdes_string_key"] = reader.read_uint32()
        reader.skip(3 * 4)
        payload["first_pad_key"] = reader.read_uint32()
    elif tag == 0x08:
        reader.skip(3)
        key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        if _version_lt(version, AllegroVersion.V_172):
            payload["pin_number_string_key"] = reader.read_uint32()
        next_key = reader.read_uint32()
        if _version_at_least(version, AllegroVersion.V_172):
            payload["pin_number_string_key"] = reader.read_uint32()
        payload["pin_name_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(4)
    elif tag == 0x09:
        reader.skip(3)
        key = reader.read_uint32()
        reader.skip(4 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(5 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x0A:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["coords"] = (
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
        )
        reader.skip(4 * 4 + 5 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x0C:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(2 * 4)
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(1 + 1 + 2)
        else:
            reader.skip(3 * 4)
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_180)
        reader.skip(2 * 4 + 2 * 4 + 3 * 4)
        if _version_at_least(version, AllegroVersion.V_174) and _version_lt(
            version, AllegroVersion.V_180
        ):
            reader.skip(4)
    elif tag == 0x0D:
        reader.skip(3)
        key = reader.read_uint32()
        payload["name_string_key"] = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        reader.skip(2 * 4)
        payload["padstack_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(2 * 4)
    elif tag == 0x0E:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["footprint_key"] = reader.read_uint32()
        reader.skip(3 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172, count=2)
        payload["coords"] = (
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
        )
        reader.skip(3 * 4)
        payload["rotation_mdeg"] = reader.read_uint32()
    elif tag == 0x0F:
        reader.skip(3)
        key = reader.read_uint32()
        payload["slot_name_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        reader.skip(32)
        if _version_at_least(version, AllegroVersion.V_172):
            next_key = reader.read_uint32()
        reader.skip(3 * 4)
    elif tag == 0x10:
        reader.skip(3)
        key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["component_instance_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        reader.skip(5 * 4)
    elif tag == 0x11:
        reader.skip(3)
        key = reader.read_uint32()
        payload["pin_name_string_key"] = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["pin_number_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x12:
        reader.skip(3)
        key = reader.read_uint32()
        reader.skip(4 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_165)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x14:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["parent_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["segment_key"] = reader.read_uint32()
        reader.skip(2 * 4)
    elif tag in {0x15, 0x16, 0x17}:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["parent_key"] = reader.read_uint32()
        payload["flags"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["width"] = reader.read_uint32()
        payload["start_x"] = reader.read_int32()
        payload["start_y"] = reader.read_int32()
        payload["end_x"] = reader.read_int32()
        payload["end_y"] = reader.read_int32()
    elif tag == 0x1B:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["net_name_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(4)
        payload["assignment_key"] = reader.read_uint32()
        payload["ratline_key"] = reader.read_uint32()
        payload["fields_key"] = reader.read_uint32()
        payload["match_group_key"] = reader.read_uint32()
        reader.skip(4 * 4)
    elif tag == 0x1C:
        key, next_key = _parse_padstack_record(
            reader, version=version, payload=payload, offset=offset
        )
    elif tag == 0x1D:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["name_string_key"] = reader.read_uint32()
        payload["field_key"] = reader.read_uint32()
        size_a = reader.read_uint16()
        size_b = reader.read_uint16()
        _require_dynamic_count(reader, size_a, offset=offset, label="0x1D dataA")
        _require_dynamic_count(reader, size_b, offset=offset, label="0x1D dataB")
        payload["data_a_count"] = size_a
        payload["data_b_count"] = size_b
        payload["data_b_fields"] = tuple(
            tuple(reader.read_int32() for _ in range(14)) for _ in range(size_b)
        )
        reader.skip(size_a * 256)
        _skip_cond_u32(reader, version, AllegroVersion.V_180)
    elif tag == 0x1E:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        if _version_at_least(version, AllegroVersion.V_164):
            reader.skip(2 + 2)
        payload["string_key"] = reader.read_uint32()
        size = reader.read_uint32()
        _require_dynamic_count(reader, size, offset=offset, label="0x1E string")
        payload["string_length"] = size
        reader.skip(size)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x1F:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(3 * 4 + 2)
        size = reader.read_uint16()
        _require_dynamic_count(reader, size, offset=offset, label="0x1F entry")
        payload["entry_count"] = size
        if _version_at_least(version, AllegroVersion.V_175):
            substruct_size = size * 384 + 8
        elif _version_at_least(version, AllegroVersion.V_172):
            substruct_size = size * 280 + 8
        elif _version_at_least(version, AllegroVersion.V_162):
            substruct_size = size * 280 + 4
        else:
            substruct_size = size * 240 + 4
        reader.skip(substruct_size)
    elif tag == 0x20:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(8 * 4)
        if _version_at_least(version, AllegroVersion.V_172):
            reader.skip(6 * 4)
    elif tag == 0x21:
        reader.skip(3)
        size = reader.read_uint32()
        if size < 12:
            raise AllegroParseError(
                f"record 0x21 size {size} is too small",
                code="record-length-invalid",
                offset=offset,
                source_name=reader.source_name,
            )
        payload["size"] = size
        key = reader.read_uint32()
        reader.skip(size - 12)
    elif tag == 0x22:
        reader.skip(3)
        key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(8 * 4)
    elif tag == 0x23:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(2 * 4 + 3 * 4 + 5 * 4 + 4 * 4)
        if _version_at_least(version, AllegroVersion.V_164):
            reader.skip(4 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x24:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["parent_key"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["coords"] = (
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
            reader.read_int32(),
        )
        reader.skip(3 * 4)
        payload["rotation_mdeg"] = reader.read_uint32()
    elif tag == 0x26:
        reader.skip(3)
        key = reader.read_uint32()
        payload["member_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["group_key"] = reader.read_uint32()
        payload["constraint_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x27:
        end_offset = record_0x27_end - 1
        if end_offset < reader.offset:
            raise AllegroParseError(
                "record 0x27 header extent points before record payload",
                code="record-length-invalid",
                offset=offset,
                source_name=reader.source_name,
            )
        total_bytes = end_offset - reader.offset
        if total_bytes <= 3:
            reader.skip(total_bytes)
        else:
            reader.skip(3)
            payload_bytes = total_bytes - 3
            if payload_bytes % 4 != 0:
                raise AllegroParseError(
                    f"record 0x27 reference payload has {payload_bytes} bytes, "
                    "which is not divisible by 4",
                    code="record-length-invalid",
                    offset=offset,
                    source_name=reader.source_name,
                )
            payload["reference_count"] = payload_bytes // 4
            reader.skip(payload_bytes)
    elif tag == 0x28:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        # Owner: a 0x2B footprint definition for package-symbol-local shapes,
        # or a 0x04 net assignment for board-level copper shapes.
        payload["owner_key"] = reader.read_uint32()
        reader.skip(4)
        if _version_at_least(version, AllegroVersion.V_172):
            payload["dynamic_shape_flags"] = reader.read_uint32()
            reader.skip(4)
        reader.skip(2 * 4)
        payload["first_keepout_key"] = reader.read_uint32()
        payload["first_segment_key"] = reader.read_uint32()
        reader.skip(2 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(4)
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(4)
        reader.skip(4 * 4)
    elif tag == 0x29:
        reader.skip(3)
        key = reader.read_uint32()
        reader.skip(4 * 4 + 2 * 4 + 4 + 4 + 3 * 4)
    elif tag == 0x2A:
        reader.skip(1)
        num_entries = reader.read_uint16()
        _require_dynamic_count(reader, num_entries, offset=offset, label="0x2A layer")
        payload["entry_count"] = num_entries
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        if _version_lt(version, AllegroVersion.V_165):
            entries = tuple(
                AllegroLayerListEntry(index=index, name=reader.read_fixed_string(36))
                for index in range(num_entries)
            )
        else:
            entries = tuple(
                AllegroLayerListEntry(
                    index=index,
                    name_string_key=reader.read_uint32(),
                    properties=reader.read_uint32(),
                    unidentified_word=reader.read_uint32(),
                )
                for index in range(num_entries)
            )
        payload["layer_entries"] = entries
        key = reader.read_uint32()
    elif tag == 0x2B:
        reader.skip(3)
        key = reader.read_uint32()
        payload["footprint_name_key"] = reader.read_uint32()
        reader.skip(4 + 4 * 4)
        next_key = reader.read_uint32()
        payload["first_instance_key"] = reader.read_uint32()
        reader.skip(6 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_164)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
    elif tag == 0x2C:
        payload["type"] = reader.read_uint8()
        payload["subtype"] = reader.read_uint16()
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172, count=3)
        payload["string_key"] = reader.read_uint32()
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(4)
        reader.skip(4 * 4)
    elif tag == 0x2D:
        reader.skip(1)
        payload["placement_side"] = reader.read_uint8()
        reader.skip(1)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        if _version_lt(version, AllegroVersion.V_172):
            payload["instance_ref_key"] = reader.read_uint32()
        reader.skip(2 + 2)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["flags"] = reader.read_uint32()
        payload["rotation_mdeg"] = reader.read_uint32()
        payload["coord_x"] = reader.read_int32()
        payload["coord_y"] = reader.read_int32()
        if _version_at_least(version, AllegroVersion.V_172):
            payload["instance_ref_key"] = reader.read_uint32()
        payload["graphic_key"] = reader.read_uint32()
        payload["first_pad_key"] = reader.read_uint32()
        payload["text_key"] = reader.read_uint32()
        reader.skip(4 * 4)
    elif tag == 0x2E:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["net_assignment_key"] = reader.read_uint32()
        reader.skip(5 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
    elif tag == 0x2F:
        reader.skip(3)
        key = reader.read_uint32()
        reader.skip(6 * 4)
    elif tag == 0x30:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172, count=2)
        if _version_at_least(version, AllegroVersion.V_172):
            payload["font_key"] = reader.read_uint8()
            payload["font_flags"] = reader.read_uint8()
            payload["text_alignment_code"] = reader.read_uint8()
            payload["text_reversal_code"] = reader.read_uint8()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        payload["string_graphic_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(4)
            payload["font_key"] = reader.read_uint8()
            payload["font_flags"] = reader.read_uint8()
            payload["text_alignment_code"] = reader.read_uint8()
            payload["text_reversal_code"] = reader.read_uint8()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["x"] = reader.read_int32()
        payload["y"] = reader.read_int32()
        reader.skip(4)
        payload["rotation_mdeg"] = reader.read_uint32()
        if _version_lt(version, AllegroVersion.V_172):
            reader.skip(4)
    elif tag == 0x31:
        reader.skip(1)
        payload["string_layer_code"] = reader.read_uint16()
        key = reader.read_uint32()
        payload["text_wrapper_key"] = reader.read_uint32()
        payload["x"] = reader.read_int32()
        payload["y"] = reader.read_int32()
        reader.skip(2)
        length = reader.read_uint16()
        _require_dynamic_count(reader, length, offset=offset, label="0x31 string")
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        payload["text_length"] = length
        payload["text"] = reader.read_fixed_string(length)
    elif tag == 0x32:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["net_key"] = reader.read_uint32()
        payload["flags"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["next_in_footprint_key"] = reader.read_uint32()
        payload["parent_footprint_key"] = reader.read_uint32()
        payload["track_key"] = reader.read_uint32()
        payload["pad_definition_key"] = reader.read_uint32()
        payload["auxiliary_key"] = reader.read_uint32()
        payload["ratline_key"] = reader.read_uint32()
        payload["pin_number_key"] = reader.read_uint32()
        payload["next_in_component_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["name_text_key"] = reader.read_uint32()
        payload["secondary_key"] = reader.read_uint32()
        payload["coord_x"] = reader.read_int32()
        payload["coord_y"] = reader.read_int32()
        payload["coord_2_x"] = reader.read_int32()
        payload["coord_2_y"] = reader.read_int32()
    elif tag == 0x33:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["net_key"] = reader.read_uint32()
        payload["flags"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["connection_key"] = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["coord_x"] = reader.read_int32()
        payload["coord_y"] = reader.read_int32()
        payload["track_key"] = reader.read_uint32()
        payload["padstack_key"] = reader.read_uint32()
        if _version_lt(version, AllegroVersion.V_172):
            # Confirmed 0x33 tail on V16.x (V16.5/16.6): a label text-wrapper
            # key, an always-zero word, via flags, rotation (mdeg, right angles
            # only observed), then a centered bbox. The layout on V17.2+ shifts
            # (extra conditional words earlier in the record), so leave the tail
            # opaque there rather than guess -- keep the byte count identical.
            payload["label_key"] = reader.read_uint32()
            reader.skip(4)
            payload["via_flags"] = reader.read_uint32()
            payload["rotation_mdeg"] = reader.read_uint32()
            payload["bbox"] = (
                reader.read_int32(),
                reader.read_int32(),
                reader.read_int32(),
                reader.read_int32(),
            )
        else:
            reader.skip(4 * 4 + 4 * 4)
    elif tag == 0x34:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        reader.skip(4)
        payload["first_segment_key"] = reader.read_uint32()
        reader.skip(2 * 4)
    elif tag == 0x35:
        reader.skip(1 + 2)
        payload["content"] = reader.read_fixed_string(120)
    elif tag == 0x36:
        key, next_key = _parse_definition_table_record(
            reader, version=version, payload=payload, offset=offset
        )
    elif tag == 0x37:
        reader.skip(3)
        key = reader.read_uint32()
        payload["group_key"] = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["capacity"] = reader.read_uint32()
        payload["count"] = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        reader.skip(100 * 4)
    elif tag == 0x38:
        reader.skip(3)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        payload["layer_list_key"] = reader.read_uint32()
        if _version_lt(version, AllegroVersion.V_166):
            payload["film_name"] = reader.read_fixed_string(20)
        if _version_at_least(version, AllegroVersion.V_166):
            payload["layer_name_key"] = reader.read_uint32()
            reader.skip(4)
        reader.skip(7 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x39:
        reader.skip(3)
        key = reader.read_uint32()
        payload["parent_key"] = reader.read_uint32()
        payload["head_key"] = reader.read_uint32()
        reader.skip(22 * 2)
    elif tag == 0x3A:
        reader.skip(1)
        _read_layer_info(reader, payload)
        key = reader.read_uint32()
        next_key = reader.read_uint32()
        reader.skip(4)
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
    elif tag == 0x3B:
        reader.skip(3)
        length = reader.read_uint32()
        _require_dynamic_count(reader, length, offset=offset, label="0x3B value")
        payload["name"] = reader.read_fixed_string(128)
        payload["value_type"] = reader.read_fixed_string(32)
        reader.skip(2 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_172)
        payload["value_length"] = length
        reader.skip(length)
    elif tag == 0x3C:
        reader.skip(3)
        key = reader.read_uint32()
        _skip_cond_u32(reader, version, AllegroVersion.V_174)
        num_entries = reader.read_uint32()
        _require_dynamic_count(reader, num_entries, offset=offset, label="0x3C entry")
        payload["entry_count"] = num_entries
        reader.skip(num_entries * 4)
    else:
        raise AllegroParseError(
            f"unknown Allegro record tag 0x{tag:02X}; record lengths are implicit",
            code="unknown-record-tag",
            offset=offset,
            source_name=reader.source_name,
        )

    return AllegroRecord(
        tag=tag,
        offset=offset,
        end_offset=reader.offset,
        key=key,
        next_key=next_key,
        payload=MappingProxyType(payload),
    )


def _parse_padstack_record(
    reader: BoundedBinaryReader,
    *,
    version: AllegroVersion,
    payload: dict[str, AllegroPayloadValue],
    offset: int,
) -> tuple[int, int]:
    reader.skip(1)
    variable_count = reader.read_uint8()
    reader.skip(1)
    key = reader.read_uint32()
    next_key = reader.read_uint32()
    payload["pad_name_key"] = reader.read_uint32()

    if _version_lt(version, AllegroVersion.V_172):
        payload["drill_size"] = reader.read_uint32()
        reader.skip(5 * 4)
        payload["drill_mark_shape"] = reader.read_uint8()
        flags = reader.read_uint8()
        payload["flags"] = flags
        payload["plated"] = 1 if flags & 0x01 else 0
        payload["drill_char"] = reader.read_uint8()
        reader.skip(1)
        payload["pad_type_code"] = reader.read_uint16()
        reader.skip(2 + 2)
        layer_count = reader.read_uint16()
        reader.skip(5 * 4)
        payload["slot_x"] = reader.read_uint32()
        payload["slot_y"] = reader.read_uint32()
        reader.skip(1 * 4)
        _skip_cond_u32(reader, version, AllegroVersion.V_165)
    else:
        reader.skip(3 * 4)
        pad_type_and_a = reader.read_uint8()
        payload["pad_type_code"] = pad_type_and_a & 0xF0
        reader.skip(1)
        flags = reader.read_uint8()
        payload["flags"] = flags
        payload["plated"] = 1 if flags & 0x20 else 0
        reader.skip(1)
        reader.skip(2 * 4)
        reader.skip(2 + 2)
        layer_count = reader.read_uint16()
        reader.skip(2)
        reader.skip(4 * 4)
        payload["drill_size"] = reader.read_uint32()
        reader.skip(2 * 4)
        payload["slot_x"] = reader.read_uint32()
        payload["slot_y"] = reader.read_uint32()
        reader.skip(2 * 4)
        reader.skip(21 * 4)
        if _version_at_least(version, AllegroVersion.V_180):
            reader.skip(8 * 4)

    if layer_count > _MAX_PADSTACK_LAYER_COUNT:
        raise AllegroParseError(
            f"padstack layer count {layer_count} exceeds {_MAX_PADSTACK_LAYER_COUNT}",
            code="record-value-out-of-range",
            offset=offset,
            source_name=reader.source_name,
        )
    _require_dynamic_count(reader, variable_count, offset=offset, label="0x1C variable")
    payload["layer_count"] = layer_count
    payload["variable_count"] = variable_count

    if _version_lt(version, AllegroVersion.V_165):
        fixed_component_count = 10
    elif _version_lt(version, AllegroVersion.V_172):
        fixed_component_count = 11
    else:
        fixed_component_count = 21
    components_per_layer = 3 if _version_lt(version, AllegroVersion.V_172) else 4
    component_count = fixed_component_count + layer_count * components_per_layer
    payload["fixed_component_count"] = fixed_component_count
    payload["components_per_layer"] = components_per_layer
    payload["component_count"] = component_count

    components: list[AllegroPadstackComponent] = []
    for index in range(component_count):
        component_type = reader.read_uint8()
        reader.skip(3)
        z1 = None
        if _version_at_least(version, AllegroVersion.V_172):
            reader.skip(4)
        width = reader.read_int32()
        height = reader.read_int32()
        if _version_at_least(version, AllegroVersion.V_172):
            z1 = reader.read_int32()
        offset_x = reader.read_int32()
        offset_y = reader.read_int32()
        string_key = reader.read_uint32()
        z2 = None
        if _version_at_least(version, AllegroVersion.V_172) or index < component_count - 1:
            z2 = reader.read_uint32()
        components.append(
            AllegroPadstackComponent(
                index=index,
                component_type=component_type,
                width=width,
                height=height,
                offset_x=offset_x,
                offset_y=offset_y,
                string_key=string_key,
                z1=z1,
                z2=z2,
            )
        )
    payload["components"] = tuple(components)

    unknown_entry_count = variable_count * (
        10 if _version_at_least(version, AllegroVersion.V_172) else 8
    )
    reader.skip(unknown_entry_count * 4)
    return key, next_key


def _parse_definition_table_record(
    reader: BoundedBinaryReader,
    *,
    version: AllegroVersion,
    payload: dict[str, AllegroPayloadValue],
    offset: int,
) -> tuple[int, int]:
    reader.skip(1)
    code = reader.read_uint16()
    key = reader.read_uint32()
    next_key = reader.read_uint32()
    _skip_cond_u32(reader, version, AllegroVersion.V_172)
    num_items = reader.read_uint32()
    count = reader.read_uint32()
    reader.skip(2 * 4)
    _skip_cond_u32(reader, version, AllegroVersion.V_174)

    _require_dynamic_count(reader, num_items, offset=offset, label="0x36 item")
    if count > num_items:
        raise AllegroParseError(
            f"definition table filled count {count} exceeds capacity {num_items}",
            code="record-value-out-of-range",
            offset=offset,
            source_name=reader.source_name,
        )
    payload["code"] = code
    payload["item_count"] = num_items
    payload["filled_count"] = count

    text_parameter_items: list[tuple[int, ...]] = []
    for _ in range(num_items):
        if code == 0x02:
            reader.skip(32 + 14 * 4)
            if _version_at_least(version, AllegroVersion.V_164):
                reader.skip(3 * 4)
            if _version_at_least(version, AllegroVersion.V_172):
                reader.skip(2 * 4)
        elif code == 0x03:
            reader.skip(64 if _version_at_least(version, AllegroVersion.V_172) else 32)
            _skip_cond_u32(reader, version, AllegroVersion.V_174)
        elif code == 0x05:
            reader.skip(28)
            _skip_cond_u32(reader, version, AllegroVersion.V_174)
        elif code == 0x06:
            reader.skip(2 + 1 + 1 + 4)
            if _version_lt(version, AllegroVersion.V_172):
                reader.skip(50 * 4)
        elif code == 0x08:
            # Text-parameter table. The leading 8 int32 hold char height/width,
            # inter-char and line spacing, and stroke width in native units.
            text_parameter_items.append(
                tuple(reader.read_int32() for _ in range(_TEXT_PARAM_LEADING_INT32))
            )
            if _version_at_least(version, AllegroVersion.V_174):
                reader.skip(36)
            elif _version_at_least(version, AllegroVersion.V_172):
                reader.skip(32)
        elif code == 0x0B:
            reader.skip(1016)
        elif code == 0x0C:
            reader.skip(232)
        elif code == 0x0D:
            reader.skip(200)
        elif code == 0x0F:
            reader.skip(5 * 4)
        elif code == 0x10:
            reader.skip(108)
            _skip_cond_u32(reader, version, AllegroVersion.V_180)
        elif code == 0x12:
            reader.skip(1052)
        else:
            raise AllegroParseError(
                f"unknown 0x36 definition table code 0x{code:02X}; item length is implicit",
                code="record-value-out-of-range",
                offset=offset,
                source_name=reader.source_name,
            )

    if code == 0x08:
        payload["text_parameter_items"] = tuple(text_parameter_items)

    return key, next_key


def _parse_field_substruct(
    reader: BoundedBinaryReader,
    *,
    subtype: int,
    size: int,
    payload: dict[str, AllegroPayloadValue],
    offset: int,
) -> None:
    _require_dynamic_count(reader, size, offset=offset, label="0x03 field")
    start_offset = reader.offset
    if subtype == 0x65:
        pass
    elif subtype in {0x64, 0x66, 0x67, 0x6A}:
        payload["value"] = reader.read_uint32()
    elif subtype == 0x69:
        payload["value"] = (reader.read_uint32(), reader.read_uint32())
    elif subtype in {0x68, 0x6B, 0x6D, 0x6E, 0x6F, 0x71, 0x73, 0x78}:
        payload["value"] = _decode_fixed_string(reader.read_bytes(size))
    elif subtype == 0x6C:
        entry_count = reader.read_uint32()
        _require_dynamic_count(reader, entry_count, offset=offset, label="0x03 subtype 0x6C")
        payload["value"] = tuple(reader.read_uint32() for _ in range(entry_count))
    elif subtype in {0x70, 0x74}:
        x0 = reader.read_uint16()
        x1 = reader.read_uint16()
        entry_bytes = x1 + 4 * x0
        _require_dynamic_count(reader, entry_bytes, offset=offset, label="0x03 subtype 0x70")
        payload["value"] = reader.read_bytes(entry_bytes)
    elif subtype == 0xF6:
        payload["value"] = tuple(reader.read_uint32() for _ in range(20))
    elif size == 4:
        payload["value"] = reader.read_uint32()
    elif size == 8:
        payload["value"] = (reader.read_uint32(), reader.read_uint32())
    else:
        raise AllegroParseError(
            f"unknown 0x03 subtype 0x{subtype:02X} with size {size}",
            code="record-length-invalid",
            offset=offset,
            source_name=reader.source_name,
        )
    if size != 0:
        _require_field_substruct_size(
            reader,
            start_offset=start_offset,
            declared_size=size,
            offset=offset,
            subtype=subtype,
        )


def _decode_fixed_string(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin1")


def _require_field_substruct_size(
    reader: BoundedBinaryReader,
    *,
    start_offset: int,
    declared_size: int,
    offset: int,
    subtype: int,
) -> None:
    consumed = reader.offset - start_offset
    if consumed != declared_size:
        raise AllegroParseError(
            f"0x03 subtype 0x{subtype:02X} consumed {consumed} bytes but declared {declared_size}",
            code="record-length-invalid",
            offset=offset,
            source_name=reader.source_name,
        )


def _require_dynamic_count(
    reader: BoundedBinaryReader, value: int, *, offset: int, label: str
) -> None:
    if value > _MAX_DYNAMIC_RECORD_ITEMS:
        raise AllegroParseError(
            f"{label} count {value} exceeds {_MAX_DYNAMIC_RECORD_ITEMS}",
            code="record-value-out-of-range",
            offset=offset,
            source_name=reader.source_name,
        )


def _read_layer_info(reader: BoundedBinaryReader, payload: dict[str, AllegroPayloadValue]) -> None:
    payload["layer_class_id"] = reader.read_uint8()
    payload["layer_subclass_id"] = reader.read_uint8()


def _skip_cond_u32(
    reader: BoundedBinaryReader,
    version: AllegroVersion,
    minimum: AllegroVersion,
    *,
    count: int = 1,
) -> None:
    if _version_at_least(version, minimum):
        reader.skip(4 * count)


def _version_at_least(version: AllegroVersion, minimum: AllegroVersion) -> bool:
    return version_at_least(version, minimum)


def _version_lt(version: AllegroVersion, minimum: AllegroVersion) -> bool:
    return not version_at_least(version, minimum)


def _align4(offset: int) -> int:
    return (offset + 3) & ~3


def _next_aligned_record_offset(data: bytes, offset: int) -> int | None:
    cursor = _align4(offset)
    while cursor < len(data) and data[cursor] == 0:
        cursor += 4
    if cursor < len(data) and 0 < data[cursor] <= 0x3C:
        return cursor
    return None
