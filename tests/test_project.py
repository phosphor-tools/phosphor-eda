"""Tests for the unified Project domain model."""

from phosphor_eda.pcb import Pcb, PcbNet
from phosphor_eda.project import (
    DesignRule,
    DiffPair,
    LibraryRef,
    NetClass,
    Project,
    ProjectMetadata,
    Stackup,
    StackupLayer,
)
from phosphor_eda.schematic import Schematic


def test_project_metadata_defaults():
    meta = ProjectMetadata()
    assert meta.name == ""
    assert meta.format == ""
    assert meta.source_paths == []


def test_stackup_layer_construction():
    layer = StackupLayer(
        name="F.Cu",
        layer_type="copper",
        thickness_mm=0.035,
        copper_weight_oz=1.0,
        side="front",
    )
    assert layer.name == "F.Cu"
    assert layer.thickness_mm == 0.035
    assert layer.epsilon_r == 0.0  # default


def test_stackup_with_layers():
    stackup = Stackup(
        layers=[
            StackupLayer(name="F.Cu", layer_type="copper", thickness_mm=0.035),
            StackupLayer(name="Prepreg", layer_type="prepreg", thickness_mm=0.1, epsilon_r=4.5),
            StackupLayer(name="In1.Cu", layer_type="copper", thickness_mm=0.035),
        ],
        total_thickness_mm=1.6,
        copper_finish="ENIG",
    )
    assert len(stackup.layers) == 3
    assert stackup.copper_finish == "ENIG"
    assert stackup.layers[1].epsilon_r == 4.5


def test_net_class_with_members():
    nc = NetClass(
        name="USB_SS",
        clearance_mm=0.15,
        trace_width_mm=0.12,
        diff_pair_width_mm=0.12,
        diff_pair_gap_mm=0.14,
        members=["USB_D+", "USB_D-"],
    )
    assert nc.name == "USB_SS"
    assert len(nc.members) == 2
    assert nc.kind == 0  # default net-level


def test_design_rule_construction():
    rule = DesignRule(
        name="usb_2.0_inner",
        kind="track_width",
        layer_scope="inner",
        preferred_value_mm=0.16,
        scope1="A.NetClass == '85Ohm-diff_USB_2.0'",
    )
    assert rule.name == "usb_2.0_inner"
    assert rule.enabled is True
    assert rule.min_value_mm is None
    assert rule.preferred_value_mm == 0.16


def test_diff_pair_construction():
    dp = DiffPair(name="HDMI_TX0", positive_net="HDMI_TX0_P", negative_net="HDMI_TX0_N")
    assert dp.positive_net == "HDMI_TX0_P"
    assert dp.negative_net == "HDMI_TX0_N"


def test_library_ref_construction():
    ref = LibraryRef(name="Device", kind="symbol", uri="/usr/share/kicad/symbols/Device.kicad_sym")
    assert ref.kind == "symbol"


def test_project_with_all_submodels():
    project = Project(
        name="test-board",
        metadata=ProjectMetadata(
            name="test-board",
            format="kicad",
            format_version="8",
            author="Test",
        ),
        schematic=Schematic(name="test-board"),
        pcb=Pcb(
            name="test-board",
            layers=[],
            nets={1: PcbNet(number=1, name="VCC")},
            footprints=[],
            pads=[],
            vias=[],
            drills=[],
            conductors=[],
            artwork=[],
            pours=[],
            keepouts=[],
        ),
        stackup=Stackup(
            layers=[StackupLayer(name="F.Cu", layer_type="copper", thickness_mm=0.035)],
            total_thickness_mm=1.6,
        ),
        net_classes=[NetClass(name="Default", clearance_mm=0.2)],
        design_rules=[DesignRule(name="clearance1", kind="clearance", min_value_mm=0.125)],
        diff_pairs=[DiffPair(name="USB", positive_net="USB_P", negative_net="USB_N")],
        library_refs=[LibraryRef(name="Device", kind="symbol")],
    )
    assert project.name == "test-board"
    assert project.schematic is not None
    assert project.pcb is not None
    assert project.stackup is not None
    assert len(project.net_classes) == 1
    assert len(project.design_rules) == 1
    assert len(project.diff_pairs) == 1
    assert len(project.library_refs) == 1


def test_project_minimal():
    """Project with only required fields — all optionals are None/empty."""
    project = Project(name="empty")
    assert project.schematic is None
    assert project.pcb is None
    assert project.stackup is None
    assert project.net_classes == []
    assert project.design_rules == []
    assert project.diff_pairs == []
    assert project.library_refs == []
