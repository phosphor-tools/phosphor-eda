"""KiCad PCB layer definitions, role mapping, and selector resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.pcb import LayerRole, PcbLayer, PcbLayerMetadata

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb_builder import PcbBuilder
    from phosphor_eda.formats.kicad.sexp import SExpNode


_KICAD_TYPE_ROLES: dict[str, tuple[LayerRole, ...]] = {
    "signal": (LayerRole.COPPER, LayerRole.SIGNAL),
    "power": (LayerRole.COPPER, LayerRole.POWER),
    "mixed": (LayerRole.COPPER, LayerRole.MIXED),
    "jumper": (LayerRole.COPPER, LayerRole.JUMPER),
    "front": (LayerRole.FRONT,),
    "back": (LayerRole.BACK,),
}


def kicad_type_roles(native_type: str) -> tuple[LayerRole, ...]:
    return _KICAD_TYPE_ROLES.get(native_type.strip().lower(), ())


_KICAD_NAME_ROLES: dict[str, tuple[LayerRole, ...]] = {
    "F.Cu": (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER),
    "B.Cu": (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER),
    "F.Mask": (LayerRole.SOLDER_MASK, LayerRole.FRONT),
    "B.Mask": (LayerRole.SOLDER_MASK, LayerRole.BACK),
    "F.Paste": (LayerRole.SOLDER_PASTE, LayerRole.FRONT),
    "B.Paste": (LayerRole.SOLDER_PASTE, LayerRole.BACK),
    "F.SilkS": (LayerRole.SILKSCREEN, LayerRole.FRONT),
    "B.SilkS": (LayerRole.SILKSCREEN, LayerRole.BACK),
    "F.Adhes": (LayerRole.ADHESIVE, LayerRole.FRONT),
    "B.Adhes": (LayerRole.ADHESIVE, LayerRole.BACK),
    "F.Fab": (LayerRole.FABRICATION, LayerRole.FRONT),
    "B.Fab": (LayerRole.FABRICATION, LayerRole.BACK),
    "F.CrtYd": (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.FRONT),
    "B.CrtYd": (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.BACK),
    "Edge.Cuts": (LayerRole.EDGE,),
    "Margin": (LayerRole.MARGIN,),
    "Dwgs.User": (LayerRole.DRAWING,),
    "Cmts.User": (LayerRole.COMMENT,),
}


def kicad_name_roles(name: str) -> tuple[LayerRole, ...]:
    exact = _KICAD_NAME_ROLES.get(name)
    if exact is not None:
        return exact
    if name.startswith("In") and name.endswith(".Cu"):
        return (LayerRole.COPPER, LayerRole.INNER)
    if name in {"Eco1.User", "Eco2.User"} or name.startswith("User."):
        return (LayerRole.USER,)
    return ()


def parse_layer_defs(sexpr: SExpNode) -> list[PcbLayer]:
    layers_section = sexp.find(sexpr, "layers")
    if not layers_section:
        return []
    layers: list[PcbLayer] = []
    for item in layers_section[1:]:
        if not isinstance(item, list) or len(item) < 3:
            msg = f"KiCad layer definition is malformed: {item!r}"
            raise ValueError(msg)
        raw_num = item[0]
        number = int(raw_num) if isinstance(raw_num, (int, float)) else None
        raw_name = item[1]
        name = raw_name.value() if isinstance(raw_name, sexpdata.Symbol) else str(raw_name)
        raw_type = item[2]
        native_type = raw_type.value() if isinstance(raw_type, sexpdata.Symbol) else str(raw_type)
        native_user_name = str(item[3]) if len(item) > 3 else ""
        layers.append(
            PcbLayer(
                name=name,
                roles=(*kicad_type_roles(native_type), *kicad_name_roles(name)),
                number=number,
                metadata=PcbLayerMetadata(
                    source_format="kicad",
                    native_type=native_type,
                    native_user_name=native_user_name,
                ),
            )
        )
    return layers


# KiCad layer-selector suffixes mapped to the domain role they expand to.
# Used for ``*.<suffix>`` (all matching layers) and ``F&B.<suffix>`` (front/back).
_SELECTOR_SUFFIX_ROLES: dict[str, LayerRole] = {
    "Cu": LayerRole.COPPER,
    "Mask": LayerRole.SOLDER_MASK,
    "Paste": LayerRole.SOLDER_PASTE,
    "SilkS": LayerRole.SILKSCREEN,
    "Adhes": LayerRole.ADHESIVE,
    "Fab": LayerRole.FABRICATION,
    "CrtYd": LayerRole.COURTYARD,
}


def resolve_layer_selector(builder: PcbBuilder, name: str, *, source: str) -> tuple[PcbLayer, ...]:
    prefix, _, suffix = name.partition(".")
    role = _SELECTOR_SUFFIX_ROLES.get(suffix)
    if role is not None and prefix == "*":
        return tuple(layer for layer in builder.layers if layer.has_role(role))
    if role is not None and prefix == "F&B":
        return tuple(
            layer
            for layer in builder.layers
            if layer.has_role(role)
            and (layer.has_role(LayerRole.FRONT) or layer.has_role(LayerRole.BACK))
        )
    return (builder.resolve_layer(name, source=source),)


def resolve_layers(builder: PcbBuilder, names: list[str], *, source: str) -> tuple[PcbLayer, ...]:
    resolved: list[PcbLayer] = []
    for name in names:
        for layer in resolve_layer_selector(builder, name, source=source):
            if layer not in resolved:
                resolved.append(layer)
    return tuple(resolved)
