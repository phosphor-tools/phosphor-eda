"""Tests for enriched Altium PCB parser: rules, classes, stackup, typed PCB objects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from phosphor_eda.domain.pcb import (
    Board,
    PcbArc,
    PcbArtworkKind,
    PcbCircle,
    PcbConductorKind,
    PcbKeepoutPermission,
    PcbLine,
    PcbPolygon,
)
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.pcb_primitives import read_text_records
from phosphor_eda.formats.altium.pcb_project import (
    parse_altium_classes,
    parse_altium_diff_pairs,
    parse_altium_rules,
    parse_altium_stackup,
)

if TYPE_CHECKING:
    from phosphor_eda.domain.project import Stackup

FIXTURE = Path(__file__).parent / "fixtures" / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="Fixture not available")


@pytest.fixture(scope="module")
def ole_streams() -> dict[str, bytes]:
    import olefile

    ole = olefile.OleFileIO(str(FIXTURE))
    streams = {
        "rules": ole.openstream("Rules6/Data").read(),
        "classes": ole.openstream("Classes6/Data").read(),
        "diffpairs": ole.openstream("DifferentialPairs6/Data").read(),
        "board": ole.openstream("Board6/Data").read(),
    }
    ole.close()
    return streams


@pytest.fixture(scope="module")
def rules(ole_streams: dict[str, bytes]):
    return parse_altium_rules(ole_streams["rules"])


def test_rules_count(rules) -> None:
    assert len(rules) >= 100


def test_rules_kinds(rules) -> None:
    kinds = {rule.kind for rule in rules}
    assert "Clearance" in kinds
    assert "Width" in kinds
    assert "DiffPairsRouting" in kinds


def test_width_rule_values(rules) -> None:
    width_rules = [rule for rule in rules if rule.kind == "Width"]
    assert len(width_rules) == 3
    under_bga = next(rule for rule in width_rules if rule.name == "WIDTH_UNDER_BGA")
    assert under_bga.min_value_mm == pytest.approx(0.08, abs=0.001)
    assert under_bga.preferred_value_mm == pytest.approx(0.1, abs=0.001)
    assert under_bga.max_value_mm is not None
    assert under_bga.max_value_mm > 1.0


def test_clearance_rule_scope(rules) -> None:
    clearance = next(rule for rule in rules if rule.name == "CLR_UNDER_BGA")
    assert "TouchesRoom" in clearance.scope1
    assert clearance.scope2 == "All"


def test_rule_enabled(rules) -> None:
    assert all(rule.enabled for rule in rules)


@pytest.fixture(scope="module")
def classes(ole_streams: dict[str, bytes]):
    return parse_altium_classes(ole_streams["classes"])


def test_classes_count(classes) -> None:
    assert len(classes) == 64


def test_class_kinds(classes) -> None:
    kinds = {item.kind for item in classes}
    assert 0 in kinds
    assert 1 in kinds
    assert 6 in kinds


def test_pmic_class_members(classes) -> None:
    pmic = next(item for item in classes if item.name == "PMIC")
    assert pmic.kind == 0
    assert len(pmic.members) == 9
    assert "PMIC_SDA" in pmic.members
    assert "PMIC_SCL" in pmic.members


def test_diff_pair_class(classes) -> None:
    diff100 = next(item for item in classes if item.name == "DIFF100")
    assert diff100.kind == 6
    assert len(diff100.members) >= 40


@pytest.fixture(scope="module")
def diff_pairs(ole_streams: dict[str, bytes]):
    return parse_altium_diff_pairs(ole_streams["diffpairs"])


def test_diff_pairs_count(diff_pairs) -> None:
    assert len(diff_pairs) == 55


def test_wifi_usb_diff_pair(diff_pairs) -> None:
    wifi = next(pair for pair in diff_pairs if pair.name == "WIFI_USB")
    assert wifi.positive_net == "WIFI_USB_P"
    assert wifi.negative_net == "WIFI_USB_N"


def test_pcie_diff_pair(diff_pairs) -> None:
    assert len([pair for pair in diff_pairs if "PCIe" in pair.name]) >= 2


@pytest.fixture(scope="module")
def stackup(ole_streams: dict[str, bytes]) -> Stackup:
    records = read_text_records(ole_streams["board"])
    board_props = records[0] if records else {}
    result = parse_altium_stackup(board_props)
    assert result is not None, "parse_altium_stackup returned None"
    return result


def test_stackup_layer_count(stackup: Stackup) -> None:
    assert len(stackup.layers) == 23


def test_stackup_copper_layers(stackup: Stackup) -> None:
    copper = [layer for layer in stackup.layers if layer.layer_type == "copper"]
    assert len(copper) == 10


def test_stackup_solder_mask_layers(stackup: Stackup) -> None:
    masks = [layer for layer in stackup.layers if layer.layer_type == "solder_mask"]
    assert len(masks) == 2
    assert masks[0].name == "Top Solder"
    assert masks[1].name == "Bottom Solder"


def test_stackup_top_layer(stackup: Stackup) -> None:
    top = next(
        layer for layer in stackup.layers if layer.layer_type == "copper" and layer.side == "front"
    )
    assert top.name == "Top Layer"
    assert top.thickness_mm == pytest.approx(0.036, abs=0.001)


def test_stackup_bottom_layer(stackup: Stackup) -> None:
    copper = [layer for layer in stackup.layers if layer.layer_type == "copper"]
    bottom = copper[-1]
    assert bottom.name == "Bottom Layer"
    assert bottom.side == "back"


def test_stackup_dielectric_properties(stackup: Stackup) -> None:
    dielectric = next(layer for layer in stackup.layers if layer.name == "Dielectric 1")
    assert dielectric.layer_type == "prepreg"
    assert dielectric.material == "PP-1080"
    assert dielectric.epsilon_r == pytest.approx(4.0)
    assert dielectric.thickness_mm == pytest.approx(0.075, abs=0.001)


def test_stackup_core_layer(stackup: Stackup) -> None:
    cores = [layer for layer in stackup.layers if layer.layer_type == "core"]
    assert len(cores) == 3
    assert all(core.epsilon_r == pytest.approx(4.6) for core in cores)


def test_stackup_copper_orientation(stackup: Stackup) -> None:
    copper = [layer for layer in stackup.layers if layer.layer_type == "copper"]
    assert copper[0].copper_orientation == "normal"
    assert copper[-1].copper_orientation == "reversed"


def test_stackup_total_thickness(stackup: Stackup) -> None:
    expected = sum(layer.thickness_mm for layer in stackup.layers)
    assert stackup.total_thickness_mm == pytest.approx(expected)


@pytest.fixture(scope="module")
def pcb() -> Board:
    return parse_altium_pcb(FIXTURE)


def test_altium_keepout_arcs_are_not_visible_trace_arcs(pcb: Board) -> None:
    visible_keepout_rings = [
        conductor
        for conductor in pcb.conductors
        if conductor.kind == PcbConductorKind.TRACE_ARC
        and conductor.layer.name in {"Top Layer", "Bottom Layer"}
        and conductor.net is None
        and isinstance(conductor.data, PcbArc)
        and conductor.data.width == pytest.approx(0.2, abs=0.001)
        and conductor.data.start_x == pytest.approx(conductor.data.end_x, abs=1e-6)
        and conductor.data.start_y == pytest.approx(conductor.data.end_y, abs=1e-6)
    ]

    assert visible_keepout_rings == []


def test_altium_keepout_arcs_are_preserved_as_queryable_keepouts(pcb: Board) -> None:
    keepout_rings = [
        item
        for item in pcb.keepouts
        if {layer.name for layer in item.layers} <= {"Top Layer", "Bottom Layer"}
        and item.rules.tracks == PcbKeepoutPermission.NOT_ALLOWED
        and item.rules.vias == PcbKeepoutPermission.NOT_ALLOWED
        and item.rules.pads == PcbKeepoutPermission.NOT_ALLOWED
        and item.rules.copper_pours == PcbKeepoutPermission.NOT_ALLOWED
    ]

    assert len(keepout_rings) >= 8
    assert all(len(item.boundary.segments) >= 16 for item in keepout_rings)


def test_altium_board_level_solder_mask_lines_and_arcs_are_preserved(pcb: Board) -> None:
    top_solder_lines = _artwork_matching(pcb, "Top Solder", PcbLine, footprint_owned=False)
    bottom_solder_lines = _artwork_matching(pcb, "Bottom Solder", PcbLine, footprint_owned=False)
    top_solder_arcs = _artwork_matching(pcb, "Top Solder", PcbArc, footprint_owned=False)
    bottom_solder_arcs = _artwork_matching(pcb, "Bottom Solder", PcbArc, footprint_owned=False)

    assert len(top_solder_lines) == 4
    assert len(bottom_solder_lines) == 4
    assert len(top_solder_arcs) == 4
    assert len(bottom_solder_arcs) == 4
    assert all(item.data.width == pytest.approx(0.1, abs=0.001) for item in top_solder_lines)
    assert all(item.data.width == pytest.approx(0.1, abs=0.001) for item in top_solder_arcs)


def test_altium_component_non_silk_fab_graphics_are_preserved(pcb: Board) -> None:
    assert _artwork_matching(pcb, "Top Paste", PcbLine, footprint_owned=True)
    assert _artwork_matching(pcb, "Bottom Paste", PcbLine, footprint_owned=True)
    assert _artwork_matching(pcb, "Top 3D Body", PcbCircle, footprint_owned=True)
    assert _artwork_matching(pcb, "Bottom 3D Body", PcbCircle, footprint_owned=True)


def test_polygons_with_holes(pcb: Board) -> None:
    polygons = _polygon_payloads(pcb)
    polys_with_holes = [poly for poly in polygons if poly.holes]
    assert len(polys_with_holes) >= 30
    assert any(len(hole) > 300 for poly in polys_with_holes for hole in poly.holes)


def test_polygon_hole_structure(pcb: Board) -> None:
    poly = next(poly for poly in _polygon_payloads(pcb) if poly.holes)
    hole = poly.holes[0]
    assert len(hole) >= 3
    assert len(hole[0]) == 2


def test_duplicate_shape_based_board_copper_polygons_are_omitted(pcb: Board) -> None:
    copper_area = [
        conductor
        for conductor in pcb.conductors
        if isinstance(conductor.data, PcbPolygon)
        and conductor.layer.name == "Top Layer"
        and _polygon_bounds_key(conductor.data, conductor.layer.name)
        == ("Top Layer", 100.871, -146.479, 136.221, -134.629)
    ]

    assert len(copper_area) == 1


def test_polygon_cutout_regions_are_not_emitted_as_copper(pcb: Board) -> None:
    cutout_area = [
        conductor
        for conductor in pcb.conductors
        if isinstance(conductor.data, PcbPolygon)
        and conductor.layer.name == "Top Layer"
        and _polygon_bounds_key(conductor.data, conductor.layer.name)
        == ("Top Layer", 88.821, -173.629, 91.321, -171.129)
    ]

    assert cutout_area == []


def test_altium_polygon_pours_are_intent_not_top_level_conductors(pcb: Board) -> None:
    assert pcb.pours
    assert not any(conductor.id.startswith("polygon_pour:") for conductor in pcb.conductors)
    assert any(pour.id.startswith("polygon_pour:") for pour in pcb.pours)


def test_altium_pour_fill_children_are_linked_to_parent_pours(pcb: Board) -> None:
    pour_fill = [
        conductor
        for conductor in pcb.conductors
        if conductor.kind == PcbConductorKind.POUR_FILL and conductor.pour is not None
    ]

    assert pour_fill
    assert all(conductor.kind != PcbConductorKind.TRACE for conductor in pour_fill)
    assert all(pcb.pour_for(conductor.pour.id) is conductor.pour for conductor in pour_fill)
    assert any(pour.fills for pour in pcb.pours)


def test_pimx8_problematic_polygon_boundary_is_not_renderable_copper(pcb: Board) -> None:
    assert pcb.pour_for("polygon_pour:29:6") is not None
    assert not any(conductor.id == "polygon_pour:29:6" for conductor in pcb.conductors)
    arc = next(conductor for conductor in pcb.conductors if conductor.id == "arc:1:46")
    assert arc.pour is None
    assert arc.kind == PcbConductorKind.TRACE_ARC


def _artwork_matching(
    pcb: Board,
    layer_name: str,
    payload_type: type[PcbLine] | type[PcbArc] | type[PcbCircle],
    *,
    footprint_owned: bool,
):
    return [
        item
        for item in pcb.artwork
        if item.layer is not None
        and item.layer.name == layer_name
        and isinstance(item.data, payload_type)
        and (item.footprint is not None) is footprint_owned
    ]


def _polygon_payloads(pcb: Board) -> list[PcbPolygon]:
    polygons: list[PcbPolygon] = []
    for conductor in pcb.conductors:
        if isinstance(conductor.data, PcbPolygon):
            polygons.append(conductor.data)
    for artwork in pcb.artwork:
        if artwork.kind == PcbArtworkKind.POLYGON and isinstance(artwork.data, PcbPolygon):
            polygons.append(artwork.data)
    return polygons


def _polygon_bounds_key(
    polygon: PcbPolygon,
    layer_name: str,
) -> tuple[str, float, float, float, float]:
    xs = [x for x, _y in polygon.points]
    ys = [y for _x, y in polygon.points]
    return (
        layer_name,
        round(min(xs), 3),
        round(min(ys), 3),
        round(max(xs), 3),
        round(max(ys), 3),
    )
