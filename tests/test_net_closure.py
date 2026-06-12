"""Tests for schematic-bridged PCB net closure (highlight traversal).

The closure maps a PCB net to every PCB net that is electrically continuous
with it through series 2-pin passives, using the schematic ``trace`` walk.
The bridge is (reference, pin/pad number) — schematic and PCB net names are
deliberately different in these fixtures to prove names never need to match.
"""

from __future__ import annotations

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PcbFootprint,
    PcbLayer,
    PcbNet,
    PcbPad,
    PcbPadType,
)
from phosphor_eda.domain.schematic import Component, Net, Pin, Schematic
from phosphor_eda.query.net_closure import connected_pcb_net_names

_LAYER = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT), number=0)


def _schematic() -> Schematic:
    """J1.2 — usb_dp_conn — R21 — usb_dp_fpga — U3.A1, with R20 pullup to
    pullup_net (U3.B2) and decoupling C5 from usb_dp_fpga to GND."""
    schematic = Schematic(name="test")

    nets = {
        "conn": Net(id="n1", name="USB_DP_CONN"),
        "fpga": Net(id="n2", name="USB_DP_FPGA"),
        "pullup": Net(id="n3", name="PULLUP_NET"),
        "gnd": Net(id="n4", name="GND"),
    }

    def component(reference: str, pin_nets: dict[str, Net | None]) -> Component:
        comp = Component(id=f"c-{reference}", reference=reference, part="", description="")
        for designator, net in pin_nets.items():
            pin = Pin(
                id=f"{reference}.{designator}",
                designator=designator,
                name=designator,
                component=comp,
                net=net,
            )
            comp.pins.append(pin)
            if net is not None:
                net.pins.append(pin)
        return comp

    schematic.components = [
        component("J1", {"1": nets["gnd"], "2": nets["conn"]}),
        component("R21", {"1": nets["fpga"], "2": nets["conn"]}),
        component("R20", {"1": nets["conn"], "2": nets["pullup"]}),
        component("C5", {"1": nets["fpga"], "2": nets["gnd"]}),
        component("U3", {"A1": nets["fpga"], "B2": nets["pullup"], "C3": nets["gnd"]}),
    ]
    schematic.nets = list(nets.values())
    return schematic


def _board() -> Board:
    """PCB pads matching the schematic refs/pins, with different net names."""
    pcb_nets = {
        "conn": PcbNet(1, "USB_D+"),
        "fpga": PcbNet(2, "/IO/_USB_D_P"),
        "pullup": PcbNet(3, "USB_PULLUP"),
        "gnd": PcbNet(4, "GND"),
    }
    pad_map = [
        ("J1", "1", "gnd"),
        ("J1", "2", "conn"),
        ("R21", "1", "fpga"),
        ("R21", "2", "conn"),
        ("R20", "1", "conn"),
        ("R20", "2", "pullup"),
        ("C5", "1", "fpga"),
        ("C5", "2", "gnd"),
        ("U3", "A1", "fpga"),
        ("U3", "B2", "pullup"),
        ("U3", "C3", "gnd"),
    ]
    footprints: dict[str, PcbFootprint] = {}
    pads: list[PcbPad] = []
    for reference, number, net_key in pad_map:
        footprint = footprints.setdefault(
            reference,
            PcbFootprint(reference, "lib:fp", 0.0, 0.0, 0.0, _LAYER),
        )
        pads.append(
            PcbPad(
                id=f"pad:{reference}:{number}",
                number=number,
                x=0.0,
                y=0.0,
                width=1.0,
                height=1.0,
                shape="rect",
                pad_type=PcbPadType.SMD,
                layers=(_LAYER,),
                net=pcb_nets[net_key],
                footprint=footprint,
            )
        )
    return Board(
        name="test",
        layers=[_LAYER],
        nets={net.number: net for net in pcb_nets.values()},
        footprints=list(footprints.values()),
        pads=pads,
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
    )


def test_closure_follows_series_passives_across_differing_net_names() -> None:
    closure = connected_pcb_net_names(_board(), _schematic(), "USB_D+")

    assert closure == frozenset({"USB_D+", "/IO/_USB_D_P", "USB_PULLUP"})


def test_closure_from_the_far_side_reaches_the_connector_stub() -> None:
    closure = connected_pcb_net_names(_board(), _schematic(), "/IO/_USB_D_P")

    assert closure == frozenset({"USB_D+", "/IO/_USB_D_P", "USB_PULLUP"})


def test_power_net_is_not_traversed() -> None:
    closure = connected_pcb_net_names(_board(), _schematic(), "GND")

    assert closure == frozenset({"GND"})


def test_decoupling_cap_does_not_bridge_into_power() -> None:
    closure = connected_pcb_net_names(_board(), _schematic(), "USB_D+")

    assert "GND" not in closure


def test_unknown_net_returns_itself() -> None:
    closure = connected_pcb_net_names(_board(), _schematic(), "DOES_NOT_EXIST")

    assert closure == frozenset({"DOES_NOT_EXIST"})


def test_unmatched_references_fall_back_to_the_original_net() -> None:
    schematic = Schematic(name="empty")

    closure = connected_pcb_net_names(_board(), schematic, "USB_D+")

    assert closure == frozenset({"USB_D+"})


def test_decorated_rail_is_a_fanout_boundary() -> None:
    """A high-fanout net is a distribution rail even when its name doesn't
    match the power catalog; closure must not crawl through its pullups."""
    schematic = Schematic(name="rail")
    signal = Net(id="n1", name="DATA")
    rail = Net(id="n2", name="+5V_SOM_RAIL")  # not in the power-name catalog
    victim = Net(id="n3", name="UNRELATED")

    def component(reference: str, pin_nets: dict[str, Net]) -> Component:
        comp = Component(id=f"c-{reference}", reference=reference, part="", description="")
        for designator, net in pin_nets.items():
            pin = Pin(
                id=f"{reference}.{designator}",
                designator=designator,
                name=designator,
                component=comp,
                net=net,
            )
            comp.pins.append(pin)
            net.pins.append(pin)
        return comp

    components = [
        component("R1", {"1": signal, "2": rail}),
        component("R2", {"1": rail, "2": victim}),
    ]
    # Enough rail pins to cross the fan-out boundary threshold.
    components.extend(
        component(f"C{index}", {"1": rail, "2": Net(id=f"g{index}", name=f"X{index}")})
        for index in range(20)
    )
    schematic.components = components
    schematic.nets = [signal, rail, victim]

    pcb_nets = {"DATA": PcbNet(1, "DATA"), "RAIL": PcbNet(2, "RAIL"), "U": PcbNet(3, "UNRELATED")}
    footprints: dict[str, PcbFootprint] = {}
    pads: list[PcbPad] = []
    pad_map = [("R1", "1", "DATA"), ("R1", "2", "RAIL"), ("R2", "1", "RAIL"), ("R2", "2", "U")]
    for reference, number, net_key in pad_map:
        footprint = footprints.setdefault(
            reference,
            PcbFootprint(reference, "lib:fp", 0.0, 0.0, 0.0, _LAYER),
        )
        pads.append(
            PcbPad(
                id=f"pad:{reference}:{number}",
                number=number,
                x=0.0,
                y=0.0,
                width=1.0,
                height=1.0,
                shape="rect",
                pad_type=PcbPadType.SMD,
                layers=(_LAYER,),
                net=pcb_nets[net_key],
                footprint=footprint,
            )
        )
    board = Board(
        name="rail",
        layers=[_LAYER],
        nets={net.number: net for net in pcb_nets.values()},
        footprints=list(footprints.values()),
        pads=pads,
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
    )

    assert connected_pcb_net_names(board, schematic, "DATA") == frozenset({"DATA"})


def test_multi_pin_component_is_not_traversed_through() -> None:
    # U3 touches fpga, pullup, and gnd nets, but active components are
    # endpoints; only the R20/R21 passives may join nets.
    closure = connected_pcb_net_names(_board(), _schematic(), "USB_PULLUP")

    assert closure == frozenset({"USB_D+", "/IO/_USB_D_P", "USB_PULLUP"})
