"""Extract KiCad-native schematic source objects.

The source extractor keeps KiCad labels, sheet pins, sheet instances, power
symbols, and local wire groups separate. It intentionally does not construct
the public schematic graph; the resolver converts these source objects into
the public ``Schematic`` model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import phosphor_eda.formats.common.sexp as sexp
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.kicad.pro_parser import parse_kicad_text_variables
from phosphor_eda.formats.kicad.resolver import resolve_kicad_source
from phosphor_eda.formats.kicad.sheet_loader import (
    ParseContextSheetWarningReporter,
    load_sheet_tree,
)
from phosphor_eda.formats.kicad.source import (
    KiCadBusAlias,
    KiCadBusEntry,
    KiCadBusLabel,
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetAnnotation,
    KiCadSheetPin,
    KiCadSheetSymbol,
    KiCadSourceDesign,
)
from phosphor_eda.formats.kicad.source_extractor import extract_sheet_sources

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.formats.kicad.sheet_loader import LoadedSheetTree


def kicad_to_design(path: Path, name: str = "") -> Schematic:
    """Parse a KiCad schematic into the public model."""
    ctx = ParseContext()
    source = kicad_to_source(path, name, ctx)
    return resolve_kicad_source(source, ctx)


def kicad_to_source(
    path: Path, name: str = "", ctx: ParseContext | None = None
) -> KiCadSourceDesign:
    """Extract KiCad-native source connectivity from a root schematic file.

    Missing or cyclic child-sheet references are recorded on *ctx* when
    provided, mirroring the other parser pipelines.
    """
    reporter = ParseContextSheetWarningReporter(ctx) if ctx is not None else None
    sheet_tree = load_sheet_tree(path, name, reporter)

    local_nets: list[KiCadLocalNet] = []
    pin_occurrences: list[KiCadPinOccurrence] = []
    local_labels: list[KiCadLocalLabel] = []
    global_labels: list[KiCadGlobalLabel] = []
    hierarchical_labels: list[KiCadHierarchicalLabel] = []
    bus_labels: list[KiCadBusLabel] = []
    bus_aliases: list[KiCadBusAlias] = []
    bus_entries: list[KiCadBusEntry] = []
    power_symbols: list[KiCadPowerSymbol] = []
    sheet_symbols: list[KiCadSheetSymbol] = []
    sheet_pins: list[KiCadSheetPin] = []
    annotations: list[KiCadSheetAnnotation] = []

    # Sheet symbols/pins reference child scopes by UUID, but a child whose file
    # was missing or formed a cycle is never loaded and so has no scope. Drop
    # those dangling references so resolution does not see a sheet pin pointing
    # at a non-existent child page.
    loaded_scopes = {instance.scope_id for instance in sheet_tree.sheet_instances}
    root_uuid = _root_uuid(sheet_tree)
    text_variables = _project_text_variables(path)

    for loaded in sheet_tree.sheets:
        extracted = extract_sheet_sources(
            loaded,
            sheet_tree.lib_pins,
            sheet_tree.lib_descs,
            sheet_tree.lib_power_kinds,
            loaded_scopes,
            root_uuid,
            text_variables,
            ctx,
        )
        local_nets.extend(extracted.local_nets)
        pin_occurrences.extend(extracted.pin_occurrences)
        local_labels.extend(extracted.local_labels)
        global_labels.extend(extracted.global_labels)
        hierarchical_labels.extend(extracted.hierarchical_labels)
        bus_labels.extend(extracted.bus_labels)
        bus_aliases.extend(extracted.bus_aliases)
        bus_entries.extend(extracted.bus_entries)
        power_symbols.extend(extracted.power_symbols)
        sheet_symbols.extend(extracted.sheet_symbols)
        sheet_pins.extend(extracted.sheet_pins)
        annotations.extend(extracted.annotations)

    return KiCadSourceDesign(
        name=name or path.stem,
        root_source_file=str(path),
        root_scope_id=sheet_tree.root_scope_id,
        sheet_instances=sheet_tree.sheet_instances,
        local_nets=local_nets,
        pin_occurrences=pin_occurrences,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        bus_labels=bus_labels,
        bus_aliases=bus_aliases,
        bus_entries=bus_entries,
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
        annotations=annotations,
        schematic_version=_root_version(sheet_tree),
    )


def _root_uuid(sheet_tree: LoadedSheetTree) -> str:
    """The root schematic's uuid — the first segment of every instance path."""
    for loaded in sheet_tree.sheets:
        if loaded.instance.scope_id == sheet_tree.root_scope_id:
            uuid_node = sexp.find(list(loaded.data[1:]), "uuid")
            return sexp.val(uuid_node) if uuid_node is not None else ""
    return ""


def _root_version(sheet_tree: LoadedSheetTree) -> int:
    """The integer KiCad schematic file version from the root sheet."""
    for loaded in sheet_tree.sheets:
        if loaded.instance.scope_id != sheet_tree.root_scope_id:
            continue
        version_node = sexp.find(list(loaded.data[1:]), "version")
        if version_node is None:
            return 0
        try:
            return int(sexp.val(version_node))
        except ValueError:
            return 0
    return 0


def _project_text_variables(path: Path) -> dict[str, str]:
    """Read sibling KiCad project text variables when a project file exists."""
    project_path = path.with_suffix(".kicad_pro")
    return parse_kicad_text_variables(project_path) if project_path.exists() else {}
