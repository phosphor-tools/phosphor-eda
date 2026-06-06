from __future__ import annotations

import json
from importlib.resources import as_file, files

import pytest

from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbFootprint,
    PcbGeometry,
    PcbGeometryMetadata,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbLayer,
    PcbLineGeometry,
    PcbNet,
    PcbPadGeometry,
    PcbTextGeometry,
    PcbViaGeometry,
)
from phosphor_eda.pcb_render import load_render_settings_json, render_pcb_svg
from phosphor_eda.pcb_render_geometry import (
    SYNTHETIC_BOARD_MATERIAL_ROLE,
    SYNTHETIC_BOARD_OUTLINE_ROLE,
    SYNTHETIC_DRILL_ROLE,
    GeometrySelector,
    build_geometry_store,
    geometry_matches_selector,
)
from phosphor_eda.pcb_render_settings import load_render_settings_file


def test_render_svg_uses_normalized_geometry_roles() -> None:
    board = _board()
    svg = render_pcb_svg(board, side="front")

    assert 'data-kind="pad"' in svg
    assert 'data-kind="trace"' in svg
    assert 'data-kind="via"' in svg
    assert 'data-kind="silkscreen"' in svg
    assert 'data-kind="designator"' in svg
    assert 'data-source-collection="pads"' in svg


def test_geometry_store_exposes_object_type_shape_roles_and_display_role() -> None:
    store = build_geometry_store(_board(), side="front")

    display_roles = {item.display_role for item in store.items}
    assert {
        SYNTHETIC_BOARD_MATERIAL_ROLE,
        SYNTHETIC_BOARD_OUTLINE_ROLE,
        SYNTHETIC_DRILL_ROLE,
        "pad",
        "via",
        "trace",
        "silkscreen",
        "designator",
    }.issubset(display_roles)
    pad = next(item for item in store.items if item.display_role == "pad")
    assert pad.object_type == PcbGeometryObject.PAD
    assert pad.shape == PcbGeometryShape.CIRCLE
    assert PcbGeometryRole.COPPER in pad.roles


def test_geometry_selector_matches_normalized_fields() -> None:
    store = build_geometry_store(_board(), side="front")
    pad = next(item for item in store.items if item.display_role == "pad")

    assert geometry_matches_selector(
        pad,
        GeometrySelector(
            object_types=frozenset({PcbGeometryObject.PAD}),
            roles=frozenset({PcbGeometryRole.COPPER}),
            layer_role="copper",
            net_name="VCC",
            component_ref="U1",
            pad_number="1",
        ),
        active_side="front",
    )
    assert not geometry_matches_selector(
        pad,
        GeometrySelector(object_types=frozenset({PcbGeometryObject.TRACK})),
        active_side="front",
    )


def test_render_settings_accept_new_object_filter_vocabulary() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "source": {
                    "layers": [
                        {"match": {"role": "copper"}, "objects": ["pad", "track", "via"]},
                        {"match": {"role": "silkscreen"}, "objects": ["graphic", "text"]},
                    ]
                }
            }
        )
    )

    assert settings.source.layers[0].objects == ("pad", "track", "via")
    assert settings.source.layers[1].objects == ("graphic", "text")


@pytest.mark.parametrize("name", ["design", "review", "clean", "high-contrast"])
def test_builtin_render_settings_use_normalized_object_filters(name: str) -> None:
    settings_file = files("phosphor_eda.render_settings").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    object_filters = {value for rule in settings.source.layers for value in rule.objects}
    assert "silk" not in object_filters
    assert "board_graphic_text" not in object_filters
    assert {"graphic", "text"}.intersection(object_filters)


def _board() -> Pcb:
    return Pcb(
        name="render-test",
        nets={1: PcbNet(1, "VCC")},
        footprints=[PcbFootprint("U1", "Package", 5.0, 5.0, 0.0, "F.Cu", value="MCU")],
        layers=[
            PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER), number=0),
            PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER), number=31),
            PcbLayer("F.Mask", (LayerRole.SOLDER_MASK, LayerRole.FRONT), number=37),
            PcbLayer("F.SilkS", (LayerRole.SILKSCREEN, LayerRole.FRONT), number=33),
            PcbLayer("Edge.Cuts", (LayerRole.EDGE,), number=44),
        ],
        geometry=[
            *_outline_geometry(),
            PcbGeometry(
                id="pad:U1:1",
                object_type=PcbGeometryObject.PAD,
                shape=PcbGeometryShape.CIRCLE,
                roles=(
                    PcbGeometryRole.COPPER,
                    PcbGeometryRole.CONDUCTOR,
                    PcbGeometryRole.SMD,
                    PcbGeometryRole.FOOTPRINT_MEMBER,
                ),
                data=PcbPadGeometry(
                    "1",
                    5.0,
                    5.0,
                    1.4,
                    1.4,
                    "circle",
                    drill=0.4,
                    mask_expansion=0.05,
                    mask_aperture_width=1.6,
                    mask_aperture_height=1.6,
                ),
                layers=("F.Cu",),
                net_number=1,
                footprint_ref="U1",
                metadata=PcbGeometryMetadata(source_collection="pads", source_index=0),
            ),
            PcbGeometry(
                id="track:1",
                object_type=PcbGeometryObject.TRACK,
                shape=PcbGeometryShape.LINE,
                roles=(
                    PcbGeometryRole.COPPER,
                    PcbGeometryRole.CONDUCTOR,
                    PcbGeometryRole.ROUTE,
                    PcbGeometryRole.TRACE,
                    PcbGeometryRole.BOARD_LEVEL,
                ),
                data=PcbLineGeometry(5.0, 5.0, 8.0, 5.0, 0.25),
                layers=("F.Cu",),
                net_number=1,
            ),
            PcbGeometry(
                id="via:1",
                object_type=PcbGeometryObject.VIA,
                shape=PcbGeometryShape.CIRCLE,
                roles=(
                    PcbGeometryRole.COPPER,
                    PcbGeometryRole.CONDUCTOR,
                    PcbGeometryRole.DRILL,
                    PcbGeometryRole.THROUGH_HOLE,
                    PcbGeometryRole.BOARD_LEVEL,
                ),
                data=PcbViaGeometry(8.0, 5.0, 0.8, 0.35),
                layers=("F.Cu", "B.Cu"),
                net_number=1,
            ),
            PcbGeometry(
                id="silk:1",
                object_type=PcbGeometryObject.GRAPHIC,
                shape=PcbGeometryShape.LINE,
                roles=(
                    PcbGeometryRole.SILKSCREEN,
                    PcbGeometryRole.FOOTPRINT_MEMBER,
                ),
                data=PcbLineGeometry(4.0, 7.0, 6.0, 7.0, 0.12),
                layers=("F.SilkS",),
                footprint_ref="U1",
            ),
            PcbGeometry(
                id="text:U1:ref",
                object_type=PcbGeometryObject.TEXT,
                shape=PcbGeometryShape.TEXT,
                roles=(
                    PcbGeometryRole.SILKSCREEN,
                    PcbGeometryRole.TEXT,
                    PcbGeometryRole.DESIGNATOR,
                    PcbGeometryRole.FOOTPRINT_MEMBER,
                ),
                data=PcbTextGeometry("U1", 5.0, 3.5, 0.0, 1.0),
                layers=("F.SilkS",),
                footprint_ref="U1",
            ),
        ],
    )


def _outline_geometry() -> list[PcbGeometry]:
    points = [(0.0, 0.0), (12.0, 0.0), (12.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    return [
        PcbGeometry(
            id=f"edge:{index}",
            object_type=PcbGeometryObject.GRAPHIC,
            shape=PcbGeometryShape.LINE,
            roles=(
                PcbGeometryRole.EDGE,
                PcbGeometryRole.BOARD_OUTLINE,
                PcbGeometryRole.BOARD_LEVEL,
            ),
            data=PcbLineGeometry(x1, y1, x2, y2, 0.1),
            layers=("Edge.Cuts",),
        )
        for index, ((x1, y1), (x2, y2)) in enumerate(zip(points, points[1:], strict=False))
    ]
