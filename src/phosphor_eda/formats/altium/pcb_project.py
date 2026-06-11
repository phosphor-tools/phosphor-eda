"""Project-level enrichment for Altium PcbDoc files.

Parses the Rules6, Classes6, DifferentialPairs6 and Board6 streams into the
domain enrichment models (design rules, net classes, diff pairs, stackup)
and exposes ``load_altium_enrichment(path)`` which owns the OLE-stream
knowledge (stream names, re-open) so callers only orchestrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import olefile

from phosphor_eda.domain.project import DesignRule, DiffPair, NetClass, Stackup, StackupLayer
from phosphor_eda.formats.altium._helpers import u32
from phosphor_eda.formats.altium.pcb_primitives import MIL_TO_MM, read_text_records
from phosphor_eda.formats.altium.record_parser import parse_record_payload

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.common.diagnostics import ParseContext


def _read_rules6_records(data: bytes) -> list[dict[str, str]]:
    """Read Rules6 stream records (2-byte header + 4-byte LE length framing)."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 6 <= len(data):
        # 2-byte header (type + padding) + 4-byte LE length
        length = u32(data, pos + 2)
        pos += 6
        if length == 0 or pos + length > len(data):
            break
        payload = data[pos : pos + length]
        pos += length
        props = parse_record_payload(payload)
        if props:
            records.append(props)
    return records


def parse_altium_rules(data: bytes, ctx: ParseContext | None = None) -> list[DesignRule]:
    """Parse Altium Rules6 stream into DesignRule objects."""
    records = _read_rules6_records(data)
    rules: list[DesignRule] = []
    for props in records:
        name = props.get("name", "")
        kind = props.get("rulekind", "")
        enabled = props.get("enabled", "TRUE").upper() == "TRUE"
        priority = int(props.get("priority", "0") or "0")
        scope1 = props.get("scope1expression", "")
        scope2 = props.get("scope2expression", "")

        # Extract numeric values (may be in mils, convert to mm).
        # Different rule kinds use different property names for their values.
        min_val = _rule_value_mm(
            props,
            "minlimit",
            "gap",
            "genericclearance",
            "clearance",
            "minimumring",
            "minsoldermaskwidth",
            "minsilkscreentomaskgap",
            "minwidth",
            "minholewidth",
            "minheight",
            "minsize",
            ctx=ctx,
        )
        max_val = _rule_value_mm(
            props,
            "maxlimit",
            "maxwidth",
            "maxholewidth",
            "maxheight",
            "maxsize",
            "maxuncoupledlength",
            "tolerance",
            "limit",
            ctx=ctx,
        )
        pref_val = _rule_value_mm(
            props,
            "preferedwidth",
            "preferredwidth",
            "expansion",
            "prefheight",
            "preferedsize",
            "toplayer_prefwidth",
            ctx=ctx,
        )

        # Collect remaining properties
        skip_keys = {
            "name",
            "rulekind",
            "enabled",
            "priority",
            "scope1expression",
            "scope2expression",
            "selection",
            "layer",
            "locked",
            "polygonoutline",
            "userrouted",
            "keepout",
            "unionindex",
            "netscope",
            "layerkind",
            "superclass",
        }
        extra: dict[str, str] = {}
        for k, v in props.items():
            if k not in skip_keys and v:
                extra[k] = v

        rules.append(
            DesignRule(
                name=name,
                kind=kind,
                enabled=enabled,
                priority=priority,
                scope1=scope1,
                scope2=scope2,
                min_value_mm=min_val,
                max_value_mm=max_val,
                preferred_value_mm=pref_val,
                properties=extra,
            )
        )
    return rules


def _rule_value_mm(
    props: dict[str, str], *keys: str, ctx: ParseContext | None = None
) -> float | None:
    """Extract a rule value in mm from property keys (values stored in mils).

    Values may have a "mil" suffix that must be stripped before conversion.
    """
    for key in keys:
        val_str = props.get(key, "")
        if val_str:
            try:
                return float(_strip_mil(val_str)) * MIL_TO_MM
            except ValueError:
                if ctx is not None:
                    ctx.warn(
                        "malformed_rule_value",
                        f"non-numeric Rules6 value {val_str!r} for {key!r}; skipped",
                    )
                continue
    return None


def parse_altium_classes(data: bytes) -> list[NetClass]:
    """Parse Altium Classes6 stream into NetClass objects."""
    records = read_text_records(data)
    classes: list[NetClass] = []
    for props in records:
        name = props.get("name", "")
        kind = int(props.get("kind", "0") or "0")
        # Extract members (M0, M1, M2, ...)
        members: list[str] = []
        i = 0
        while True:
            key = f"m{i}"
            if key in props:
                members.append(props[key])
                i += 1
            else:
                break
        classes.append(NetClass(name=name, kind=kind, members=members))
    return classes


def parse_altium_diff_pairs(data: bytes) -> list[DiffPair]:
    """Parse Altium DifferentialPairs6 stream into DiffPair objects."""
    records = read_text_records(data)
    pairs: list[DiffPair] = []
    for props in records:
        name = props.get("name", "")
        pos_net = props.get("positivenetname", "")
        neg_net = props.get("negativenetname", "")
        if name and pos_net and neg_net:
            pairs.append(DiffPair(name=name, positive_net=pos_net, negative_net=neg_net))
    return pairs


def parse_altium_stackup(
    board_props: dict[str, str], ctx: ParseContext | None = None
) -> Stackup | None:
    """Extract PCB stackup from Board6 properties.

    Prefers the v9 stackup format (v9_stack_layerN_*) which stores explicit
    layer names, correct physical ordering, and separate core/prepreg entries.
    Falls back to the legacy format (layerN + next-pointer chain) for older files.
    """
    stackup = _parse_v9_stackup(board_props, ctx)
    if stackup:
        return stackup
    return _parse_legacy_stackup(board_props)


def _parse_v9_stackup(
    board_props: dict[str, str], ctx: ParseContext | None = None
) -> Stackup | None:
    """Parse the v9 stackup format (Altium Designer 19+).

    v9 layers are stored as v9_stack_layer{N}_* in physical order from top
    to bottom. Includes solder mask, copper, prepreg, and core layers with
    explicit user-assigned names.
    """
    # Discover which v9 layer indices exist
    layer_indices: list[int] = []
    for key in board_props:
        if key.startswith("v9_stack_layer") and key.endswith("_name"):
            try:
                idx = int(key[len("v9_stack_layer") : -len("_name")])
                layer_indices.append(idx)
            except ValueError:
                if ctx is not None:
                    ctx.warn(
                        "malformed_stackup_layer",
                        f"non-integer v9 stackup layer index in {key!r}; skipped",
                    )
                continue

    if not layer_indices:
        return None

    layer_indices.sort()

    layers: list[StackupLayer] = []
    # Track whether we've seen the first and last copper to determine sides
    copper_indices: list[int] = []
    for idx in layer_indices:
        copthick = board_props.get(f"v9_stack_layer{idx}_copthick", "")
        if copthick:
            copper_indices.append(idx)

    first_copper = copper_indices[0] if copper_indices else -1
    last_copper = copper_indices[-1] if copper_indices else -1

    for idx in layer_indices:
        prefix = f"v9_stack_layer{idx}_"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            continue

        copthick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")
        copper_orient = board_props.get(f"{prefix}copperorientation", "")

        if copthick_str:
            # Copper layer
            cop_thick_mm = float(copthick_str) * MIL_TO_MM

            side = ""
            if idx == first_copper:
                side = "front"
            elif idx == last_copper:
                side = "back"

            orientation = ""
            if copper_orient == "1":
                orientation = "reversed"
            elif copper_orient == "0" or (copper_orient == "" and copthick_str):
                orientation = "normal"

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type="copper",
                    thickness_mm=cop_thick_mm,
                    side=side,
                    copper_orientation=orientation,
                )
            )
        elif diel_height_str:
            # Dielectric layer (prepreg, core, or solder mask)
            thickness_mm = float(diel_height_str) * MIL_TO_MM
            epsilon_r = float(diel_const_str) if diel_const_str else 0.0
            loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

            # dieltype: 0=unspecified, 1=core, 2=prepreg, 3=solder_mask
            diel_type_map = {"1": "core", "2": "prepreg", "3": "solder_mask"}
            layer_type = diel_type_map.get(diel_type_raw, "prepreg")

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type=layer_type,
                    thickness_mm=thickness_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )
        # Skip non-physical layers (paste, overlay) that have neither
        # copper thickness nor dielectric height

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _parse_legacy_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Parse the legacy layerN + next-pointer stackup format.

    Used by older Altium files that lack v9_stack_layer data. Follows the
    layer{N}next chain starting at layer 1. Dielectrics are numbered
    sequentially by traversal position.
    """
    layers: list[StackupLayer] = []

    # Follow the next-pointer chain starting at layer 1
    i = 1
    visited: set[int] = set()
    diel_counter = 0
    while i > 0 and i not in visited:
        visited.add(i)
        prefix = f"layer{i}"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            break

        # Copper thickness (value may have "mil" suffix)
        cop_thick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        cop_thick_mm = float(cop_thick_str) * MIL_TO_MM if cop_thick_str else 0.0

        # Dielectric properties
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")

        epsilon_r = float(diel_const_str) if diel_const_str else 0.0
        diel_height_mm = float(diel_height_str) * MIL_TO_MM if diel_height_str else 0.0
        loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

        # Dielectric type mapping
        diel_type_map = {"0": "prepreg", "1": "core", "2": "prepreg"}
        diel_type = diel_type_map.get(diel_type_raw, "prepreg")

        # Determine side
        side = ""
        name_lower = name.lower()
        if "top" in name_lower:
            side = "front"
        elif "bottom" in name_lower or "bot" in name_lower:
            side = "back"

        # Add copper layer
        layers.append(
            StackupLayer(
                name=name,
                layer_type="copper",
                thickness_mm=cop_thick_mm,
                side=side,
            )
        )

        # Follow next pointer
        next_str = board_props.get(f"{prefix}next", "0")
        next_layer = int(next_str) if next_str else 0

        # Add dielectric layer between this copper and the next (skip after last)
        if diel_height_mm > 0 and next_layer > 0:
            diel_counter += 1
            layers.append(
                StackupLayer(
                    name=f"Dielectric {diel_counter}",
                    layer_type=diel_type,
                    thickness_mm=diel_height_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )

        i = next_layer

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _strip_mil(s: str) -> str:
    """Strip 'mil' suffix from an Altium dimension string."""
    s = s.strip()
    if s.lower().endswith("mil"):
        return s[:-3]
    return s


@dataclass(frozen=True)
class AltiumEnrichment:
    """Project-level enrichment parsed from an Altium PcbDoc's side streams."""

    design_rules: list[DesignRule]
    net_classes: list[NetClass]
    diff_pairs: list[DiffPair]
    stackup: Stackup | None


def load_altium_enrichment(path: Path, ctx: ParseContext | None = None) -> AltiumEnrichment:
    """Read and parse the enrichment streams from a .PcbDoc file.

    Owns the OLE-stream knowledge (stream names, re-open) so callers only
    orchestrate: open the document once for the Rules6/Classes6/
    DifferentialPairs6/Board6 streams and turn them into domain enrichment.
    """
    ole = olefile.OleFileIO(str(path))
    try:
        rules_data = ole.openstream("Rules6/Data").read() if ole.exists("Rules6/Data") else b""
        classes_data = (
            ole.openstream("Classes6/Data").read() if ole.exists("Classes6/Data") else b""
        )
        dp_data = (
            ole.openstream("DifferentialPairs6/Data").read()
            if ole.exists("DifferentialPairs6/Data")
            else b""
        )
        board_data = ole.openstream("Board6/Data").read() if ole.exists("Board6/Data") else b""
    finally:
        ole.close()

    design_rules = parse_altium_rules(rules_data, ctx) if rules_data else []
    net_classes = parse_altium_classes(classes_data) if classes_data else []
    diff_pairs = parse_altium_diff_pairs(dp_data) if dp_data else []

    stackup = None
    if board_data:
        records = read_text_records(board_data)
        if records:
            stackup = parse_altium_stackup(records[0], ctx)

    return AltiumEnrichment(
        design_rules=design_rules,
        net_classes=net_classes,
        diff_pairs=diff_pairs,
        stackup=stackup,
    )
