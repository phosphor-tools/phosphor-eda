"""Raw OrCAD Capture schematic view parsing."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.binary_reader import BinaryReader
from phosphor_eda.formats.dsn.raw_models import DsnView

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


def parse_view_schematic(
    data: bytes,
    *,
    stream_path: str,
    hierarchy_stream_paths: list[str],
    ctx: ParseContext | None = None,
) -> DsnView | None:
    """Parse a ``Views/<name>/Schematic`` stream into raw view metadata.

    Layout follows OpenOrCadParser ``StreamSchematic``: schematic prefix,
    preamble, view name, a four-byte unknown field, ordered page names, two
    trailing counted raw tables, and a final four-byte raw value.
    """
    r = BinaryReader(data, stream_path)
    try:
        type_id, _end_offset, _pairs = r.read_prefix_chain()
        r.try_read_preamble()
        name = r.read_string_len_zero()
        unknown_u32 = r.read_uint32()

        page_count = r.read_uint16()
        page_names = [r.read_string_len_zero() for _ in range(page_count)]

        table1_count = r.read_uint16()
        table1_values = [r.read_uint32() for _ in range(table1_count)]

        table2_count = r.read_uint16()
        table2_values = [r.read_bytes(5).hex() for _ in range(table2_count)]

        final_u32 = r.read_uint32()
        if not r.eof():
            msg = f"{stream_path} has {r.remaining()} trailing bytes"
            raise ValueError(msg)
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_view", f"View schematic parse error in {stream_path}: {e}")
        return None

    return DsnView(
        name=name,
        page_names=page_names,
        hierarchy_stream_paths=hierarchy_stream_paths,
        metadata={
            "stream_path": stream_path,
            "structure_type": str(type_id),
            "unknown_u32": str(unknown_u32),
            "raw_table1_u32": ",".join(str(value) for value in table1_values),
            "raw_table2_hex": ",".join(table2_values),
            "final_u32": str(final_u32),
        },
    )


def view_name_from_path(path: str) -> str:
    parts = path.split("/")
    return parts[1] if len(parts) >= 2 and parts[0] == "Views" else ""


def warn_repeated_sheet_identity(
    views: list[DsnView],
    ctx: ParseContext | None,
) -> None:
    # Until a repeated-sheet fixture proves source-page identity, this warning
    # is deliberately only a conservative page-name reuse signal.
    view_names_by_page: dict[str, set[str]] = {}
    for view in views:
        for page_name in view.page_names:
            view_names_by_page.setdefault(page_name, set()).add(view.name)

    for page_name, view_names in sorted(view_names_by_page.items()):
        if len(view_names) < 2:
            continue
        scopes = ", ".join(sorted(view_names))
        warn_optional(
            ctx,
            "dsn_repeated_sheet_identity",
            (
                f"page {page_name!r} appears under multiple schematic views ({scopes}); "
                "this reflects page-name reuse only — confirmed repeated blocks are "
                "resolved with occurrence-scoped page, refdes, and net identity"
            ),
        )
