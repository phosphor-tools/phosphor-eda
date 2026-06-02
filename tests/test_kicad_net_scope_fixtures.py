"""KiCad fixture regressions for resolved net scope behavior."""

from pathlib import Path

from phosphor_eda.kicad.resolver import resolve_kicad_source
from phosphor_eda.kicad.to_schematic import kicad_to_source
from phosphor_eda.schematic import Net

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HIERARCHY_ROOT = FIXTURES / "kicad-hierarchy" / "root.kicad_sch"
REPEATED_ROOT = FIXTURES / "kicad-repeated-sheet" / "root.kicad_sch"


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


def test_hierarchy_fixture_keeps_unwired_same_name_root_label_separate() -> None:
    design = resolve_kicad_source(kicad_to_source(HIERARCHY_ROOT))

    assert _refs(_net_for_reference(design.nets, "R1")) == {"R1"}
    assert _refs(_net_for_reference(design.nets, "R2")) == {"R2"}


def test_repeated_sheet_fixture_keeps_unwired_child_instances_distinct() -> None:
    design = resolve_kicad_source(kicad_to_source(REPEATED_ROOT))
    r1_nets = [
        net for net in design.nets if any(pin.component.reference == "R1" for pin in net.pins)
    ]

    sig_nets = [
        net
        for net in r1_nets
        if any(occurrence.source_names == {"SIG_IN"} for occurrence in net.occurrences)
    ]

    assert len(sig_nets) == 2
    assert all(_refs(net) == {"R1"} for net in sig_nets)


def test_repeated_sheet_fixture_unconnected_global_label_does_not_emit_empty_net() -> None:
    design = resolve_kicad_source(kicad_to_source(REPEATED_ROOT))

    assert all(net.name != "SYNC" for net in design.nets)
