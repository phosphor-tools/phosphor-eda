"""KiCad PCB stackup parsing (returns the project.Stackup model)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.project import Stackup, StackupLayer

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.kicad.sexp import SExpNode


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


def load_kicad_stackup(path: Path) -> Stackup | None:
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    sexpr: SExpNode = list(data[1:]) if data else []
    return parse_kicad_stackup(sexpr)
