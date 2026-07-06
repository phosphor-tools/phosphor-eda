"""Parser for OrCAD Capture ``Packages/*`` streams.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.binary_reader import (
    STRUCT_DEVICE,
    STRUCT_LIBRARY_PART,
    STRUCT_PACKAGE,
    STRUCT_PART_CELL,
    BinaryReader,
)
from phosphor_eda.formats.dsn.raw_models import (
    DsnPackage,
    DsnPackageDevice,
    DsnPackageDevicePin,
    DsnPackageLibraryPart,
    DsnPackagePartCell,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext

_MAX_PACKAGE_PART_CELLS = 4096
_MAX_PACKAGE_LIBRARY_PARTS = 4096
_MAX_PACKAGE_DEVICES = 4096
_MAX_PACKAGE_DEVICE_PINS = 4096


class DsnPackageStreamError(ValueError):
    """A non-fatal Packages/* layout error with the most useful byte offset."""

    def __init__(self, offset: int, message: str) -> None:
        super().__init__(message)
        self.offset = offset


def _package_parse_error(
    ctx: ParseContext | None,
    stream_path: str,
    offset: int,
    message: str,
) -> None:
    warn_optional(ctx, "dsn_package_stream", f"{stream_path} at byte offset {offset}: {message}")


def _require_structure_type(type_id: int, expected: int, offset: int) -> None:
    if type_id != expected:
        msg = f"expected structure 0x{expected:02x}, got 0x{type_id:02x}"
        raise DsnPackageStreamError(offset, msg)


def _finish_parsed_package_structure(
    r: BinaryReader,
    end_offset: int,
    structure_name: str,
) -> None:
    if end_offset <= 0:
        return
    if r.pos != end_offset:
        msg = f"{structure_name} parsed to byte {r.pos}, expected end offset {end_offset}"
        raise DsnPackageStreamError(r.pos, msg)


def _parse_package_part_cell(r: BinaryReader) -> DsnPackagePartCell:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_PART_CELL, start_offset)
    r.try_read_preamble()

    part_cell = DsnPackagePartCell(
        ref=r.read_string_len_zero(),
        name=r.read_string_len_zero(),
    )
    view_count = r.read_uint16()
    if view_count == 1:
        part_cell.normal_name = r.read_string_len_zero()
    elif view_count == 2:
        part_cell.normal_name = r.read_string_len_zero()
        part_cell.convert_name = r.read_string_len_zero()
    else:
        msg = f"unsupported part-cell view count {view_count}"
        raise DsnPackageStreamError(start_offset, msg)

    _finish_parsed_package_structure(r, end_offset, "part cell")
    return part_cell


def _parse_package_library_part(r: BinaryReader) -> DsnPackageLibraryPart:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_LIBRARY_PART, start_offset)
    r.try_read_preamble()

    library_part = DsnPackageLibraryPart(
        name=r.read_string_len_zero(),
        source_library=r.read_string_len_zero(),
    )
    if end_offset <= 0:
        msg = "library part lacks an end offset"
        raise DsnPackageStreamError(start_offset, msg)
    if r.pos > end_offset:
        msg = f"library part parsed to byte {r.pos}, expected end offset {end_offset}"
        raise DsnPackageStreamError(r.pos, msg)
    r.pos = end_offset
    return library_part


def _parse_package_device(r: BinaryReader) -> DsnPackageDevice:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_DEVICE, start_offset)
    r.try_read_preamble()

    device = DsnPackageDevice(
        unit_ref=r.read_string_len_zero(),
        refdes_suffix=r.read_string_len_zero(),
    )
    pin_count = r.read_uint16()
    if pin_count > _MAX_PACKAGE_DEVICE_PINS:
        msg = f"implausible device pin count {pin_count}"
        raise DsnPackageStreamError(r.pos - 2, msg)

    for order in range(pin_count):
        pin_offset = r.pos
        string_len = struct.unpack_from("<h", r.data, r.pos)[0]
        if string_len == -1:
            r.read_int16()
            device.pins.append(DsnPackageDevicePin(order=order))
            continue
        package_pin = r.read_string_len_zero()
        pin_group_config = r.read_uint8()
        group_number = pin_group_config & 0x7F
        group = "" if group_number == 127 else str(group_number)
        device.pins.append(
            DsnPackageDevicePin(
                order=order,
                package_pin=package_pin,
                ignored=bool(pin_group_config & 0x80),
                group=group,
            )
        )
        if end_offset > 0 and r.pos > end_offset:
            msg = "device pin overruns its structure"
            raise DsnPackageStreamError(pin_offset, msg)

    _finish_parsed_package_structure(r, end_offset, "device")
    return device


def _parse_final_package(
    r: BinaryReader,
    stream_path: str,
    part_cells: list[DsnPackagePartCell],
    library_parts: list[DsnPackageLibraryPart],
) -> DsnPackage:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_PACKAGE, start_offset)
    r.try_read_preamble()

    package = DsnPackage(
        stream_path=stream_path,
        name=r.read_string_len_zero(),
        source_library=r.read_string_len_zero(),
        refdes_prefix=r.read_string_len_zero(),
        unknown_name=r.read_string_len_zero(),
        pcb_footprint=r.read_string_len_zero(),
        part_cells=part_cells,
        library_parts=library_parts,
    )
    device_count = r.read_uint16()
    if device_count > _MAX_PACKAGE_DEVICES:
        msg = f"implausible device count {device_count}"
        raise DsnPackageStreamError(r.pos - 2, msg)
    for _ in range(device_count):
        package.devices.append(_parse_package_device(r))

    _finish_parsed_package_structure(r, end_offset, "package")
    return package


def parse_package_stream(
    data: bytes,
    ctx: ParseContext | None,
    stream_path: str,
) -> DsnPackage | None:
    """Parse one OrCAD Capture Packages/* stream.

    The parser follows OpenOrCadParser's package stream shape: a counted
    part-cell/library-part section followed by one final Package structure.
    Successful parses must consume the stream exactly to EOF.
    """
    r = BinaryReader(data, stream_path)
    try:
        part_cell_count = r.read_uint16()
        if part_cell_count > _MAX_PACKAGE_PART_CELLS:
            msg = f"implausible part-cell count {part_cell_count}"
            raise DsnPackageStreamError(0, msg)

        part_cells: list[DsnPackagePartCell] = []
        library_parts: list[DsnPackageLibraryPart] = []
        for _ in range(part_cell_count):
            part_cell = _parse_package_part_cell(r)
            part_cells.append(part_cell)
            library_part_count = r.read_uint16()
            if library_part_count > _MAX_PACKAGE_LIBRARY_PARTS:
                msg = f"implausible library-part count {library_part_count}"
                raise DsnPackageStreamError(r.pos - 2, msg)
            for _ in range(library_part_count):
                library_part = _parse_package_library_part(r)
                part_cell.library_parts.append(library_part)
                library_parts.append(library_part)

        package = _parse_final_package(r, stream_path, part_cells, library_parts)
        if not r.eof():
            msg = f"package stream has {r.remaining()} trailing bytes"
            raise DsnPackageStreamError(r.pos, msg)
        return package
    except DsnPackageStreamError as exc:
        _package_parse_error(ctx, stream_path, exc.offset, str(exc))
        return None
    except (struct.error, IndexError, ValueError) as exc:
        _package_parse_error(ctx, stream_path, r.pos, str(exc))
        return None
