"""Serialize a derived render plan to SVG.

Emits an SVG with layer groups, data-* attributes on every element, clip
paths and masks, plus the custom-CSS and annotation style blocks. Geometry
omission and styling are already resolved in the plan; this module only
turns the plan into SVG text.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.geometry.text_metrics import EMBEDDED_FONT_FAMILY, embedded_font_css
from phosphor_eda.render.annotation_svg import annotation_css, render_annotations
from phosphor_eda.render.primitives import PaintMode
from phosphor_eda.render.svg import Svg, fmt_attrs

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Pcb
    from phosphor_eda.render.inventory import InventoryTags
    from phosphor_eda.render.modes import DerivedLayer
    from phosphor_eda.render.plan import DerivedRenderPlan
    from phosphor_eda.render.primitives import LayerClip, LayerMask, SvgPrimitive, SvgText
    from phosphor_eda.render.profiler import RenderProfiler
    from phosphor_eda.render.tokens import ResolvedStyle, VisualRole

_BOARD_TEXT_FONT_FAMILY = f"{EMBEDDED_FONT_FAMILY}, Inter, system-ui, sans-serif"

_STYLE_BLOCK_TERMINATOR_RE = re.compile(r"</\s*style\s*>", re.IGNORECASE)
_LayerClipSignature = tuple[str, ...]
_LayerMaskSignature = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def render_pcb_svg_from_derived_plan(
    plan: DerivedRenderPlan,
    *,
    profiler: RenderProfiler | None = None,
) -> str:
    """Serialize a derived-layer render plan to SVG."""
    svg = Svg()
    view_box = plan.view_box
    svg_open = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{plan.width_px}" '
        + f'height="{plan.height_px}" viewBox="{view_box.x:.4f} {view_box.y:.4f} '
        + f'{view_box.width:.4f} {view_box.height:.4f}">'
    )
    svg.raw(svg_open)
    charset = _plan_text_charset(plan)
    if charset:
        svg.raw('<style id="fonts">')
        svg.raw(_escape_style_block_text(embedded_font_css(frozenset(charset))))
        svg.raw("</style>")
    if plan.custom_css:
        svg.raw('<style id="custom">')
        svg.raw(_escape_style_block_text(plan.custom_css))
        svg.raw("</style>")
    if plan.annotations is not None:
        svg.raw('<style id="annotations">')
        svg.raw(
            _escape_style_block_text(
                annotation_css(plan.annotations.font_size, annotation_style=plan.annotation_style)
            )
        )
        svg.raw("</style>")

    clip_ids_by_signature: dict[_LayerClipSignature, str] = {}
    mask_ids_by_signature: dict[_LayerMaskSignature, str] = {}
    _render_derived_layers(
        svg,
        plan.base_layers,
        profiler=profiler,
        group="base",
        clip_ids_by_signature=clip_ids_by_signature,
        mask_ids_by_signature=mask_ids_by_signature,
    )
    for group in plan.highlight_groups:
        svg.group_start(attrs={"class": "highlight-overlay", "data-highlight-target": group.target})
        _render_derived_layers(
            svg,
            group.layers,
            profiler=profiler,
            group="highlight",
            clip_ids_by_signature=clip_ids_by_signature,
            mask_ids_by_signature=mask_ids_by_signature,
        )
        svg.group_end()

    if plan.annotations is not None:
        render_annotations(
            svg,
            plan.annotations,
            plan.annotations.font_size,
            annotation_style=plan.annotation_style,
        )

    svg.raw("</svg>")
    rendered = svg.build()
    if profiler is not None:
        profiler.metric(
            "svg.output",
            bytes=len(rendered.encode()),
            characters=len(rendered),
            base_layers=len(plan.base_layers),
            highlight_groups=len(plan.highlight_groups),
            highlight_layers=sum(len(group.layers) for group in plan.highlight_groups),
        )
    return rendered


def append_pcb_metadata(svg: str, board: Pcb) -> str:
    """Append a ``<script id="pcb-metadata">`` block with footprint metadata."""
    metadata_json = _pcb_metadata_json(board)
    if not metadata_json:
        return svg
    metadata_block = (
        f'<script type="application/json" id="pcb-metadata">\n{metadata_json}\n</script>'
    )
    if svg.endswith("</svg>"):
        return f"{svg[:-6]}{metadata_block}\n</svg>"
    return f"{svg}\n{metadata_block}"


def _pcb_metadata_json(board: Pcb) -> str:
    meta = {
        fp.reference: {"lib": fp.footprint_lib, "value": fp.value}
        for fp in sorted(board.footprints, key=lambda footprint: footprint.reference)
        if fp.footprint_lib or fp.value
    }
    return _escape_json_for_script(json.dumps(meta, separators=(",", ":"))) if meta else ""


def _escape_json_for_script(value: str) -> str:
    return value.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _escape_style_block_text(value: str) -> str:
    return _STYLE_BLOCK_TERMINATOR_RE.sub(r"<\\/style>", value)


def _render_derived_layers(
    svg: Svg,
    layers: tuple[DerivedLayer, ...],
    *,
    profiler: RenderProfiler | None = None,
    group: str,
    clip_ids_by_signature: dict[_LayerClipSignature, str],
    mask_ids_by_signature: dict[_LayerMaskSignature, str],
) -> None:
    for layer in layers:
        if not layer.primitives:
            continue
        clip = layer.clip if layer.clip is not None and layer.clip.board else None
        clip_id = ""
        clip_already_rendered = False
        if clip is not None:
            clip_signature = _layer_clip_signature(clip)
            clip_already_rendered = clip_signature in clip_ids_by_signature
            clip_id = clip_ids_by_signature.setdefault(
                clip_signature,
                _layer_clip_id(group, len(clip_ids_by_signature), layer),
            )
        mask = layer.mask if layer.mask is not None and layer.mask.board else None
        mask_id = ""
        mask_already_rendered = False
        if mask is not None:
            mask_signature = _layer_mask_signature(mask)
            mask_already_rendered = mask_signature in mask_ids_by_signature
            mask_id = mask_ids_by_signature.setdefault(
                mask_signature,
                _layer_mask_id(group, len(mask_ids_by_signature), layer),
            )
        if clip is not None:
            _render_layer_clip(
                svg,
                clip_id,
                clip,
                already_rendered=clip_already_rendered,
            )
        if mask is not None:
            _render_layer_mask(
                svg,
                mask_id,
                mask,
                already_rendered=mask_already_rendered,
            )
        if profiler is not None:
            profiler.metric(
                "svg.layer_path",
                group=group,
                layer=layer.id,
                source_ids=len(layer.source_ids),
                primitives=len(layer.primitives),
                path_characters=sum(len(primitive.d) for primitive in layer.primitives),
                move_commands=sum(primitive.d.count("M ") for primitive in layer.primitives),
                line_commands=sum(primitive.d.count("L ") for primitive in layer.primitives),
            )
        attrs = _derived_layer_group_attrs(layer)
        if clip_id:
            attrs["clip-path"] = f"url(#{clip_id})"
        if mask_id:
            attrs["mask"] = f"url(#{mask_id})"
        svg.group_start(attrs=attrs)
        for primitive in layer.primitives:
            if primitive.text is not None:
                _render_text_primitive(svg, layer.style, primitive, primitive.text)
            else:
                svg.path(primitive.d, attrs=_derived_layer_path_attrs(layer.style, primitive))
        svg.group_end()


def _plan_text_charset(plan: DerivedRenderPlan) -> set[str]:
    """Collect every character that will be rendered as ``<text>``.

    Drives the lazy font subset: board text from all layers plus annotation
    label/legend strings, so one embedded face covers the whole document.
    """
    chars: set[str] = set()
    layer_groups = [plan.base_layers, *(group.layers for group in plan.highlight_groups)]
    for layers in layer_groups:
        for layer in layers:
            for primitive in layer.primitives:
                if primitive.text is not None:
                    chars.update(primitive.text.content)
    annotations = plan.annotations
    if annotations is not None:
        for callout in (
            *(box.callout for box in annotations.boxes),
            *(pointer.callout for pointer in annotations.pointers),
            *(label.callout for label in annotations.labels),
        ):
            if callout is not None:
                chars.update(callout.text)
        legend = annotations.legend
        if legend is not None:
            # .legend-title-text renders with `text-transform: uppercase`,
            # so the subset needs the uppercased glyphs too.
            chars.update(legend.title)
            chars.update(legend.title.upper())
            for entry in legend.entries:
                chars.update(entry.label)
    return chars


def _render_text_primitive(
    svg: Svg,
    style: ResolvedStyle | None,
    primitive: SvgPrimitive,
    text: SvgText,
) -> None:
    """Emit a board-text primitive as a native ``<text>`` element."""
    attrs: dict[str, str] = {
        "x": f"{text.x:.4f}",
        "y": f"{text.y:.4f}",
        "font-size": f"{text.font_size:.4f}",
        "font-family": _BOARD_TEXT_FONT_FAMILY,
        "text-anchor": text.text_anchor,
    }
    fill = style.fill if style is not None and style.fill is not None else None
    if fill is not None:
        attrs["fill"] = fill
    transform = _text_transform(text)
    if transform:
        attrs["transform"] = transform
    attrs.update(_primitive_metadata_attrs(primitive))
    svg.raw(f"<text{fmt_attrs(attrs)}>{xml_escape(text.content)}</text>")


def _text_transform(text: SvgText) -> str:
    """Compose rotation about the text center with back-side mirroring.

    Both rotation and the mirror turn about the authored center
    ``(pivot_x, pivot_y)`` so they're independent of the baseline shift.
    Non-mirrored text is a plain ``rotate(θ pivot)``. Mirrored (back-side)
    text flips horizontally across the vertical line through the pivot, then
    rotates — written as ``translate(p) rotate(θ) scale(-1 1)
    translate(-p)`` so the pivot stays fixed under the flip.
    """
    px = text.pivot_x
    py = text.pivot_y
    if text.mirrored:
        return (
            f"translate({px:.4f} {py:.4f}) "
            f"rotate({text.rotation:.4f}) "
            "scale(-1 1) "
            f"translate({-px:.4f} {-py:.4f})"
        )
    if text.rotation:
        return f"rotate({text.rotation:.4f} {px:.4f} {py:.4f})"
    return ""


def _render_layer_clip(
    svg: Svg,
    clip_id: str,
    clip: LayerClip,
    *,
    already_rendered: bool,
) -> None:
    if already_rendered:
        return
    if not clip.board:
        return
    svg.raw(f'<defs><clipPath id="{xml_escape(clip_id)}" clipPathUnits="userSpaceOnUse">')
    for primitive in clip.board:
        svg.path(primitive.d, attrs=_layer_clip_path_attrs(primitive))
    svg.raw("</clipPath></defs>")


def _render_layer_mask(
    svg: Svg,
    mask_id: str,
    mask: LayerMask,
    *,
    already_rendered: bool,
) -> None:
    if already_rendered:
        return
    if not mask.board:
        return
    bounds = mask.bounds()
    if bounds is None:
        return
    min_x, min_y, max_x, max_y = bounds
    pad = max(max_x - min_x, max_y - min_y, 1.0) * 0.05
    mask_attrs = " ".join(
        (
            f'id="{xml_escape(mask_id)}"',
            'maskUnits="userSpaceOnUse"',
            f'x="{min_x - pad:.4f}"',
            f'y="{min_y - pad:.4f}"',
            f'width="{(max_x - min_x) + 2 * pad:.4f}"',
            f'height="{(max_y - min_y) + 2 * pad:.4f}"',
        )
    )
    svg.raw(f"<defs><mask {mask_attrs}>")
    for primitive in mask.board:
        svg.path(primitive.d, attrs=_layer_mask_path_attrs(primitive, fill="white"))
    for primitive in (*mask.drills, *mask.openings):
        svg.path(primitive.d, attrs=_layer_mask_path_attrs(primitive, fill="black"))
    svg.raw("</mask></defs>")


def _layer_mask_signature(mask: LayerMask) -> _LayerMaskSignature:
    return (
        tuple(primitive.d for primitive in mask.board),
        tuple(primitive.d for primitive in mask.drills),
        tuple(primitive.d for primitive in mask.openings),
    )


def _layer_clip_signature(clip: LayerClip) -> _LayerClipSignature:
    return tuple(primitive.d for primitive in clip.board)


def _layer_mask_id(group: str, index: int, layer: DerivedLayer) -> str:
    raw = f"layer-mask-{group}-{index}-{layer.id}"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw)


def _layer_clip_id(group: str, index: int, layer: DerivedLayer) -> str:
    raw = f"layer-clip-{group}-{index}-{layer.id}"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw)


def _derived_layer_group_attrs(layer: DerivedLayer) -> dict[str, str]:
    attrs = {
        "data-role": _visual_role_name(layer.role),
        "data-source-layers": ",".join(layer.source_layers),
    }
    for key, value in layer.data.items():
        attr_name = key if key.startswith("data-") else f"data-{key}"
        attrs[attr_name] = value
    group_style = _resolved_group_style_svg_attrs(layer.style)
    if group_style:
        attrs.update(group_style)
    return attrs


def _derived_layer_path_attrs(
    style: ResolvedStyle | None,
    primitive: SvgPrimitive,
) -> dict[str, str]:
    if primitive.paint is PaintMode.STROKE:
        attrs = _stroke_primitive_style_attrs(style, primitive)
    else:
        attrs = _resolved_path_style_svg_attrs(style)
        attrs["fill-rule"] = "evenodd"
    attrs.update(primitive.style)
    attrs.update(_primitive_metadata_attrs(primitive))
    return attrs


def _stroke_primitive_style_attrs(
    style: ResolvedStyle | None,
    primitive: SvgPrimitive,
) -> dict[str, str]:
    """Style attrs for a stroke-mode primitive: paint the layer color as stroke."""
    declarations = ["fill: none"]
    if style is not None and style.fill is not None:
        declarations.append(f"stroke: {style.fill}")
    if primitive.stroke_width is not None:
        declarations.append(f"stroke-width: {primitive.stroke_width:.4f}")
    if primitive.stroke_linecap is not None:
        declarations.append(f"stroke-linecap: {primitive.stroke_linecap}")
    return {"style": "; ".join(declarations)}


def _layer_mask_path_attrs(primitive: SvgPrimitive, *, fill: str) -> dict[str, str]:
    if primitive.paint is PaintMode.STROKE:
        attrs = {"fill": "none", "stroke": fill}
        if primitive.stroke_width is not None:
            attrs["stroke-width"] = f"{primitive.stroke_width:.4f}"
        if primitive.stroke_linecap is not None:
            attrs["stroke-linecap"] = primitive.stroke_linecap
    else:
        attrs = {"fill": fill, "fill-rule": "evenodd"}
    attrs.update(_primitive_metadata_attrs(primitive))
    return attrs


def _layer_clip_path_attrs(primitive: SvgPrimitive) -> dict[str, str]:
    attrs = {"fill-rule": "evenodd"}
    attrs.update(_primitive_metadata_attrs(primitive))
    return attrs


def _primitive_metadata_attrs(primitive: SvgPrimitive) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if primitive.source_id:
        attrs["data-source-id"] = primitive.source_id
    if primitive.source_layer:
        attrs["data-source-layer"] = primitive.source_layer
    attrs["data-kind"] = str(primitive.kind)
    attrs.update(_primitive_tag_attrs(primitive.tags))
    for key, value in primitive.data.items():
        attr_name = key if key.startswith("data-") else f"data-{key}"
        attrs[attr_name] = value
    return attrs


def _primitive_tag_attrs(tags: InventoryTags) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if tags.source_collection:
        attrs["data-source-collection"] = tags.source_collection
        attrs["data-source-index"] = str(tags.source_index)
    if tags.component_ref:
        attrs["data-component-ref"] = tags.component_ref
    if tags.component_prefix:
        attrs["data-component-prefix"] = tags.component_prefix
    if tags.pad_number:
        attrs["data-pad-number"] = tags.pad_number
    if tags.net_number is not None:
        attrs["data-net-number"] = str(tags.net_number)
    if tags.net_name:
        attrs["data-net-name"] = tags.net_name
    if tags.text_kind:
        attrs["data-text-kind"] = tags.text_kind
    if tags.footprint_lib:
        attrs["data-footprint-lib"] = tags.footprint_lib
    if tags.value:
        attrs["data-value"] = tags.value
    return attrs


def _visual_role_name(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ".".join(parts)


def _resolved_group_style_svg_attrs(style: ResolvedStyle | None) -> dict[str, str]:
    if style is None or style.opacity is None:
        return {}
    return {"style": f"opacity: {style.opacity:.4f}"}


def _resolved_path_style_svg_attrs(style: ResolvedStyle | None) -> dict[str, str]:
    if style is None:
        return {}
    declarations: list[str] = []
    if style.fill is not None:
        declarations.append(f"fill: {style.fill}")
    if style.stroke is not None:
        declarations.append(f"stroke: {style.stroke}")
    if style.stroke_width_mm is not None:
        declarations.append(f"stroke-width: {style.stroke_width_mm:.4f}")
    return {"style": "; ".join(declarations)} if declarations else {}
