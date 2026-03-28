"""Tests for signal path tracing through 2-pin passives."""

import pytest
from phosphor_eda.schematic import Component, Design, Net, Page, Pin
from phosphor_eda.trace import (
    find_paths,
    is_two_pin_passive,
    trace_from_net,
)


def _make_comp(ref: str, part: str, n_pins: int, page: Page) -> Component:
    comp = Component(reference=ref, part=part, description="", pages=[page])
    for i in range(1, n_pins + 1):
        pin = Pin(designator=str(i), name=f"P{i}", component=comp, metadata={})
        comp.pins.append(pin)
    page.components.append(comp)
    return comp


def _connect(pin: Pin, net: Net) -> None:
    pin.net = net
    net.pins.append(pin)


def _trace_design() -> Design:
    """Build a design with known topology for trace testing.

    Topology:
        U1.1 --[SIG_A]-- R1.1  R1.2 --[SIG_B]-- U2.1    (series R1)
        SIG_A also has R2.1, R2.2 --[P3V3]               (pull-up R2)
        P3V3 has C1.1, C1.2 --[GND]                      (decoupling C1)
        U1.2 --[SIG_C]-- R3.1  R3.2 --[SIG_D]-- FB1.1  FB1.2 --[SIG_E]-- U3.1
        U1.3 --[GND] (power pin, should be skipped)
    """
    page = Page(name="Main")

    u1 = _make_comp("U1", "MCU", 4, page)
    u2 = _make_comp("U2", "ADC", 4, page)
    u3 = _make_comp("U3", "FPGA", 4, page)
    r1 = _make_comp("R1", "100R", 2, page)
    r2 = _make_comp("R2", "4k7", 2, page)
    r3 = _make_comp("R3", "33R", 2, page)
    fb1 = _make_comp("FB1", "Ferrite", 2, page)
    c1 = _make_comp("C1", "100nF", 2, page)

    sig_a = Net(name="SIG_A")
    sig_b = Net(name="SIG_B")
    sig_c = Net(name="SIG_C")
    sig_d = Net(name="SIG_D")
    sig_e = Net(name="SIG_E")
    p3v3 = Net(name="P3V3")
    gnd = Net(name="GND")

    # U1.1 -> SIG_A -> R1.1 (series), R2.1 (pull-up)
    _connect(u1.pins[0], sig_a)
    _connect(r1.pins[0], sig_a)
    _connect(r2.pins[0], sig_a)

    # R1.2 -> SIG_B -> U2.1
    _connect(r1.pins[1], sig_b)
    _connect(u2.pins[0], sig_b)

    # R2.2 -> P3V3 (pull-up)
    _connect(r2.pins[1], p3v3)

    # C1 decoupling: P3V3 -> GND
    _connect(c1.pins[0], p3v3)
    _connect(c1.pins[1], gnd)

    # U1.2 -> SIG_C -> R3.1, R3.2 -> SIG_D -> FB1.1, FB1.2 -> SIG_E -> U3.1
    _connect(u1.pins[1], sig_c)
    _connect(r3.pins[0], sig_c)
    _connect(r3.pins[1], sig_d)
    _connect(fb1.pins[0], sig_d)
    _connect(fb1.pins[1], sig_e)
    _connect(u3.pins[0], sig_e)

    # U1.3 -> GND
    _connect(u1.pins[2], gnd)

    all_nets = [sig_a, sig_b, sig_c, sig_d, sig_e, p3v3, gnd]
    page.nets = all_nets
    all_comps = [u1, u2, u3, r1, r2, r3, fb1, c1]

    return Design(name="TRACE_TEST", pages=[page], nets=all_nets, components=all_comps)


# ---- is_two_pin_passive ----


def test_two_pin_passive_resistor():
    page = Page(name="P")
    r = _make_comp("R1", "10k", 2, page)
    assert is_two_pin_passive(r)


def test_two_pin_passive_ferrite_bead():
    page = Page(name="P")
    fb = _make_comp("FB1", "Ferrite", 2, page)
    assert is_two_pin_passive(fb)


def test_not_passive_ic():
    page = Page(name="P")
    u = _make_comp("U1", "MCU", 8, page)
    assert not is_two_pin_passive(u)


def test_not_passive_three_pin():
    page = Page(name="P")
    q = _make_comp("R1", "Network", 3, page)
    assert not is_two_pin_passive(q)


def test_not_passive_connector():
    page = Page(name="P")
    j = _make_comp("J1", "Header", 2, page)
    assert not is_two_pin_passive(j)


# ---- trace_from_net ----


def test_trace_finds_series_passive():
    design = _trace_design()
    sig_a = next(n for n in design.nets if n.name == "SIG_A")
    u1 = next(c for c in design.components if c.reference == "U1")

    results = trace_from_net(sig_a, origin_comp=u1)
    # Should find R1 as series (leads to U2) and R2 as shunt (to P3V3)
    series_results = [r for r in results if r.series_path]
    assert len(series_results) == 1

    r = series_results[0]
    assert r.series_path[0].component.reference == "R1"
    assert r.terminal_pin is not None
    assert r.terminal_pin.component.reference == "U2"


def test_trace_identifies_shunt():
    design = _trace_design()
    sig_a = next(n for n in design.nets if n.name == "SIG_A")
    u1 = next(c for c in design.components if c.reference == "U1")

    results = trace_from_net(sig_a, origin_comp=u1)
    # R2 is a pull-up to P3V3 — should appear as a shunt on the series result
    assert len(results) == 1  # only R1 as series path
    shunt_refs = {c.reference for c, _ in results[0].shunts}
    assert "R2" in shunt_refs


def test_trace_multi_hop():
    design = _trace_design()
    sig_c = next(n for n in design.nets if n.name == "SIG_C")
    u1 = next(c for c in design.components if c.reference == "U1")

    results = trace_from_net(sig_c, origin_comp=u1)
    series_results = [r for r in results if r.terminal_pin is not None]
    assert len(series_results) == 1

    r = series_results[0]
    assert len(r.series_path) == 2
    assert r.series_path[0].component.reference == "R3"
    assert r.series_path[1].component.reference == "FB1"
    assert r.terminal_pin.component.reference == "U3"


def test_trace_shunt_does_not_follow_into_power():
    """A shunt passive (pull-up/decoupling) should not be followed as a series path."""
    design = _trace_design()
    sig_a = next(n for n in design.nets if n.name == "SIG_A")
    u1 = next(c for c in design.components if c.reference == "U1")

    results = trace_from_net(sig_a, origin_comp=u1)
    # R2 is a pull-up to P3V3 — should NOT appear as a series path
    for r in results:
        for wp in r.series_path:
            assert wp.component.reference != "R2"


def test_trace_cycle_detection():
    """Two passives forming a loop should not recurse infinitely."""
    page = Page(name="P")
    r1 = _make_comp("R1", "10k", 2, page)
    r2 = _make_comp("R2", "10k", 2, page)

    net_a = Net(name="LOOP_A")
    net_b = Net(name="LOOP_B")

    _connect(r1.pins[0], net_a)
    _connect(r2.pins[0], net_a)
    _connect(r1.pins[1], net_b)
    _connect(r2.pins[1], net_b)

    page.nets = [net_a, net_b]

    # Should terminate without error
    results = trace_from_net(net_a)
    assert isinstance(results, list)


# ---- find_paths ----


def test_find_paths_series():
    design = _trace_design()
    paths = find_paths(design, "U1", "U2")
    assert len(paths) == 1
    assert paths[0].left_pin.component.reference == "U1"
    assert paths[0].right_pin.component.reference == "U2"
    assert len(paths[0].series) == 1
    assert paths[0].series[0].reference == "R1"


def test_find_paths_multi_hop():
    design = _trace_design()
    paths = find_paths(design, "U1", "U3")
    assert len(paths) == 1
    assert len(paths[0].series) == 2
    refs = [c.reference for c in paths[0].series]
    assert refs == ["R3", "FB1"]


def test_find_paths_no_connection():
    design = _trace_design()
    paths = find_paths(design, "U2", "U3")
    assert paths == []


def test_find_paths_not_found():
    design = _trace_design()
    with pytest.raises(ValueError, match="not found"):
        find_paths(design, "U99", "U1")


def test_find_paths_shunts_collected():
    design = _trace_design()
    paths = find_paths(design, "U1", "U2")
    # R2 is a pull-up on SIG_A, should appear in shunts
    shunt_refs = [c.reference for c, _ in paths[0].shunts]
    assert "R2" in shunt_refs
