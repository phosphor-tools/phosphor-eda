"""Parsers for the OrCAD Capture Library stream and OLB/DSN library inventory.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import olefile

from phosphor_eda.formats.common.diagnostics import ParseContext, warn_optional
from phosphor_eda.formats.dsn.binary_reader import PAGE_SETTINGS_SIZE, BinaryReader
from phosphor_eda.formats.dsn.cache import parse_cache_symbols
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.packages import parse_package_stream
from phosphor_eda.formats.dsn.raw_models import (
    DsnCacheSymbols,
    DsnLibraryHeader,
    DsnLibraryInventory,
    DsnLibraryPackageInventory,
    DsnPackage,
)

if TYPE_CHECKING:
    from pathlib import Path


def parse_library(
    data: bytes, ctx: ParseContext | None = None
) -> tuple[DsnLibraryHeader, list[str], list[str]]:
    """Parse the Library stream. Returns (header, string_list, part_fields).

    The Library header and string-list region is the first thing ``parse_dsn``
    reads. Old (OrCAD 9.2-era) files carry a layout this parser misreads and
    unpack past the buffer; convert that into a typed ``DsnFormatError`` so the
    caller reports a bad file rather than crashing with a raw struct error.
    """
    try:
        return _parse_library_stream(data, ctx)
    except (struct.error, IndexError) as exc:
        msg = f"Library stream is truncated or uses an unsupported layout: {exc}"
        raise DsnFormatError(msg, offset=0, type_id=0) from exc


def _parse_library_stream(
    data: bytes, ctx: ParseContext | None
) -> tuple[DsnLibraryHeader, list[str], list[str]]:
    r = BinaryReader(data, "Library")

    # Introduction (32-byte padded string)
    intro_start = r.pos
    intro = r.read_string_zero().rstrip()
    r.pos = intro_start + 32  # pad to 32 bytes

    # Version
    version_major = r.read_uint16()
    version_minor = r.read_uint16()

    # Timestamps (uint32 + uint32 = 8 bytes)
    created_timestamp = r.read_uint32()
    modified_timestamp = r.read_uint32()
    header = DsnLibraryHeader(
        intro=intro,
        version_major=version_major,
        version_minor=version_minor,
        created_timestamp=created_timestamp,
        modified_timestamp=modified_timestamp,
    )

    # Zero padding (4 bytes)
    r.skip(4)

    # Text fonts
    text_font_len = r.read_uint16()
    for _ in range(text_font_len - 1):
        r.skip(60)  # LOGFONTA structure

    # someLen array
    some_len = r.read_uint16()
    r.skip(some_len * 2)  # uint16 array

    # Unknown data
    r.skip(4)  # unknown_2_0
    r.skip(4)  # unknown_2_1

    # Part field mapping (8 strings)
    part_fields: list[str] = []
    for _ in range(8):
        part_fields.append(r.read_string_len_zero())

    # Page settings (fixed 156 bytes)
    r.skip(PAGE_SETTINGS_SIZE)

    # String list — the count is uint16 in some layouts and uint32 in others.
    # Every committed fixture (DSN + OLB) reports the same version_major (3) and
    # a count that fits in uint16 with the u32 high bytes zero, so version does
    # not discriminate the width here; keep the ">100000 ⇒ uint16" heuristic and
    # emit a cross-check diagnostic recording the version whenever it fires, so a
    # future differently-versioned file that needs the other width is visible.
    str_lst_len = r.read_uint32()
    if str_lst_len > 100000:
        # Probably was uint16, rewind and read as uint16
        r.pos -= 4
        str_lst_len = r.read_uint16()
        warn_optional(
            ctx,
            "dsn_library_string_width",
            f"Library string-count {str_lst_len} needed the uint16 fallback "
            f"(version_major={version_major}); verify the width heuristic for this version",
        )

    string_list: list[str] = []
    for _ in range(str_lst_len):
        string_list.append(r.read_string_len_zero())

    return header, string_list, part_fields


def ole_stream_entries(ole: olefile.OleFileIO) -> list[tuple[str, list[str]]]:
    """Enumerate an OLE compound file's streams as ``(path, entry)`` pairs.

    The single source of truth for iterating an OrCAD OLE file: every consumer
    sees the same tuple order (``path`` first) so package/cache/hierarchy
    steps can share the same enumeration.
    """
    return [("/".join(entry), entry) for entry in ole.listdir(streams=True, storages=False)]


def parse_packages_from_ole(
    ole: olefile.OleFileIO,
    ctx: ParseContext | None,
    entries: list[tuple[str, list[str]]],
) -> dict[str, DsnPackage]:
    packages: dict[str, DsnPackage] = {}
    for path, entry in entries:
        if path.startswith("Packages/"):
            package_data = ole.openstream(entry).read()
            package = parse_package_stream(package_data, ctx, path)
            if package is not None:
                packages[path] = package
    return packages


def parse_cache_from_ole(
    ole: olefile.OleFileIO,
    entries: list[tuple[str, list[str]]],
    ctx: ParseContext | None,
) -> DsnCacheSymbols:
    for path, entry in entries:
        if path == "Cache":
            return parse_cache_symbols(ole.openstream(entry).read(), ctx)
    return DsnCacheSymbols(pin_names={}, pins={})


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _package_inventory(package: DsnPackage) -> DsnLibraryPackageInventory:
    source_package_names: list[str] = []
    source_library_references: list[str] = []
    _append_unique(source_package_names, package.name)
    _append_unique(source_library_references, package.source_library)
    for part_cell in package.part_cells:
        _append_unique(source_package_names, part_cell.name)
        _append_unique(source_package_names, part_cell.normal_name)
        _append_unique(source_package_names, part_cell.convert_name)
        for library_part in part_cell.library_parts:
            _append_unique(source_package_names, library_part.name)
            _append_unique(source_library_references, library_part.source_library)
    for library_part in package.library_parts:
        _append_unique(source_package_names, library_part.name)
        _append_unique(source_library_references, library_part.source_library)

    return DsnLibraryPackageInventory(
        stream_path=package.stream_path,
        name=package.name,
        source_package_names=source_package_names,
        source_library_references=source_library_references,
        pcb_footprint=package.pcb_footprint,
        device_count=len(package.devices),
        pin_count=sum(len(device.pins) for device in package.devices),
    )


def parse_library_inventory(
    library_path: Path,
    ctx: ParseContext | None = None,
) -> DsnLibraryInventory:
    """Parse OrCAD DSN/OLB library streams without requiring schematic pages."""
    if ctx is None:
        ctx = ParseContext()

    with olefile.OleFileIO(str(library_path)) as ole:
        entries = ole_stream_entries(ole)
        lib_data = ole.openstream("Library").read()
        library_header, string_list, part_fields = parse_library(lib_data, ctx)
        packages = parse_packages_from_ole(ole, ctx, entries)
        symbol_pin_names = parse_cache_from_ole(ole, entries, ctx).pin_names

    cache_part_names = sorted(symbol_pin_names)
    return DsnLibraryInventory(
        path=str(library_path),
        library_header=library_header,
        string_list=string_list,
        part_fields=part_fields,
        packages=packages,
        package_inventory=[_package_inventory(package) for package in packages.values()],
        cache_part_names=cache_part_names,
        cache_pin_counts={name: len(symbol_pin_names[name]) for name in cache_part_names},
    )
