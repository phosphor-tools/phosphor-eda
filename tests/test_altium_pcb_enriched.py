"""Tests for enriched Altium PCB parser: rules, classes, diff pairs, stackup, holes.

Uses the Pi.MX8 fixture (CERN-OHL-S-2.0 licensed).
"""

from pathlib import Path

import pytest

from phosphor_eda.altium.pcb_parser import (
    parse_altium_classes,
    parse_altium_diff_pairs,
    parse_altium_pcb,
    parse_altium_rules,
    parse_altium_stackup,
    read_text_records,
)
from phosphor_eda.pcb import Pcb, PcbPolygon
from phosphor_eda.project import Stackup

FIXTURE = Path(__file__).parent / "fixtures" / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="Fixture not available")


# ---------------------------------------------------------------------------
# Helpers to read streams
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ole_streams() -> dict[str, bytes]:
    """Read relevant OLE streams once for all tests."""
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


# ---------------------------------------------------------------------------
# Rules6
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rules(ole_streams: dict[str, bytes]):
    return parse_altium_rules(ole_streams["rules"])


def test_rules_count(rules) -> None:
    """Should parse 100+ rules from this complex board."""
    assert len(rules) >= 100


def test_rules_kinds(rules) -> None:
    """Multiple rule kinds present."""
    kinds = {r.kind for r in rules}
    assert "Clearance" in kinds
    assert "Width" in kinds
    assert "DiffPairsRouting" in kinds


def test_width_rule_values(rules) -> None:
    """Width rules have min/max/preferred values in mm."""
    width_rules = [r for r in rules if r.kind == "Width"]
    assert len(width_rules) == 3
    # WIDTH_UNDER_BGA: minlimit=3.1496mil → ~0.08mm, preferedwidth=3.937mil → ~0.1mm
    under_bga = next(r for r in width_rules if r.name == "WIDTH_UNDER_BGA")
    assert under_bga.min_value_mm == pytest.approx(0.08, abs=0.001)
    assert under_bga.preferred_value_mm == pytest.approx(0.1, abs=0.001)
    assert under_bga.max_value_mm is not None
    assert under_bga.max_value_mm > 1.0


def test_clearance_rule_scope(rules) -> None:
    """Clearance rules preserve scope expressions."""
    clr = next(r for r in rules if r.name == "CLR_UNDER_BGA")
    assert "TouchesRoom" in clr.scope1
    assert clr.scope2 == "All"


def test_rule_enabled(rules) -> None:
    """Rules have enabled flag parsed."""
    # All rules in this fixture are enabled
    assert all(r.enabled for r in rules)


# ---------------------------------------------------------------------------
# Classes6
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def classes(ole_streams: dict[str, bytes]):
    return parse_altium_classes(ole_streams["classes"])


def test_classes_count(classes) -> None:
    """Should parse 64 classes across multiple kinds."""
    assert len(classes) == 64


def test_class_kinds(classes) -> None:
    """Multiple class kinds present (net=0, component=1, diff pair=6, etc.)."""
    kinds = {c.kind for c in classes}
    assert 0 in kinds  # Net classes
    assert 1 in kinds  # Component classes
    assert 6 in kinds  # Diff pair classes


def test_pmic_class_members(classes) -> None:
    """PMIC net class has correct members."""
    pmic = next(c for c in classes if c.name == "PMIC")
    assert pmic.kind == 0
    assert len(pmic.members) == 9
    assert "PMIC_SDA" in pmic.members
    assert "PMIC_SCL" in pmic.members


def test_diff_pair_class(classes) -> None:
    """DIFF100 class (kind=6) has members."""
    diff100 = next(c for c in classes if c.name == "DIFF100")
    assert diff100.kind == 6
    assert len(diff100.members) >= 40


# ---------------------------------------------------------------------------
# DifferentialPairs6
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def diff_pairs(ole_streams: dict[str, bytes]):
    return parse_altium_diff_pairs(ole_streams["diffpairs"])


def test_diff_pairs_count(diff_pairs) -> None:
    """Should parse 55 differential pairs."""
    assert len(diff_pairs) == 55


def test_wifi_usb_diff_pair(diff_pairs) -> None:
    """WIFI_USB pair has correct positive/negative nets."""
    wifi = next(p for p in diff_pairs if p.name == "WIFI_USB")
    assert wifi.positive_net == "WIFI_USB_P"
    assert wifi.negative_net == "WIFI_USB_N"


def test_pcie_diff_pair(diff_pairs) -> None:
    """PCIe differential pairs are present."""
    pcie_pairs = [p for p in diff_pairs if "PCIe" in p.name]
    assert len(pcie_pairs) >= 2


# ---------------------------------------------------------------------------
# PCB keepouts
# ---------------------------------------------------------------------------


def test_altium_keepout_arcs_are_not_visible_trace_arcs(pcb: Pcb) -> None:
    visible_keepout_rings = [
        arc
        for arc in pcb.trace_arcs
        if arc.layer in {"Top Layer", "Bottom Layer"}
        and arc.net_number == 0
        and arc.width == pytest.approx(0.2, abs=0.001)
        and arc.start_x == pytest.approx(arc.end_x, abs=1e-6)
        and arc.start_y == pytest.approx(arc.end_y, abs=1e-6)
    ]

    assert visible_keepout_rings == []


def test_altium_keepout_arcs_are_preserved_as_queryable_keepouts(pcb: Pcb) -> None:
    keepout_rings = [
        keepout
        for keepout in pcb.keepouts
        if set(keepout.layers) <= {"Top Layer", "Bottom Layer"}
        and keepout.rules.tracks == "not_allowed"
        and keepout.rules.vias == "not_allowed"
        and keepout.rules.pads == "not_allowed"
        and keepout.rules.copperpour == "not_allowed"
    ]

    assert len(keepout_rings) >= 8
    assert any("Top Layer" in keepout.layers for keepout in keepout_rings)
    assert any("Bottom Layer" in keepout.layers for keepout in keepout_rings)
    assert all(len(keepout.boundary) >= 16 for keepout in keepout_rings)


# ---------------------------------------------------------------------------
# Stackup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stackup(ole_streams: dict[str, bytes]) -> Stackup:
    records = read_text_records(ole_streams["board"])
    board_props = records[0] if records else {}
    result = parse_altium_stackup(board_props)
    assert result is not None, "parse_altium_stackup returned None"
    return result


def test_stackup_exists(stackup) -> None:
    """Stackup is parsed successfully."""
    assert stackup is not None


def test_stackup_layer_count(stackup) -> None:
    """v9 stackup: 10 copper + 2 solder mask + 8 prepreg + 3 core = 23 layers."""
    assert len(stackup.layers) == 23


def test_stackup_copper_layers(stackup) -> None:
    """10 copper layers present."""
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper) == 10


def test_stackup_solder_mask_layers(stackup) -> None:
    """Top and bottom solder masks included from v9 format."""
    masks = [ly for ly in stackup.layers if ly.layer_type == "solder_mask"]
    assert len(masks) == 2
    assert masks[0].name == "Top Solder"
    assert masks[1].name == "Bottom Solder"


def test_stackup_top_layer(stackup) -> None:
    """Top copper layer has correct thickness and side."""
    # First layer is solder mask, second is top copper
    top = next(ly for ly in stackup.layers if ly.layer_type == "copper" and ly.side == "front")
    assert top.name == "Top Layer"
    # 1.4173mil → ~0.036mm
    assert top.thickness_mm == pytest.approx(0.036, abs=0.001)


def test_stackup_bottom_layer(stackup) -> None:
    """Bottom copper layer is last copper and has correct side."""
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    bottom = copper[-1]
    assert bottom.name == "Bottom Layer"
    assert bottom.side == "back"


def test_stackup_dielectric_properties(stackup) -> None:
    """First prepreg (Dielectric 1) has material and epsilon_r."""
    diel1 = next(ly for ly in stackup.layers if ly.name == "Dielectric 1")
    assert diel1.layer_type == "prepreg"
    assert diel1.material == "PP-1080"
    assert diel1.epsilon_r == pytest.approx(4.0)
    # 2.9528mil → ~0.075mm
    assert diel1.thickness_mm == pytest.approx(0.075, abs=0.001)


def test_stackup_core_layer(stackup) -> None:
    """Core layers have correct type and epsilon_r."""
    cores = [ly for ly in stackup.layers if ly.layer_type == "core"]
    assert len(cores) == 3
    assert all(c.epsilon_r == pytest.approx(4.6) for c in cores)


def test_stackup_copper_orientation(stackup) -> None:
    """Copper orientation is parsed (normal vs reversed foil)."""
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    # Top layers have normal orientation, inner layers 5-8 have reversed
    assert copper[0].copper_orientation == "normal"  # Top Layer
    assert copper[-1].copper_orientation == "reversed"  # Bottom Layer


def test_stackup_total_thickness(stackup) -> None:
    """Total thickness is sum of all layers."""
    expected = sum(ly.thickness_mm for ly in stackup.layers)
    assert stackup.total_thickness_mm == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Polygon holes
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pcb() -> Pcb:
    return parse_altium_pcb(FIXTURE)


def test_polygons_with_holes(pcb) -> None:
    """Polygons with holes are preserved."""
    polys_with_holes = [p for p in pcb.polygons if p.holes]
    assert len(polys_with_holes) >= 30
    assert any(len(hole) > 300 for poly in polys_with_holes for hole in poly.holes)


def test_polygon_hole_structure(pcb) -> None:
    """Each hole is a list of at least 3 coordinate pairs."""
    poly = next(p for p in pcb.polygons if p.holes)
    hole = poly.holes[0]
    assert len(hole) >= 3
    # Each point is a (float, float) tuple
    assert len(hole[0]) == 2


def test_duplicate_shape_based_board_copper_polygons_are_omitted(pcb) -> None:
    """Altium Regions6 and ShapeBasedRegions6 can duplicate board copper polygons."""
    copper_area = [
        poly
        for poly in pcb.polygons
        if poly.layer == "Top Layer"
        and _polygon_bounds_key(poly) == ("Top Layer", 100.871, -146.479, 136.221, -134.629)
    ]

    assert len(copper_area) == 1


def test_polygon_cutout_regions_are_not_emitted_as_copper(pcb) -> None:
    cutout_area = [
        poly
        for poly in pcb.polygons
        if poly.layer == "Top Layer"
        and _polygon_bounds_key(poly) == ("Top Layer", 88.821, -173.629, 91.321, -171.129)
    ]

    assert cutout_area == []


def _polygon_bounds_key(poly: PcbPolygon) -> tuple[str, float, float, float, float]:
    xs = [x for x, _y in poly.points]
    ys = [y for _x, y in poly.points]
    return (
        poly.layer,
        round(min(xs), 3),
        round(min(ys), 3),
        round(max(xs), 3),
        round(max(ys), 3),
    )
