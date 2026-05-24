from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
