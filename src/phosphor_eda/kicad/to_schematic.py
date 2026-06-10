"""Extract KiCad-native schematic source objects.

The source extractor keeps KiCad labels, sheet pins, sheet instances, power
symbols, and local wire groups separate. It intentionally does not construct
the public schematic graph; the resolver converts these source objects into
the public ``Schematic`` model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.kicad.resolver import resolve_kicad_source
from phosphor_eda.kicad.sheet_loader import load_sheet_tree
from phosphor_eda.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetPin,
    KiCadSheetSymbol,
    KiCadSourceDesign,
)
from phosphor_eda.kicad.source_extractor import extract_sheet_sources

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.schematic import Schematic


def kicad_to_design(path: Path, name: str = "") -> Schematic:
    """Parse a KiCad schematic into the public model."""
    source = kicad_to_source(path, name)
    return resolve_kicad_source(source)


def kicad_to_source(path: Path, name: str = "") -> KiCadSourceDesign:
    """Extract KiCad-native source connectivity from a root schematic file."""
    sheet_tree = load_sheet_tree(path, name)

    local_nets: list[KiCadLocalNet] = []
    pin_occurrences: list[KiCadPinOccurrence] = []
    local_labels: list[KiCadLocalLabel] = []
    global_labels: list[KiCadGlobalLabel] = []
    hierarchical_labels: list[KiCadHierarchicalLabel] = []
    power_symbols: list[KiCadPowerSymbol] = []
    sheet_symbols: list[KiCadSheetSymbol] = []
    sheet_pins: list[KiCadSheetPin] = []

    # Sheet symbols/pins reference child scopes by UUID, but a child whose file
    # was missing or formed a cycle is never loaded and so has no scope. Drop
    # those dangling references so resolution does not see a sheet pin pointing
    # at a non-existent child page.
    loaded_scopes = {instance.scope_id for instance in sheet_tree.sheet_instances}

    for loaded in sheet_tree.sheets:
        extracted = extract_sheet_sources(
            loaded,
            sheet_tree.lib_pins,
            sheet_tree.lib_descs,
            loaded_scopes,
        )
        local_nets.extend(extracted.local_nets)
        pin_occurrences.extend(extracted.pin_occurrences)
        local_labels.extend(extracted.local_labels)
        global_labels.extend(extracted.global_labels)
        hierarchical_labels.extend(extracted.hierarchical_labels)
        power_symbols.extend(extracted.power_symbols)
        sheet_symbols.extend(extracted.sheet_symbols)
        sheet_pins.extend(extracted.sheet_pins)

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
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
    )
