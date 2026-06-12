"""Parse an Altium Designer .PcbDoc file into the PCB domain model.

A .PcbDoc is an OLE compound document containing separate streams for each
primitive type (tracks, pads, vias, etc.).  Text-based streams use
pipe-delimited ASCII properties; binary streams use fixed-size records with
a type(u8) + length(u32) header.

This module owns the top-level orchestration: read each OLE stream, decode it
via :mod:`pcb_streams`, then assemble the domain ``Pcb`` via :mod:`pcb_build`.
The layer map lives in :mod:`pcb_layers`, keepout synthesis in
:mod:`pcb_keepouts`, the intermediate primitive model and shared helpers in
:mod:`pcb_primitives`, and project-level enrichment in :mod:`pcb_project`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import olefile

from phosphor_eda.domain.pcb import PcbText
from phosphor_eda.formats.altium.errors import require_ole_file
from phosphor_eda.formats.altium.pcb_build import (
    build_pcb_from_parsed_primitives,
    compute_bbox,
)
from phosphor_eda.formats.altium.pcb_layers import build_layer_map
from phosphor_eda.formats.altium.pcb_primitives import (
    ParsedObjectKind,
    ParsedPrimitive,
    ParsedRole,
    parse_mil,
    read_text_records,
)
from phosphor_eda.formats.altium.pcb_records import COMPONENT_NONE
from phosphor_eda.formats.altium.pcb_streams import (
    apply_drill_manager_mask_apertures,
    dedupe_shape_based_board_polygons,
    parse_arcs,
    parse_board6_outline,
    parse_board_outline,
    parse_component_bodies,
    parse_components,
    parse_fills,
    parse_nets,
    parse_pads,
    parse_polygon_pours,
    parse_regions,
    parse_shape_based_regions,
    parse_texts,
    parse_tracks,
    parse_vias,
)
from phosphor_eda.formats.common.diagnostics import ParseContext

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import Pcb

# Re-exported so library code and tests can import the canonical Altium PCB
# surface from one module even though the implementation is split.
__all__ = [
    "parse_altium_pcb",
]


def _read_stream(ole: olefile.OleFileIO, name: str) -> bytes:
    """Read a stream from the OLE container, returning empty bytes if absent."""
    if ole.exists(name):
        return ole.openstream(name).read()
    return b""


def parse_altium_pcb(
    path: Path,
    ctx: ParseContext | None = None,
) -> Pcb:
    """Parse an Altium .PcbDoc file into the PCB domain model."""
    if ctx is None:
        ctx = ParseContext()
    require_ole_file(path)
    ole = olefile.OleFileIO(str(path))
    try:
        # Read all streams
        nets_data = _read_stream(ole, "Nets6/Data")
        comp_data = _read_stream(ole, "Components6/Data")
        tracks_data = _read_stream(ole, "Tracks6/Data")
        vias_data = _read_stream(ole, "Vias6/Data")
        arcs_data = _read_stream(ole, "Arcs6/Data")
        pads_data = _read_stream(ole, "Pads6/Data")
        texts_data = _read_stream(ole, "Texts6/Data")
        fills_data = _read_stream(ole, "Fills6/Data")
        regions_data = _read_stream(ole, "Regions6/Data")
        polygons6_data = _read_stream(ole, "Polygons6/Data")
        sb_regions_data = _read_stream(ole, "ShapeBasedRegions6/Data")
        comp_bodies_data = _read_stream(ole, "ComponentBodies6/Data")
        board_data = _read_stream(ole, "Board6/Data")
        drill_manager_data = _read_stream(ole, "DrillManager/Data")
    finally:
        ole.close()

    # Build layer map from Board6 metadata + static defaults
    board_props: dict[str, str] = {}
    if board_data:
        board_records = read_text_records(board_data)
        if board_records:
            board_props = board_records[0]
    layer_map = build_layer_map(board_props, ctx)

    # Parse text streams
    nets = parse_nets(nets_data)
    footprints = parse_components(comp_data, layer_map)

    # Parse binary streams
    vias = parse_vias(vias_data, layer_map, ctx)
    raw_pads = parse_pads(pads_data, nets, layer_map, ctx)
    raw_pads = apply_drill_manager_mask_apertures(raw_pads, drill_manager_data, ctx)
    raw_texts = parse_texts(texts_data, layer_map, ctx)
    pours, pour_id_map, pour_net_map = parse_polygon_pours(polygons6_data, nets, layer_map)
    track_geometry, track_keepouts = parse_tracks(tracks_data, layer_map, ctx, pour_id_map)
    arc_geometry, arc_keepouts = parse_arcs(arcs_data, layer_map, ctx, pour_id_map)
    fills, fill_keepouts = parse_fills(fills_data, layer_map, ctx)
    regions = parse_regions(regions_data, nets, layer_map, ctx, pour_id_map, pour_net_map)
    shape_regions = parse_shape_based_regions(
        sb_regions_data, nets, layer_map, ctx, pour_id_map, pour_net_map
    )
    comp_models = parse_component_bodies(comp_bodies_data)

    geometry = [
        *[item for item in track_geometry if item.metadata.native_component_index is None],
        *vias,
        *[item for item in arc_geometry if item.metadata.native_component_index is None],
        *fills,
        *regions,
        *dedupe_shape_based_board_polygons(
            regions,
            [item for item in shape_regions if item.metadata.native_component_index is None],
        ),
    ]
    if not any(item.has_role(ParsedRole.BOARD_OUTLINE) for item in geometry):
        geometry.extend(parse_board_outline(tracks_data, arcs_data, layer_map, ctx))
    if not any(item.has_role(ParsedRole.BOARD_OUTLINE) for item in geometry):
        # Older files keep the board shape only as Board6 vertex keys.
        geometry.extend(parse_board6_outline(board_props, layer_map, ctx))

    # Component-owned primitives carry their owner index in
    # metadata.native_component_index; board-level primitives carry None.
    # Drop any primitive whose component index points past the footprint list.
    for comp_idx, pad in raw_pads:
        if comp_idx == COMPONENT_NONE or comp_idx < len(footprints):
            geometry.append(pad)

    for comp_idx, text in raw_texts:
        if comp_idx == COMPONENT_NONE or comp_idx < len(footprints):
            geometry.append(text)

    for item in track_geometry + arc_geometry + shape_regions:
        comp_idx = item.metadata.native_component_index
        if comp_idx is not None and comp_idx < len(footprints):
            geometry.append(item)

    for comp_idx, models in comp_models.items():
        if comp_idx < len(footprints):
            geometry.extend(models)

    # Group component-owned geometry by footprint index once, then derive each
    # footprint's value text and pad bounding box from its own primitives.
    geometry_by_component: dict[int, list[ParsedPrimitive]] = {}
    for item in geometry:
        comp_idx = item.metadata.native_component_index
        if comp_idx is not None:
            geometry_by_component.setdefault(comp_idx, []).append(item)

    for fp_idx, fp in enumerate(footprints):
        owned = geometry_by_component.get(fp_idx, [])
        if not fp.value:
            fp.value = next(
                (
                    item.data.text
                    for item in owned
                    if item.has_role(ParsedRole.VALUE) and isinstance(item.data, PcbText)
                ),
                "",
            )
        fp.bbox = compute_bbox([item for item in owned if item.object_type == ParsedObjectKind.PAD])

    # Board name from Board6/Data (board_props already parsed above)
    board_name = board_props.get("filename", "")
    if "\\" in board_name:
        board_name = board_name.rsplit("\\", 1)[-1]
    if board_name.endswith(".$$$"):
        board_name = board_name[:-4]

    keepouts = [*track_keepouts, *arc_keepouts, *fill_keepouts]

    pcb = build_pcb_from_parsed_primitives(
        name=board_name,
        layer_map=layer_map,
        nets=nets,
        footprints=footprints,
        pours=pours,
        keepouts=keepouts,
        primitives=geometry,
        ctx=ctx,
    )
    # Board origin (Board6 ORIGINX/ORIGINY) aligns coordinates with
    # manufacturing outputs (gerber/pick-and-place). Stored in model
    # coordinates (mm, y negated like all parsed geometry).
    if "originx" in board_props and "originy" in board_props:
        pcb.metadata.properties["origin_x_mm"] = f"{parse_mil(board_props['originx']):.6f}"
        pcb.metadata.properties["origin_y_mm"] = f"{-parse_mil(board_props['originy']):.6f}"
    return pcb
