"""Convert an Eagle .sch schematic to the domain model.

Parses Eagle v6+ XML schematics using stdlib xml.etree.ElementTree.
Eagle nets explicitly list pin connections via <pinref> elements,
so no union-find or spatial indexing is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ecad_tools.schematic import Component, Design, Net, Page, Pin, merge_pages

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
) -> list[Page]:
    """Build Page objects from <sheets>."""
    pages: list[Page] = []
    sheets_elem = schematic.find("sheets")
    if sheets_elem is None:
        return pages

    for sheet_idx, sheet_elem in enumerate(sheets_elem.findall("sheet"), 1):
        # Page name from <description> or default
        page_name = _get_description(sheet_elem) or f"Sheet {sheet_idx}"

        page = Page(name=page_name)
        nets_by_name: dict[str, Net] = {}

        # Collect instances on this sheet: part_name -> list of gate_names
        instances_per_part: dict[str, list[str]] = {}
        instances_elem = sheet_elem.find("instances")
        if instances_elem is not None:
            for inst_elem in instances_elem.findall("instance"):
                part_name = inst_elem.get("part", "")
                gate_name = inst_elem.get("gate", "")
                instances_per_part.setdefault(part_name, []).append(gate_name)

        # Parse nets: collect pinrefs across all segments
        # (part_name, gate_name, pin_name) -> net_name
        pinref_map: dict[tuple[str, str, str], str] = {}
        nets_elem = sheet_elem.find("nets")
        if nets_elem is not None:
            for net_elem in nets_elem.findall("net"):
                net_name = net_elem.get("name", "")
                for segment_elem in net_elem.findall("segment"):
                    for pinref_elem in segment_elem.findall("pinref"):
                        pr_part = pinref_elem.get("part", "")
                        pr_gate = pinref_elem.get("gate", "")
                        pr_pin = pinref_elem.get("pin", "")
                        pinref_map[(pr_part, pr_gate, pr_pin)] = net_name

        # Build components — one Component per part, collecting all gates
        for part_name, gate_names in instances_per_part.items():
            part_info = parts.get(part_name)
            if part_info is None:
                continue

            lib_data = libraries.get(part_info.library)
            if lib_data is None:
                continue

            ds_info = lib_data.devicesets.get(part_info.deviceset)
            if ds_info is None:
                continue

            # Skip power/supply symbols and aesthetic parts (frames, fiducials)
            if ds_info.is_supply:
                continue

            metadata: dict[str, str] = {}
            if part_info.value:
                metadata["Value"] = part_info.value

            comp = Component(
                reference=part_name,
                part=f"{part_info.library}:{part_info.deviceset}",
                description=ds_info.description,
                pages=[page],
                metadata=metadata,
            )

            # Add pins from each gate placed on this sheet
            existing_designators: set[str] = set()

            for gate_name in gate_names:
                symbol_name = ds_info.gates.get(gate_name)
                if symbol_name is None:
                    continue

                for pin_def in lib_data.symbols.get(symbol_name, []):
                    # Physical pin designator from connects mapping
                    pad = ds_info.connects.get(
                        (part_info.device, gate_name, pin_def.name), ""
                    )
                    designator = pad or pin_def.name

                    # Skip duplicate designators (shared pins across gates)
                    if designator in existing_designators:
                        continue
                    existing_designators.add(designator)

                    # Resolve net from pinref
                    net_name = pinref_map.get(
                        (part_name, gate_name, pin_def.name)
                    )
                    net: Net | None = None
                    if net_name:
                        if net_name not in nets_by_name:
                            nets_by_name[net_name] = Net(name=net_name)
                        net = nets_by_name[net_name]

                    # Map pin direction to canonical electrical type
                    electrical = _DIRECTION_MAP.get(pin_def.direction, "")
                    pin_meta: dict[str, str] = {}
                    if electrical and electrical != "passive":
                        pin_meta["electrical"] = electrical

                    is_nc = pin_def.direction == "nc"

                    pin = Pin(
                        designator=designator,
                        name=pin_def.name,
                        component=comp,
                        net=net,
                        no_connect=is_nc,
                        metadata=pin_meta,
                    )
                    comp.pins.append(pin)
                    if net is not None:
                        net.pins.append(pin)

            page.components.append(comp)

        page.nets = list(nets_by_name.values())
        pages.append(page)

    return pages


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def eagle_to_design(path: Path, name: str = "") -> Design:
    """Parse an Eagle .sch schematic and return a Design."""
    if not name:
        name = path.stem

    tree = ET.parse(path)  # noqa: S314 — trusted local files only
    root = tree.getroot()

    # Navigate to <eagle><drawing><schematic>
    drawing = root.find("drawing")
    if drawing is None:
        return Design(name=name)

    schematic = drawing.find("schematic")
    if schematic is None:
        return Design(name=name)

    libraries = _parse_libraries(schematic)
    parts = _parse_parts(schematic)
    pages = _build_pages(schematic, libraries, parts)

    if not pages:
        return Design(name=name)

    return merge_pages(name, pages)
