"""Parser for Altium .PrjPcb project files (INI format)."""

import configparser
import re
from dataclasses import dataclass, field
from enum import IntEnum

from phosphor_eda.domain.schematic import FootprintModel, LibraryLink, Parameter, PartNumber
from phosphor_eda.domain.variants import (
    Variant,
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)


class AltiumHierarchyMode(IntEnum):
    """Altium project net identifier scope mode."""

    SMART = 0
    FLAT = 1
    HIERARCHICAL_POWER_GLOBAL = 2
    GLOBAL = 3
    HIERARCHICAL_POWER_LOCAL = 4


@dataclass
class AltiumProjectDocument:
    """One document entry from an Altium project file."""

    path: str
    unique_id: str = ""


@dataclass
class AltiumProject:
    """Parsed Altium project file."""

    hierarchy_mode: AltiumHierarchyMode = AltiumHierarchyMode.FLAT
    allow_port_net_names: bool = False
    allow_sheet_entry_net_names: bool = True
    append_sheet_number_to_local_nets: bool = False
    name_nets_hierarchically: bool = False
    netlist_single_pin_nets: bool = False
    power_port_names_take_priority: bool = False
    current_variant: str = ""
    schematic_paths: list[str] = field(default_factory=list)
    pcb_paths: list[str] = field(default_factory=list)
    schematic_documents: list[AltiumProjectDocument] = field(default_factory=list)
    pcb_documents: list[AltiumProjectDocument] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)


def _get_hierarchy_mode(parser: configparser.ConfigParser) -> AltiumHierarchyMode:
    raw_value = parser.get("Design", "HierarchyMode", fallback=str(AltiumHierarchyMode.FLAT.value))
    try:
        value = int(raw_value)
        return AltiumHierarchyMode(value)
    except ValueError as exc:
        raise ValueError(f"HierarchyMode has unknown value {raw_value}") from exc


def parse_prjpcb(content: str) -> AltiumProject:
    """Parse a .PrjPcb file's text content into an AltiumProject."""
    parser = configparser.ConfigParser(strict=False)
    content = content.lstrip("\ufeff")
    parser.read_string(content)

    project = AltiumProject()

    if parser.has_section("Design"):
        project.hierarchy_mode = _get_hierarchy_mode(parser)
        project.allow_port_net_names = parser.getboolean(
            "Design",
            "AllowPortNetNames",
            fallback=project.allow_port_net_names,
        )
        project.allow_sheet_entry_net_names = parser.getboolean(
            "Design",
            "AllowSheetEntryNetNames",
            fallback=project.allow_sheet_entry_net_names,
        )
        project.append_sheet_number_to_local_nets = parser.getboolean(
            "Design",
            "AppendSheetNumberToLocalNets",
            fallback=project.append_sheet_number_to_local_nets,
        )
        project.name_nets_hierarchically = parser.getboolean(
            "Design",
            "NameNetsHierarchically",
            fallback=project.name_nets_hierarchically,
        )
        project.netlist_single_pin_nets = parser.getboolean(
            "Design",
            "NetlistSinglePinNets",
            fallback=project.netlist_single_pin_nets,
        )
        project.power_port_names_take_priority = parser.getboolean(
            "Design",
            "PowerPortNamesTakePriority",
            fallback=project.power_port_names_take_priority,
        )
        project.current_variant = parser.get("Design", "CurrentVariant", fallback="").strip()

    sections: list[str] = parser.sections()
    for section in sections:
        if section.startswith("Document"):
            doc_path = parser.get(section, "DocumentPath", fallback="")
            document = AltiumProjectDocument(
                path=doc_path,
                unique_id=parser.get(section, "DocumentUniqueId", fallback="").strip(),
            )
            lower = doc_path.lower()
            if lower.endswith(".schdoc"):
                project.schematic_paths.append(doc_path)
                project.schematic_documents.append(document)
            elif lower.endswith(".pcbdoc"):
                project.pcb_paths.append(doc_path)
                project.pcb_documents.append(document)
        elif section.startswith("ProjectVariant"):
            variant = _parse_variant_section(parser, section)
            if variant.name:
                project.variants.append(variant)

    return project


_VARIANT_SECTION_RE = re.compile(r"^ProjectVariant(\d+)$")


def _parse_variant_section(parser: configparser.ConfigParser, section: str) -> Variant:
    match = _VARIANT_SECTION_RE.match(section)
    order = int(match.group(1)) if match else 0
    name = parser.get(section, "Description", fallback="").strip()
    variant = Variant(name=name, order=order, source_id=section)

    variation_count = _as_int(parser.get(section, "VariationCount", fallback="0"))
    for index in range(1, variation_count + 1):
        raw = parser.get(section, f"Variation{index}", fallback="")
        override_parts = _variation_overrides(name, index, raw)
        variant.overrides.extend(override_parts)

    param_count = _as_int(parser.get(section, "ParamVariationCount", fallback="0"))
    for index in range(1, param_count + 1):
        raw = parser.get(section, f"ParamVariation{index}", fallback="")
        designator = parser.get(section, f"ParamDesignator{index}", fallback="").strip()
        override = _parameter_override(name, index, raw, designator)
        if override is not None:
            variant.overrides.append(override)
    return variant


def _variation_overrides(
    variant_name: str,
    index: int,
    raw: str,
) -> list[VariantOverride]:
    fields = _pipe_fields(raw)
    designator = fields.get("Designator", "").strip()
    component_uid = _component_uid(fields.get("UniqueId", ""))
    target = VariantTarget(
        kind=VariantTargetKind.COMPONENT,
        reference=designator,
        source_id=component_uid,
    )
    source_id = f"Variation{index}"
    native_kind = fields.get("Kind", "").strip()
    overrides: list[VariantOverride] = []
    if native_kind == "1":
        overrides.append(
            VariantOverride(
                variant_name=variant_name,
                target=target,
                field=VariantField.FITTED,
                value=False,
                source_id=source_id,
                native_kind="altium_not_fitted",
                metadata=fields,
            )
        )
        overrides.append(
            VariantOverride(
                variant_name=variant_name,
                target=target,
                field=VariantField.EXCLUDE_FROM_BOM,
                value=True,
                source_id=source_id,
                native_kind="altium_not_fitted",
                metadata=fields,
            )
        )
    elif native_kind == "2":
        alternate = fields.get("AlternatePart", "").strip()
        overrides.append(
            VariantOverride(
                variant_name=variant_name,
                target=target,
                field=VariantField.ALTERNATE_PART,
                value=alternate,
                source_id=source_id,
                native_kind="altium_alternate_part",
                metadata=fields,
            )
        )
        lib = _alternate_library_link(fields)
        if lib is not None:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=target,
                    field=VariantField.LIB,
                    value=lib,
                    source_id=source_id,
                    native_kind="altium_alternate_part",
                    metadata=fields,
                )
            )
        footprint = fields.get("AltLibLink_Footprint", "").strip()
        if footprint:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=target,
                    field=VariantField.FOOTPRINTS,
                    value=(FootprintModel(name=footprint, is_current=True),),
                    source_id=source_id,
                    native_kind="altium_alternate_part",
                    metadata=fields,
                )
            )
        part_number = fields.get("AltLibLink_DesignItemID", "").strip()
        if part_number:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=target,
                    field=VariantField.PART_NUMBERS,
                    value=(PartNumber(manufacturer="", number=part_number),),
                    source_id=source_id,
                    native_kind="altium_alternate_part",
                    metadata=fields,
                )
            )
    return overrides


def _parameter_override(
    variant_name: str,
    index: int,
    raw: str,
    designator: str,
) -> VariantOverride | None:
    fields = _pipe_fields(raw)
    name = fields.get("ParameterName", "").strip()
    if not name:
        return None
    value = fields.get("VariantValue", "")
    return VariantOverride(
        variant_name=variant_name,
        target=VariantTarget(
            kind=VariantTargetKind.COMPONENT,
            reference=designator,
            parameter_name=name,
        ),
        field=VariantField.PARAMETER,
        value=Parameter(name=name, value=value, source="altium_variant"),
        source_id=f"ParamVariation{index}",
        native_kind="altium_parameter",
        metadata={**fields, "ParamDesignator": designator},
    )


def _pipe_fields(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in raw.split("|"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _component_uid(unique_id_path: str) -> str:
    parts = [part for part in unique_id_path.split("\\") if part]
    return parts[-1] if parts else ""


def _alternate_library_link(fields: dict[str, str]) -> LibraryLink | None:
    symbol = fields.get("AltLibLink_SymbolReference", "").strip()
    library = (
        fields.get("AltLibLink_SourceLibraryName", "").strip()
        or fields.get("AltLibLink_LibraryIdentifier", "").strip()
    )
    design_item_id = fields.get("AltLibLink_DesignItemID", "").strip()
    if not any((symbol, library, design_item_id)):
        return None
    return LibraryLink(
        symbol=symbol,
        library=library,
        design_item_id=design_item_id,
        source="altium_variant",
    )


def _as_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def parse_prjpcb_file(path: str) -> AltiumProject:
    """Read and parse a .PrjPcb file from disk."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return parse_prjpcb(f.read())
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as f:
            return parse_prjpcb(f.read())
