"""KiCad v9 padstack parsing: per-layer copper geometry and pruning flags.

Grammar (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp): pads and
vias carry an optional ``(padstack (mode front_inner_back|custom)
(layer "name" ...) ...)`` node. The item's base shape/size is the front/outer
copper geometry; ``layer`` entries define the remaining tiers — ``"Inner"``
and ``"B.Cu"`` for ``front_inner_back``, one entry per copper layer (except
F.Cu) for ``custom``. Pad entries carry ``(shape ...)``, ``(size w h)``,
``(offset x y)``, and ``(roundrect_rratio r)``; via entries carry only a
single-value ``(size d)`` diameter.

Copper pruning sits next to the padstack node on the pad/via itself:
``(remove_unused_layers [yes|no])`` and ``(keep_end_layers [yes|no])`` (older
boards write the bare token, meaning yes), plus ``(zone_layer_connections
"layer" ...)`` listing the layers forced to stay zone-connected.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import PadStack, PadStackLayer, PadStackMode
from phosphor_eda.formats.common import sexp
from phosphor_eda.formats.kicad import pcb_common

if TYPE_CHECKING:
    from collections.abc import Callable

    from phosphor_eda.formats.common.sexp import SExpNode

# KiCad's padstack mode tokens mapped to the domain's stack modes.
_STACK_MODES = {
    "front_inner_back": PadStackMode.TOP_MID_BOTTOM,
    "custom": PadStackMode.PER_LAYER,
}

# A parser for one ``(layer "name" ...)`` entry: (entry, domain layer name,
# base geometry) -> stack layer.
type _EntryParser = Callable[[SExpNode, str, PadStackLayer], PadStackLayer]


def parse_pad_stack(
    pad_sexpr: SExpNode,
    *,
    shape: str,
    size_x: float,
    size_y: float,
    corner_radius_ratio: float,
) -> PadStack:
    """Build a pad's :class:`PadStack` from its base geometry and sexpr node."""
    base = PadStackLayer(
        layer="",
        shape=shape,
        size_x=size_x,
        size_y=size_y,
        corner_radius_ratio=corner_radius_ratio,
    )
    return _parse_stack(pad_sexpr, base, _pad_layer_geometry)


def parse_via_stack(via_sexpr: SExpNode, *, diameter: float) -> PadStack:
    """Build a via's :class:`PadStack` from its base diameter and sexpr node."""
    base = PadStackLayer(layer="", shape="circle", size_x=diameter, size_y=diameter)
    return _parse_stack(via_sexpr, base, _via_layer_geometry)


def _parse_stack(item: SExpNode, base: PadStackLayer, parse_entry: _EntryParser) -> PadStack:
    stack_node = sexp.find(item, "padstack")
    if stack_node is None:
        mode = PadStackMode.SIMPLE
        layers: tuple[PadStackLayer, ...] = (base,)
    else:
        mode = _stack_mode(stack_node)
        entries = sexp.find_all(stack_node, "layer")
        if mode is PadStackMode.TOP_MID_BOTTOM:
            layers = _tier_layers(base, entries, parse_entry)
        else:
            layers = _per_layer_layers(base, entries, parse_entry)
    return PadStack(
        mode=mode,
        layers=layers,
        remove_unused_layers=_flag(item, "remove_unused_layers"),
        keep_end_layers=_flag(item, "keep_end_layers"),
        zone_connected_layers=_zone_connected_layers(item),
    )


def _stack_mode(stack_node: SExpNode) -> PadStackMode:
    mode_name = sexp.find_str(stack_node, "mode")
    mode = _STACK_MODES.get(mode_name)
    if mode is None:
        msg = f"Unsupported padstack mode {mode_name!r}"
        raise ValueError(msg)
    return mode


def _tier_layers(
    base: PadStackLayer,
    entries: list[SExpNode],
    parse_entry: _EntryParser,
) -> tuple[PadStackLayer, ...]:
    """front_inner_back stack: base front geometry + "Inner"/"B.Cu" entries."""
    overrides = {sexp.val(entry): entry for entry in entries}
    tiers = [dataclasses.replace(base, layer="top")]
    for source_name, tier in (("Inner", "mid"), ("B.Cu", "bottom")):
        entry = overrides.get(source_name)
        if entry is None:
            tiers.append(dataclasses.replace(base, layer=tier))
        else:
            tiers.append(parse_entry(entry, tier, base))
    return tuple(tiers)


def _per_layer_layers(
    base: PadStackLayer,
    entries: list[SExpNode],
    parse_entry: _EntryParser,
) -> tuple[PadStackLayer, ...]:
    """custom stack: base F.Cu geometry + one entry per remaining copper layer."""
    layers = [dataclasses.replace(base, layer="F.Cu")]
    for entry in entries:
        name = sexp.val(entry)
        parsed = parse_entry(entry, name, base)
        if name == "F.Cu":
            # KiCad never writes the front layer, but tolerate generators that do.
            layers[0] = parsed
        else:
            layers.append(parsed)
    return tuple(layers)


def _pad_layer_geometry(entry: SExpNode, name: str, base: PadStackLayer) -> PadStackLayer:
    shape_node = sexp.find(entry, "shape")
    size_node = sexp.find(entry, "size")
    offset_node = sexp.find(entry, "offset")
    if size_node:
        size_x = sexp.num(size_node, 1)
        size_y = sexp.num(size_node, 2) if len(size_node) > 2 else size_x
    else:
        size_x, size_y = base.size_x, base.size_y
    # Absent offset/rratio mean zero (KiCad resets them per layer), not the
    # base value — the formatter omits them when default.
    offset_x, offset_y = pcb_common.xy(offset_node) if offset_node else (0.0, 0.0)
    return PadStackLayer(
        layer=name,
        shape=sexp.val(shape_node) if shape_node else base.shape,
        size_x=size_x,
        size_y=size_y,
        corner_radius_ratio=sexp.find_num(entry, "roundrect_rratio"),
        offset_x=offset_x,
        offset_y=offset_y,
    )


def _via_layer_geometry(entry: SExpNode, name: str, base: PadStackLayer) -> PadStackLayer:
    size_node = sexp.find(entry, "size")
    diameter = sexp.num(size_node, 1) if size_node else base.size_x
    return PadStackLayer(layer=name, shape="circle", size_x=diameter, size_y=diameter)


def _flag(item: SExpNode, tag_name: str) -> bool:
    node = sexp.find(item, tag_name)
    if node is None:
        return False
    if len(node) < 2:
        # Older boards write the bare token with no yes/no argument.
        return True
    return pcb_common.sexp_bool(node[1], default=True)


def _zone_connected_layers(item: SExpNode) -> tuple[str, ...]:
    node = sexp.find(item, "zone_layer_connections")
    return tuple(pcb_common.layer_names(node)) if node else ()
