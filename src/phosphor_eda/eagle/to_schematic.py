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

from phosphor_eda.schematic import (
    Component,
    ComponentOccurrence,
    Net,
    NetOccurrence,
    Page,
    Pin,
    Schematic,
    ScopeId,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Pin direction mapping (Eagle direction attr -> canonical electrical type)
# ---------------------------------------------------------------------------

_DIRECTION_MAP: dict[str, str] = {
    "pas": "passive",
    "in": "input",
    "out": "output",
    "io": "IO",
    "sup": "power",
    "nc": "no-connect",
    "hiz": "hi-Z",
    "oc": "open-collector",
    "pwr": "power",
}

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


def _append_unique_page(pages: list[Page], page: Page) -> None:
    if any(existing.id == page.id for existing in pages):
        return
    pages.append(page)


def _append_unique_component(components: list[Component], component: Component) -> None:
    if any(existing.id == component.id for existing in components):
        return
    components.append(component)


def _append_unique_net(nets: list[Net], net: Net) -> None:
    if any(existing.id == net.id for existing in nets):
        return
    nets.append(net)


def _append_unique_pin(pins: list[Pin], pin: Pin) -> None:
    if any(existing.id == pin.id for existing in pins):
        return
    pins.append(pin)


def _remove_pin(pins: list[Pin], pin: Pin) -> None:
    pins[:] = [existing for existing in pins if existing.id != pin.id]


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
    schematic: ET.Element,
    libraries: dict[str, _LibData],
    parts: dict[str, _PartInfo],
) -> tuple[list[Page], list[Component], list[Net]]:
    """Build the public schematic graph from Eagle sheets.

    Eagle net names are global: net segments with the same name are one
    electrical net even across sheets. That is Eagle-specific source evidence,
    not a generic same-name page merge.
    """
    pages: list[Page] = []
    components: list[Component] = []
    components_by_name: dict[str, Component] = {}
    nets_by_name: dict[str, Net] = {}
    pins_by_component_designator: dict[tuple[str, str], Pin] = {}
    component_occurrences_seen: set[tuple[str, str, str]] = set()

    sheets_elem = schematic.find("sheets")
    if sheets_elem is None:
        return (pages, components, [])

    for sheet_idx, sheet_elem in enumerate(sheets_elem.findall("sheet"), 1):
        # Page name from <description> or default
        page_name = _get_description(sheet_elem) or f"Sheet {sheet_idx}"
        scope_id = ScopeId(path=(f"sheet-{sheet_idx}",))
        page = Page(
            id=f"eagle:page:{sheet_idx:04d}",
            name=page_name,
            scope_id=scope_id,
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
        # (part_name, gate_name, pin_name) -> net_name
        pinref_map: dict[tuple[str, str, str], str] = {}
        source_net_ids_by_name: dict[str, list[str]] = {}
        nets_elem = sheet_elem.find("nets")
        if nets_elem is not None:
            for net_idx, net_elem in enumerate(nets_elem.findall("net"), 1):
                net_name = net_elem.get("name", "")
                has_pinrefs = False
                for segment_elem in net_elem.findall("segment"):
                    for pinref_elem in segment_elem.findall("pinref"):
                        pr_part = pinref_elem.get("part", "")
                        pr_gate = pinref_elem.get("gate", "")
                        pr_pin = pinref_elem.get("pin", "")
                        has_pinrefs = True
                        pinref_map[(pr_part, pr_gate, pr_pin)] = net_name
                if net_name and has_pinrefs:
                    source_net_ids_by_name.setdefault(net_name, []).append(
                        f"{page.id}:net:{net_idx:04d}"
                    )

        # Build components — one Component per part, collecting all gates
        for part_name, instances in instances_per_part.items():
            part_info = parts.get(part_name)
            if part_info is None:
                continue

            lib_data = libraries.get(part_info.library)
            if lib_data is None:
                continue

            ds_info = lib_data.devicesets.get(part_info.deviceset)
            if ds_info is None:
                continue

            metadata: dict[str, str] = {}
            if ds_info.is_supply:
                metadata["is_power_symbol"] = "true"
            if part_info.value:
                metadata["Value"] = part_info.value

            component_id = f"eagle:component:{part_name}"
            comp = components_by_name.get(part_name)
            if comp is None:
                comp = Component(
                    id=component_id,
                    reference=part_name,
                    part=f"{part_info.library}:{part_info.deviceset}",
                    description=ds_info.description,
                    metadata=metadata,
                )
                components_by_name[part_name] = comp
                components.append(comp)
            else:
                comp.metadata.update(metadata)

            # Add pins from each gate placed on this sheet
            for instance in instances:
                _append_unique_page(comp.pages, page)
                _append_unique_component(page.components, comp)
                occurrence_source_id = (
                    f"{page.id}:instance:{instance.part_name}:{instance.gate_name}"
                )
                occurrence_key = (comp.id, page.id, occurrence_source_id)
                if occurrence_key not in component_occurrences_seen:
                    component_occurrences_seen.add(occurrence_key)
                    comp.occurrences.append(
                        ComponentOccurrence(
                            id=f"{comp.id}:occ:{len(comp.occurrences) + 1:04d}",
                            component=comp,
                            page=page,
                            scope_id=scope_id,
                            source_id=occurrence_source_id,
                            part_id=part_info.value,
                            x=instance.x,
                            y=instance.y,
                            rotation=instance.rotation,
                            mirror=instance.mirror,
                            metadata={
                                "eagle_gate": instance.gate_name,
                            },
                        )
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
                    net_name = pinref_map.get((part_name, instance.gate_name, pin_def.name))
                    net: Net | None = None
                    if net_name:
                        if net_name not in nets_by_name:
                            nets_by_name[net_name] = Net(
                                id=f"eagle:net:{len(nets_by_name) + 1:04d}",
                                name=net_name,
                                metadata={
                                    "eagle_net_name": net_name,
                                },
                            )
                        net = nets_by_name[net_name]

                    # Map pin direction to canonical electrical type
                    electrical = _DIRECTION_MAP.get(pin_def.direction, "")
                    pin_meta: dict[str, str] = {}
                    if electrical and electrical != "passive":
                        pin_meta["electrical"] = electrical

                    is_nc = pin_def.direction == "nc"

                    pin_key = (comp.id, designator)
                    pin = pins_by_component_designator.get(pin_key)
                    if pin is None:
                        pin = Pin(
                            id=f"{comp.id}:pin:{designator}",
                            designator=designator,
                            name=pin_def.name,
                            component=comp,
                            no_connect=is_nc,
                            metadata=pin_meta,
                        )
                        pins_by_component_designator[pin_key] = pin
                        comp.pins.append(pin)
                    else:
                        pin.metadata.update(pin_meta)
                        pin.no_connect = pin.no_connect or is_nc

                    if net is not None:
                        if pin.net is not None and pin.net.id != net.id:
                            _remove_pin(pin.net.pins, pin)
                        pin.net = net
                        _append_unique_pin(net.pins, pin)

        for net_name, source_net_ids in source_net_ids_by_name.items():
            net = nets_by_name.get(net_name)
            if net is None:
                continue
            _append_unique_page(net.pages, page)
            _append_unique_net(page.nets, net)
            for source_net_id in source_net_ids:
                net.occurrences.append(
                    NetOccurrence(
                        id=f"{net.id}:occ:{len(net.occurrences) + 1:04d}",
                        net=net,
                        page=page,
                        scope_id=scope_id,
                        source_local_net_id=source_net_id,
                        source_names={net_name},
                    )
                )

        pages.append(page)

    return (pages, components, list(nets_by_name.values()))


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

    libraries = _parse_libraries(schematic)
    parts = _parse_parts(schematic)
    pages, components, nets = _build_pages(schematic, libraries, parts)

    if not pages:
        return Schematic(name=name)

    return Schematic(
        name=name,
        pages=pages,
        components=components,
        nets=nets,
    )
