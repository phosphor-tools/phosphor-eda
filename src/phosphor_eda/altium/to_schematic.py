"""Convert Altium schematics into the public schematic domain model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.altium.project import parse_prjpcb_file
from phosphor_eda.altium.resolver import resolve_altium_source
from phosphor_eda.altium.sheet_builder import SheetRecords, load_sheet
from phosphor_eda.altium.source import (
    AltiumSourceDesign,
    altium_to_source,
    load_project_source_sheets,
)
from phosphor_eda.diagnostics import ParseContext

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.schematic import Schematic


def load_project_sheets(
    path: Path,
    ctx: ParseContext | None = None,
) -> dict[str, SheetRecords]:
    """Load raw typed schematic records for netlist and import compatibility."""
    if ctx is None:
        ctx = ParseContext()
    sheets: dict[str, SheetRecords] = {}

    if path.suffix.lower() == ".prjpcb":
        project = parse_prjpcb_file(str(path))
        project_dir = path.parent
        for rel_path in project.schematic_paths:
            schdoc = project_dir / rel_path.replace("\\", "/")
            if schdoc.exists():
                sheet = load_sheet(str(schdoc), ctx=ctx)
                sheets[sheet.name] = sheet
            else:
                ctx.warn(
                    "missing_sheet",
                    f"Schematic sheet not found: {rel_path} (resolved to {schdoc})",
                )
        return sheets

    sheet = load_sheet(str(path), ctx=ctx)
    sheets[sheet.name] = sheet
    return sheets


def load_project_source(
    path: Path,
    ctx: ParseContext | None = None,
) -> tuple[AltiumSourceDesign, ParseContext]:
    """Load an Altium project as source objects for scoped net resolution."""
    if ctx is None:
        ctx = ParseContext()
    project, sheets = load_project_source_sheets(path, ctx=ctx)
    source = AltiumSourceDesign(
        name=path.stem,
        project=project,
        sheets=sheets,
        root_sheet_name=next(iter(sheets), ""),
    )
    return source, ctx


def altium_to_design(path: Path, name: str = "") -> Schematic:
    """Convert an Altium project into the public schematic domain model."""
    ctx = ParseContext()
    source = altium_to_source(path, name=name, ctx=ctx)
    return resolve_altium_source(source, ctx)
