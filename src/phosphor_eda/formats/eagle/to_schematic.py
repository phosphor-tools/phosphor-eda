"""Convert an Eagle .sch schematic to the domain model.

Parses Eagle v6+ XML schematics using stdlib xml.etree.ElementTree.
Eagle nets explicitly list pin connections via <pinref> elements,
so no union-find or spatial indexing is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from phosphor_eda.domain.schematic import Schematic, ScopeId
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.electrical import (
    EAGLE_DIRECTION_MAP,
    set_pin_electrical,
)
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.resolved_graph import (
    ResolvedComponentOccurrenceInput,
    ResolvedLocalNetInput,
    ResolvedNetInput,
    ResolvedPageInput,
    ResolvedPinInput,
    build_resolved_schematic,
)

if TYPE_CHECKING:
    from pathlib import Path

_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _PinDef:
    """Pin definition from a library symbol."""

    name: str
    direction: str


@dataclass
class _DeviceSetInfo:
    """Parsed deviceset from a library."""

    prefix: str
    description: str
    # gate_name -> symbol_name
    gates: dict[str, str] = field(default_factory=dict)
    # (device_name, gate_name, pin_name) -> pad
    connects: dict[tuple[str, str, str], str] = field(default_factory=dict)
    # True if no device in this deviceset has a package (power/aesthetic symbol)
    is_supply: bool = False


@dataclass
class _LibData:
    """Parsed library data."""

    # symbol_name -> [_PinDef, ...]
    symbols: dict[str, list[_PinDef]] = field(default_factory=dict)
    # deviceset_name -> _DeviceSetInfo
    devicesets: dict[str, _DeviceSetInfo] = field(default_factory=dict)


@dataclass
class _PartInfo:
    """A <part> element."""

    name: str
    library: str
    deviceset: str
    device: str
    value: str
    technology: str


@dataclass(frozen=True, slots=True)
class _InstanceInfo:
    """A placed Eagle gate instance on a schematic sheet."""

    part_name: str
    gate_name: str
    x: float | None
    y: float | None
    rotation: float
    mirror: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_description(elem: ET.Element) -> str:
    """Extract description text from an element, stripping HTML."""
    desc_elem = elem.find("description")
    if desc_elem is None:
        return ""
    text = "".join(desc_elem.itertext())
    # Strip residual HTML tags (from entity-decoded content)
    text = _HTML_TAG_RE.sub("", text)
    # Collapse whitespace
    text = " ".join(text.split())
    return text.strip()


def _sheet_annotations(sheet_elem: ET.Element) -> tuple[str, ...]:
    annotations: list[str] = []
    for text_elem in sheet_elem.findall("./plain/text"):
        text = "".join(text_elem.itertext()).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text or text.startswith(">"):
            continue
        annotations.append(text)
    return tuple(annotations)


def _optional_float(value: str | None) -> float | None:
    if value is None or not value:
        return None
    return float(value)


def _parse_rotation(value: str) -> tuple[float, bool]:
    mirror = value.startswith("M")
    rotation_text = value[1:] if mirror else value
    if rotation_text.startswith("R"):
        return (float(rotation_text[1:] or "0"), mirror)
    return (0.0, mirror)


# ---------------------------------------------------------------------------
# Library parsing
# ---------------------------------------------------------------------------


def _parse_libraries(schematic: ET.Element) -> dict[str, _LibData]:
    """Parse <libraries> into per-library data."""
    result: dict[str, _LibData] = {}
    libs_elem = schematic.find("libraries")
    if libs_elem is None:
        return result

    for lib_elem in libs_elem.findall("library"):
        lib_name = lib_elem.get("name", "")
        lib_data = _LibData()

        # Parse symbols
        symbols_elem = lib_elem.find("symbols")
        if symbols_elem is not None:
            for sym_elem in symbols_elem.findall("symbol"):
                sym_name = sym_elem.get("name", "")
                pins: list[_PinDef] = []
                for pin_elem in sym_elem.findall("pin"):
                    pins.append(
                        _PinDef(
                            name=pin_elem.get("name", ""),
                            direction=pin_elem.get("direction", ""),
                        )
                    )
                lib_data.symbols[sym_name] = pins

        # Parse devicesets
        devicesets_elem = lib_elem.find("devicesets")
        if devicesets_elem is not None:
            for ds_elem in devicesets_elem.findall("deviceset"):
                ds_name = ds_elem.get("name", "")
                ds_info = _DeviceSetInfo(
                    prefix=ds_elem.get("prefix", ""),
                    description=_get_description(ds_elem),
                )

                # Parse gates
                gates_elem = ds_elem.find("gates")
                if gates_elem is not None:
                    for gate_elem in gates_elem.findall("gate"):
                        gate_name = gate_elem.get("name", "")
                        gate_symbol = gate_elem.get("symbol", "")
                        ds_info.gates[gate_name] = gate_symbol

                # Parse devices and their connects
                has_package = False
                devices_elem = ds_elem.find("devices")
                if devices_elem is not None:
                    for dev_elem in devices_elem.findall("device"):
                        dev_name = dev_elem.get("name", "")
                        if dev_elem.get("package", ""):
                            has_package = True
                        connects_elem = dev_elem.find("connects")
                        if connects_elem is not None:
                            for conn_elem in connects_elem.findall("connect"):
                                gate = conn_elem.get("gate", "")
                                pin = conn_elem.get("pin", "")
                                pad = conn_elem.get("pad", "")
                                ds_info.connects[(dev_name, gate, pin)] = pad

                # Supply/power symbols have no physical package
                ds_info.is_supply = not has_package
                lib_data.devicesets[ds_name] = ds_info

        result[lib_name] = lib_data

    return result


# ---------------------------------------------------------------------------
# Parts parsing
# ---------------------------------------------------------------------------


def _parse_parts(schematic: ET.Element) -> dict[str, _PartInfo]:
    """Parse <parts> into a lookup dict."""
    result: dict[str, _PartInfo] = {}
    parts_elem = schematic.find("parts")
    if parts_elem is None:
        return result

    for part_elem in parts_elem.findall("part"):
        name = part_elem.get("name", "")
        result[name] = _PartInfo(
            name=name,
            library=part_elem.get("library", ""),
            deviceset=part_elem.get("deviceset", ""),
            device=part_elem.get("device", ""),
            value=part_elem.get("value", ""),
            technology=part_elem.get("technology", ""),
        )

    return result


# ---------------------------------------------------------------------------
# Sheet building
# ---------------------------------------------------------------------------


def _build_pages(
    name: str,
    schematic: ET.Element,
    libraries: dict[str, _LibData],
    parts: dict[str, _PartInfo],
    ctx: ParseContext | None = None,
) -> Schematic:
    """Build the public schematic graph from Eagle sheets.

    Eagle net names are global: net segments with the same name are one
    electrical net even across sheets. That is Eagle-specific source evidence,
    not a generic same-name page merge.
    """
    page_inputs: list[ResolvedPageInput] = []
    local_net_inputs: list[ResolvedLocalNetInput] = []
    pin_inputs: list[ResolvedPinInput] = []
    local_net_ids_by_name: dict[str, list[str]] = {}
    net_name_by_local_id: dict[str, str] = {}
    pin_occurrences_seen: set[tuple[str, str, str]] = set()

    sheets_elem = schematic.find("sheets")
    if sheets_elem is None:
        return Schematic(name=name)

    for sheet_idx, sheet_elem in enumerate(sheets_elem.findall("sheet"), 1):
        # Page name from <description> or default
        page_name = _get_description(sheet_elem) or f"Sheet {sheet_idx}"
        scope_id = ScopeId(path=(f"sheet-{sheet_idx}",))
        page_id = f"eagle:page:{sheet_idx:04d}"
        page_inputs.append(
            ResolvedPageInput(
                id=page_id,
                name=page_name,
                scope_id=scope_id,
                annotations=_sheet_annotations(sheet_elem),
            )
        )

        # Collect instances on this sheet: part_name -> list of gate_names
        instances_per_part: dict[str, list[_InstanceInfo]] = {}
        instances_elem = sheet_elem.find("instances")
        if instances_elem is not None:
            for inst_elem in instances_elem.findall("instance"):
                part_name = inst_elem.get("part", "")
                gate_name = inst_elem.get("gate", "")
                rotation, mirror = _parse_rotation(inst_elem.get("rot", ""))
                instances_per_part.setdefault(part_name, []).append(
                    _InstanceInfo(
                        part_name=part_name,
                        gate_name=gate_name,
                        x=_optional_float(inst_elem.get("x")),
                        y=_optional_float(inst_elem.get("y")),
                        rotation=rotation,
                        mirror=mirror,
                    )
                )

        # Parse nets: collect pinrefs across all segments
        # (part_name, gate_name, pin_name) -> sheet-local net id
        pinref_map: dict[tuple[str, str, str], str] = {}
        nets_elem = sheet_elem.find("nets")
        if nets_elem is not None:
            for net_idx, net_elem in enumerate(nets_elem.findall("net"), 1):
                net_name = net_elem.get("name", "")
                pinrefs: list[tuple[str, str, str]] = []
                for segment_elem in net_elem.findall("segment"):
                    for pinref_elem in segment_elem.findall("pinref"):
                        pr_part = pinref_elem.get("part", "")
                        pr_gate = pinref_elem.get("gate", "")
                        pr_pin = pinref_elem.get("pin", "")
                        pinrefs.append((pr_part, pr_gate, pr_pin))
                if net_name and pinrefs:
                    local_net_id = f"{page_id}:net:{net_idx:04d}"
                    local_net_inputs.append(
                        ResolvedLocalNetInput(
                            id=local_net_id,
                            scope_id=scope_id,
                            source_names=frozenset({net_name}),
                        )
                    )
                    local_net_ids_by_name.setdefault(net_name, []).append(local_net_id)
                    net_name_by_local_id[local_net_id] = net_name
                    for pinref in pinrefs:
                        pinref_map[pinref] = local_net_id

        # Build components — one Component per part, collecting all gates
        for part_name, instances in instances_per_part.items():
            part_info = parts.get(part_name)
            if part_info is None:
                if ctx is not None:
                    ctx.warn(
                        "eagle_missing_part",
                        f"{part_name}: no <part> definition; instance dropped",
                    )
                continue

            lib_data = libraries.get(part_info.library)
            if lib_data is None:
                if ctx is not None:
                    ctx.warn(
                        "eagle_missing_library",
                        f"{part_name}: library {part_info.library!r} not found; part dropped",
                    )
                continue

            ds_info = lib_data.devicesets.get(part_info.deviceset)
            if ds_info is None:
                if ctx is not None:
                    ctx.warn(
                        "eagle_missing_deviceset",
                        f"{part_name}: deviceset {part_info.deviceset!r} not found in "
                        f"library {part_info.library!r}; part dropped",
                    )
                continue

            metadata: dict[str, str] = {}
            if ds_info.is_supply:
                metadata["is_power_symbol"] = "true"
            if part_info.value:
                metadata["Value"] = part_info.value

            component_id = f"eagle:component:{part_name}"

            # Add pins from each gate placed on this sheet
            for instance in instances:
                occurrence_source_id = (
                    f"{page_id}:instance:{instance.part_name}:{instance.gate_name}"
                )

                symbol_name = ds_info.gates.get(instance.gate_name)
                if symbol_name is None:
                    continue

                for pin_def in lib_data.symbols.get(symbol_name, []):
                    # Physical pin designator from connects mapping
                    pad = ds_info.connects.get(
                        (part_info.device, instance.gate_name, pin_def.name),
                        "",
                    )
                    designator = pad or pin_def.name

                    # Resolve net from pinref
                    local_net_id = pinref_map.get((part_name, instance.gate_name, pin_def.name))

                    # Map pin direction to canonical electrical type
                    pin_meta: dict[str, str] = {}
                    set_pin_electrical(pin_meta, EAGLE_DIRECTION_MAP.get(pin_def.direction))

                    is_nc = pin_def.direction == "nc"

                    pin_source_id = (
                        f"{page_id}:instance:{instance.part_name}:"
                        f"{instance.gate_name}:pin:{pin_def.name}"
                    )
                    pin_id = f"{component_id}:pin:{designator}"
                    pin_occurrence_key = (pin_id, page_id, pin_source_id)
                    if pin_occurrence_key not in pin_occurrences_seen:
                        pin_occurrences_seen.add(pin_occurrence_key)
                        occurrence_metadata = {
                            "eagle_gate": instance.gate_name,
                            "eagle_pin": pin_def.name,
                        }
                        if pad:
                            occurrence_metadata["eagle_pad"] = pad
                        pin_inputs.append(
                            ResolvedPinInput(
                                id=pin_source_id,
                                scope_id=scope_id,
                                local_net_id=local_net_id,
                                component_id=component_id,
                                component_reference=part_name,
                                component_part=f"{part_info.library}:{part_info.deviceset}",
                                component_description=ds_info.description,
                                pin_id=pin_id,
                                pin_designator=designator,
                                pin_name=pin_def.name,
                                no_connect=is_nc,
                                component_occurrence=ResolvedComponentOccurrenceInput(
                                    source_id=occurrence_source_id,
                                    part_id=part_info.value,
                                    x=instance.x,
                                    y=instance.y,
                                    rotation=instance.rotation,
                                    mirror=instance.mirror,
                                    metadata={
                                        "eagle_gate": instance.gate_name,
                                    },
                                ),
                                pin_metadata=pin_meta,
                                pin_occurrence_metadata=occurrence_metadata,
                                component_metadata=metadata,
                            )
                        )

    net_union = NetUnion(local_net.id for local_net in local_net_inputs)
    for local_net_ids in local_net_ids_by_name.values():
        if len(local_net_ids) < 2:
            continue
        first_id = local_net_ids[0]
        for local_net_id in local_net_ids[1:]:
            _ = net_union.union(first_id, local_net_id)

    design_metadata: dict[str, str] = {}
    if ctx is not None and ctx.issues:
        design_metadata["parse_issue_count"] = str(len(ctx.issues))

    return build_resolved_schematic(
        name=name,
        pages=page_inputs,
        local_nets=local_net_inputs,
        pins=pin_inputs,
        net_union=net_union,
        net_factory=lambda net_index, _root_id, group_local_nets: _eagle_net_input_for_group(
            net_index,
            net_name_by_local_id,
            group_local_nets,
        ),
        include_net=_include_eagle_net,
        metadata=design_metadata,
    )


def _eagle_net_input_for_group(
    net_index: int,
    net_name_by_local_id: dict[str, str],
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    name = next(
        (net_name_by_local_id[local_net.id] for local_net in group_local_nets),
        "__auto_net",
    )
    return ResolvedNetInput(
        id=f"eagle:net:{net_index:04d}",
        name=name,
        metadata={
            "eagle_net_name": name,
        },
    )


def _include_eagle_net(
    _root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
    pins: tuple[ResolvedPinInput, ...],
) -> bool:
    group_local_net_ids = {local_net.id for local_net in group_local_nets}
    return any(pin.local_net_id in group_local_net_ids for pin in pins)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def eagle_to_design(path: Path, name: str = "") -> Schematic:
    """Parse an Eagle .sch schematic and return a Schematic."""
    if not name:
        name = path.stem

    tree = ET.parse(path)  # noqa: S314 — trusted local files only
    root = tree.getroot()

    # Navigate to <eagle><drawing><schematic>
    drawing = root.find("drawing")
    if drawing is None:
        return Schematic(name=name)

    schematic = drawing.find("schematic")
    if schematic is None:
        return Schematic(name=name)

    ctx = ParseContext()
    libraries = _parse_libraries(schematic)
    parts = _parse_parts(schematic)
    design = _build_pages(name, schematic, libraries, parts, ctx)

    if not design.pages:
        return Schematic(name=name)

    return design
