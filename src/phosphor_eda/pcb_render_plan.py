from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.pcb import LayerFunction, PcbLayer

if TYPE_CHECKING:
    from phosphor_eda.pcb_render_settings import LayerIncludeRule


class GeometryKind(StrEnum):
    BOARD_OUTLINE = "board_outline"
    PAD = "pad"
    TRACE = "trace"
    TRACE_ARC = "trace_arc"
    ZONE = "zone"
    VIA = "via"
    SILK = "silk"
    BODY = "body"
    REF_TEXT = "ref_text"


class InclusionReason(StrEnum):
    VISIBLE = "visible"
    HIGHLIGHT = "highlight"
    ANNOTATION_TARGET = "annotation_target"


@dataclass(frozen=True)
class ViewBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class RenderPoint:
    x: float
    y: float


@dataclass
class EmittedGeometry:
    kind: GeometryKind
    layer: str
    attrs: dict[str, str]
    reason: InclusionReason
    source: object | None = None
    points: tuple[RenderPoint, ...] = ()
    clipped: bool = True
    style: dict[str, object] = field(default_factory=dict)


@dataclass
class ClipPlan:
    board_path_d: str
    drill_path_d: str = ""


@dataclass
class PcbRenderPlan:
    side: str
    width_px: int
    height_px: int
    view_box: ViewBox
    board_bbox: tuple[float, float, float, float]
    base: list[EmittedGeometry] = field(default_factory=list)
    overlay: list[EmittedGeometry] = field(default_factory=list)
    omitted_count: int = 0
    clip: ClipPlan | None = None
    annotations: object | None = None
    annotation_style: dict[str, object] = field(default_factory=dict)
    custom_css: str = ""


def layer_role(layer: PcbLayer) -> str:
    if layer.function == LayerFunction.COPPER:
        return "copper"
    if layer.function == LayerFunction.SILKSCREEN:
        return "silkscreen"
    if layer.function == LayerFunction.FAB:
        return "fabrication"
    if layer.function == LayerFunction.SOLDER_MASK:
        return "mask"
    if layer.function == LayerFunction.SOLDER_PASTE:
        return "paste"
    if layer.function == LayerFunction.MECHANICAL:
        return "mechanical"
    return "unknown"


def layer_matches_rule(layer: PcbLayer, rule: LayerIncludeRule, active_side: str) -> bool:
    if rule.name and rule.name != layer.name:
        return False
    if rule.role and rule.role != layer_role(layer):
        return False
    if rule.side in ("", "any"):
        return True
    if rule.side == "active":
        return layer.side == active_side
    if rule.side == "opposite":
        return layer.side in ("front", "back") and layer.side != active_side
    return layer.side == rule.side
