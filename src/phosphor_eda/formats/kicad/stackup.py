"""KiCad PCB stackup parsing (returns the project.Stackup model)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.project import Stackup, StackupLayer

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.formats.kicad.sexp import SExpNode

_ASSUMED_FR4_MATERIAL = "FR4 (assumed)"
_INNER_COPPER_RE = re.compile(r"^In(\d+)\.Cu$")


def parse_kicad_stackup(sexpr: SExpNode) -> Stackup | None:
    setup_node = sexp.find(sexpr, "setup")
    if not setup_node:
        return None
    stackup_node = sexp.find(setup_node, "stackup")
    if not stackup_node:
        return None
    layers: list[StackupLayer] = []
    copper_finish = ""
    for item in stackup_node[1:]:
        if not isinstance(item, list) or not item:
            continue
        tag = item[0].value() if isinstance(item[0], sexpdata.Symbol) else str(item[0])
        if tag == "copper_finish":
            copper_finish = str(item[1]) if len(item) > 1 else ""
            continue
        if tag != "layer":
            continue
        name = str(item[1]) if len(item) > 1 else ""
        side = ""
        if name.startswith("F.") or name == "Top":
            side = "front"
        elif name.startswith("B.") or name == "Bottom":
            side = "back"
        layer_type = sexp.find_str(item, "type")
        layers.append(
            StackupLayer(
                name=name,
                layer_type=layer_type,
                thickness_mm=sexp.find_num(item, "thickness"),
                material=sexp.find_str(item, "material"),
                epsilon_r=sexp.find_num(item, "epsilon_r"),
                loss_tangent=sexp.find_num(item, "loss_tangent"),
                side=side,
            )
        )
        for sub_item in item[2:]:
            if not isinstance(sub_item, list) or not sub_item:
                continue
            sub_tag = (
                sub_item[0].value()
                if isinstance(sub_item[0], sexpdata.Symbol)
                else str(sub_item[0])
            )
            if sub_tag != "addsublayer":
                continue
            layers.append(
                StackupLayer(
                    name=f"{name} (sublayer)",
                    layer_type=layer_type,
                    thickness_mm=sexp.find_num(sub_item, "thickness"),
                    material=sexp.find_str(sub_item, "material"),
                    epsilon_r=sexp.find_num(sub_item, "epsilon_r"),
                    loss_tangent=sexp.find_num(sub_item, "loss_tangent"),
                    side=side,
                )
            )
    if not layers:
        return None
    return Stackup(
        layers=layers,
        total_thickness_mm=sum(layer.thickness_mm for layer in layers),
        copper_finish=copper_finish,
    )


def synthesize_kicad_stackup(sexpr: SExpNode, layers: list[PcbLayer]) -> Stackup | None:
    """Build a conservative stackup when KiCad omits explicit construction data."""
    copper_layers = sorted(
        (layer for layer in layers if layer.has_role("copper")),
        key=_copper_stack_order,
    )
    if len(copper_layers) < 2:
        return None

    total_thickness_mm = _board_thickness_mm(sexpr)
    last_copper_index = len(copper_layers) - 1

    stackup_layers: list[StackupLayer] = []
    front_mask = _first_layer_with_roles(layers, ("solder_mask", "front"))
    if front_mask is not None:
        stackup_layers.append(
            StackupLayer(
                name=front_mask.name,
                layer_type="solder_mask",
                side="front",
            )
        )

    for index, layer in enumerate(copper_layers):
        stackup_layers.append(
            StackupLayer(
                name=layer.name,
                layer_type="copper",
                side=layer.side,
            )
        )
        if index < last_copper_index:
            stackup_layers.append(
                StackupLayer(
                    name=f"Dielectric {index + 1}",
                    layer_type="dielectric",
                    material=_ASSUMED_FR4_MATERIAL,
                )
            )

    back_mask = _first_layer_with_roles(layers, ("solder_mask", "back"))
    if back_mask is not None:
        stackup_layers.append(
            StackupLayer(
                name=back_mask.name,
                layer_type="solder_mask",
                side="back",
            )
        )

    return Stackup(layers=stackup_layers, total_thickness_mm=total_thickness_mm)


def _board_thickness_mm(sexpr: SExpNode) -> float:
    general = sexp.find(sexpr, "general")
    if general is None:
        return 0.0
    return sexp.find_num(general, "thickness")


def _copper_stack_order(layer: PcbLayer) -> tuple[int, int, int, str]:
    if layer.has_role("front"):
        return (0, 0, _layer_number(layer), layer.name)
    if layer.has_role("back"):
        return (2, 0, _layer_number(layer), layer.name)
    inner_match = _INNER_COPPER_RE.match(layer.name)
    if inner_match:
        return (1, int(inner_match.group(1)), _layer_number(layer), layer.name)
    if layer.has_role("inner"):
        return (1, _layer_number(layer), _layer_number(layer), layer.name)
    return (1, _layer_number(layer), _layer_number(layer), layer.name)


def _layer_number(layer: PcbLayer) -> int:
    return layer.number if layer.number is not None else 9999


def _first_layer_with_roles(layers: list[PcbLayer], roles: tuple[str, ...]) -> PcbLayer | None:
    for layer in layers:
        if all(layer.has_role(role) for role in roles):
            return layer
    return None


def load_kicad_stackup(path: Path) -> Stackup | None:
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    sexpr: SExpNode = list(data[1:]) if data else []
    return parse_kicad_stackup(sexpr)
