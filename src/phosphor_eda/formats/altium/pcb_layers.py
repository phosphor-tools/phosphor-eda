"""Altium layer-identity mapping for the PCB parser.

Builds the ``{layer_number: PcbLayer}`` map from Board6 metadata, resolving
roles from Altium's numeric layer ranges, mechanical-kind tags, layer-name
heuristics, and the v9 stackup layer-id table. Also resolves the V7 layer
name overrides used by region records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import LayerRole, PcbLayer, PcbLayerMetadata
from phosphor_eda.formats.altium.enums import AltiumLayer

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext

# Altium Board6/Data carries layer names and mechanical kinds. Numeric layer
# ranges are used only to decode file-format semantics after the source has
# provided a concrete layer identity.
_ALTIUM_LAYER_NUMBERS = tuple(range(AltiumLayer.TOP_LAYER, AltiumLayer.MULTI_LAYER + 1))

_MECHKIND_ROLES: dict[str, tuple[LayerRole, ...]] = {
    "assemblytop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.ASSEMBLY,
        LayerRole.FRONT,
    ),
    "assemblybottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.ASSEMBLY,
        LayerRole.BACK,
    ),
    "assemblynotes": (LayerRole.MECHANICAL, LayerRole.ASSEMBLY_NOTES),
    "board": (LayerRole.MECHANICAL, LayerRole.BOARD),
    "coatingtop": (LayerRole.MECHANICAL, LayerRole.COATING, LayerRole.FRONT),
    "coatingbottom": (LayerRole.MECHANICAL, LayerRole.COATING, LayerRole.BACK),
    "componentcentertop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_CENTER,
        LayerRole.FRONT,
    ),
    "componentcenterbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_CENTER,
        LayerRole.BACK,
    ),
    "componentoutlinetop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.FRONT,
    ),
    "componentoutlinebottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.BACK,
    ),
    "courtyardtop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.FRONT,
    ),
    "courtyardbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.BACK,
    ),
    "designatortop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DESIGNATOR,
        LayerRole.FRONT,
    ),
    "designatorbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DESIGNATOR,
        LayerRole.BACK,
    ),
    "dimensions": (LayerRole.MECHANICAL, LayerRole.DIMENSION),
    "dimensionstop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DIMENSION,
        LayerRole.FRONT,
    ),
    "dimensionsbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DIMENSION,
        LayerRole.BACK,
    ),
    "fabnotes": (LayerRole.MECHANICAL, LayerRole.FABRICATION, LayerRole.FAB_NOTES),
    "gluepointstop": (LayerRole.MECHANICAL, LayerRole.GLUE_POINTS, LayerRole.FRONT),
    "gluepointsbottom": (LayerRole.MECHANICAL, LayerRole.GLUE_POINTS, LayerRole.BACK),
    "goldplatingtop": (LayerRole.MECHANICAL, LayerRole.GOLD_PLATING, LayerRole.FRONT),
    "goldplatingbottom": (LayerRole.MECHANICAL, LayerRole.GOLD_PLATING, LayerRole.BACK),
    "valuetop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.VALUE,
        LayerRole.FRONT,
    ),
    "valuebottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.VALUE,
        LayerRole.BACK,
    ),
    "vcut": (LayerRole.MECHANICAL, LayerRole.V_CUT),
    "3dbodytop": (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.FRONT),
    "3dbodybottom": (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.BACK),
    "routetoolpath": (LayerRole.MECHANICAL, LayerRole.ROUTE_TOOL_PATH),
    "sheet": (LayerRole.MECHANICAL, LayerRole.SHEET),
    "boardshape": (LayerRole.MECHANICAL, LayerRole.BOARD_SHAPE, LayerRole.EDGE),
}

# V7 layer name → Altium layer number.  Used to resolve the V7_LAYER property
# that overrides the byte-level layer number in region records.
V7_NAME_TO_NUM: dict[str, int] = {
    "TOP": 1,
    **{f"MID{i - 1}": i for i in range(2, 32)},
    "BOTTOM": 32,
    "TOPOVERLAY": 33,
    "BOTTOMOVERLAY": 34,
    "TOPPASTE": 35,
    "BOTTOMPASTE": 36,
    "TOPSOLDER": 37,
    "BOTTOMSOLDER": 38,
    **{f"MECHANICAL{i}": 56 + i for i in range(1, 17)},
}

_V9_STACK_LAYER_ID_TO_NUM: dict[int, int] = {
    16777217: 1,
    **{16777218 + index: 2 + index for index in range(30)},
    16842751: 32,
    16973830: 33,
    16973831: 34,
    16973832: 35,
    16973833: 36,
    16973834: 37,
    16973835: 38,
}


def build_layer_map(
    board_props: dict[str, str], ctx: ParseContext | None = None
) -> dict[int, PcbLayer]:
    """Build layer definitions from Board6 metadata and Altium layer IDs."""
    layers: dict[int, PcbLayer] = {}
    for num in _ALTIUM_LAYER_NUMBERS:
        native_kind = board_props.get(f"layer{num}mechkind", "")
        name = board_props.get(f"layer{num}name", "")
        if not name:
            continue
        layers[num] = PcbLayer(
            name=name,
            roles=(
                *_altium_number_roles(num),
                *_altium_mechkind_roles(native_kind),
                *_altium_name_roles(num, name, native_kind),
            ),
            number=num,
            metadata=PcbLayerMetadata(source_format="altium", native_kind=native_kind),
        )

    _apply_v9_stack_layer_names(layers, board_props, ctx)
    return layers


def _altium_number_roles(num: int) -> tuple[LayerRole, ...]:
    if num == AltiumLayer.TOP_LAYER:
        return (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER, LayerRole.SIGNAL)
    if AltiumLayer.MID_LAYER_1 <= num <= AltiumLayer.MID_LAYER_30:
        return (LayerRole.COPPER, LayerRole.INNER, LayerRole.SIGNAL)
    if num == AltiumLayer.BOTTOM_LAYER:
        return (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER, LayerRole.SIGNAL)
    if num == AltiumLayer.TOP_OVERLAY:
        return (LayerRole.SILKSCREEN, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_OVERLAY:
        return (LayerRole.SILKSCREEN, LayerRole.BACK)
    if num == AltiumLayer.TOP_PASTE:
        return (LayerRole.SOLDER_PASTE, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_PASTE:
        return (LayerRole.SOLDER_PASTE, LayerRole.BACK)
    if num == AltiumLayer.TOP_SOLDER:
        return (LayerRole.SOLDER_MASK, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_SOLDER:
        return (LayerRole.SOLDER_MASK, LayerRole.BACK)
    if AltiumLayer.INTERNAL_PLANE_1 <= num <= AltiumLayer.INTERNAL_PLANE_16:
        return (LayerRole.COPPER, LayerRole.INNER, LayerRole.PLANE, LayerRole.INTERNAL_PLANE)
    if num == AltiumLayer.DRILL_GUIDE:
        return (LayerRole.DRILL, LayerRole.DRILL_GUIDE)
    if num == AltiumLayer.KEEP_OUT_LAYER:
        return (LayerRole.KEEPOUT,)
    if AltiumLayer.MECHANICAL_1 <= num <= AltiumLayer.MECHANICAL_16:
        return (LayerRole.MECHANICAL,)
    if num == AltiumLayer.DRILL_DRAWING:
        return (LayerRole.DRILL, LayerRole.DRILL_DRAWING)
    if num == AltiumLayer.MULTI_LAYER:
        return (LayerRole.MULTI_LAYER,)
    return (LayerRole.UNKNOWN,)


def _altium_mechkind_roles(kind: str) -> tuple[LayerRole, ...]:
    return _MECHKIND_ROLES.get(kind.lower(), ())


def _altium_name_roles(num: int, name: str, native_kind: str) -> tuple[LayerRole, ...]:
    if native_kind or not (AltiumLayer.MECHANICAL_1 <= num <= AltiumLayer.MECHANICAL_16):
        return ()
    normalized = name.strip().lower().replace("-", " ").replace("_", " ")
    roles: list[LayerRole] = []
    if "outline" in normalized or "board shape" in normalized:
        roles.extend([LayerRole.BOARD_SHAPE, LayerRole.EDGE])
    if "courtyard" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.COURTYARD])
    if "assembly" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.ASSEMBLY])
    if "designator" in normalized or "reference" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.DESIGNATOR])
    if "value" in normalized or "comment" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.VALUE])
    if "3d body" in normalized or "3dbody" in normalized:
        roles.append(LayerRole.THREE_D_BODY)
    if normalized.startswith("top ") or normalized.endswith(" top"):
        roles.append(LayerRole.FRONT)
    elif normalized.startswith("bottom ") or normalized.endswith(" bottom"):
        roles.append(LayerRole.BACK)
    return tuple(roles)


def _apply_v9_stack_layer_names(
    layers: dict[int, PcbLayer],
    board_props: dict[str, str],
    ctx: ParseContext | None = None,
) -> None:
    """Use Altium v9 stackup layer IDs to preserve file-defined physical layer names."""
    for key, raw_layer_id in board_props.items():
        if not key.startswith("v9_stack_layer") or not key.endswith("_layerid"):
            continue

        prefix = key[: -len("layerid")]
        layer_name = board_props.get(f"{prefix}name", "")
        if not layer_name:
            continue

        layer_num = v9_stack_layer_id_to_num(raw_layer_id, ctx, key=key)
        if layer_num is None:
            continue

        layers[layer_num] = PcbLayer(
            name=layer_name,
            roles=(
                *_altium_number_roles(layer_num),
                *_altium_name_roles(layer_num, layer_name, ""),
            ),
            number=layer_num,
            metadata=PcbLayerMetadata(source_format="altium"),
        )


def v9_stack_layer_id_to_num(
    raw_layer_id: str, ctx: ParseContext | None = None, *, key: str = ""
) -> int | None:
    try:
        layer_id = int(raw_layer_id)
    except ValueError:
        if ctx is not None:
            ctx.warn(
                "malformed_layer_id",
                f"non-integer v9 stack layer id {raw_layer_id!r} for {key or 'layer'}; skipped",
            )
        return None
    return _V9_STACK_LAYER_ID_TO_NUM.get(layer_id)


def altium_layer_name(num: int, layer_map: dict[int, PcbLayer]) -> str:
    """Get native layer name for a layer number, or '' if unmapped."""
    layer = layer_map.get(num)
    return layer.name if layer else ""


def altium_layer_ref(
    num: int,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    *,
    source: str,
) -> PcbLayer | None:
    """Resolve a layer number to its concrete layer, or ``None`` if unknown.

    An unknown layer byte is a malformed-file condition, not a parser bug:
    warn and return ``None`` so the caller can skip the offending primitive
    instead of aborting the whole board.
    """
    layer = layer_map.get(num)
    if layer is None:
        ctx.warn(
            "unknown_layer",
            f"{source}: unknown Altium layer {num}; Board6/Data has no concrete "
            "layer name; primitive skipped",
        )
        return None
    return layer
