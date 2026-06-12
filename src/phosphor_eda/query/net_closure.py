"""Schematic-bridged net closure for PCB net highlighting.

A physical signal often spans several PCB nets, split at series passives
(an ESD diode or termination resistor renames the net on its far side).
Highlighting "the net" should follow the signal the way ``query.trace``
does. PCB and schematic net names are not guaranteed to match, so the
bridge between domains is (reference, pin/pad number) correspondence —
the same invariant ECO sync relies on:

1. PCB pads on the target net map to schematic pins by ref + number.
2. ``trace_from_net`` walks through series 2-pin passives (power nets are
   boundaries, shunts are not followed).
3. Every schematic pin on the traced nets maps back to PCB pads, whose
   net names form the closure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.query.classify import is_power_net
from phosphor_eda.query.trace import trace_from_net

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Net, Pin, Schematic

# A net with this many pins is a distribution rail even when its name is
# decorated past the power-net catalog (e.g. "+5V_SoM"). Signals run 2-14
# pins in practice; crawling through a rail's pullups would put half the
# board in the closure.
_BOUNDARY_FANOUT = 16


def _is_boundary_net(net: Net) -> bool:
    return is_power_net(net.name, net) or len(net.pins) >= _BOUNDARY_FANOUT


def connected_pcb_net_names(
    board: Board, schematic: Schematic, pcb_net_name: str
) -> frozenset[str]:
    """PCB net names electrically continuous with *pcb_net_name*.

    Follows series 2-pin passives through the schematic. The result always
    contains *pcb_net_name* itself; a boundary net — power, or rail-like
    fan-out — and a net whose pads can't be matched to schematic pins map
    to just themselves.
    """
    if is_power_net(pcb_net_name):
        return frozenset({pcb_net_name})

    pins_by_key = _schematic_pins_by_ref_and_number(schematic)
    seed_nets: dict[str, Net] = {}
    for reference, number in _pcb_pad_keys_on_net(board, pcb_net_name):
        pin = pins_by_key.get((reference, number))
        if pin is not None and pin.net is not None:
            seed_nets[pin.net.id] = pin.net

    if any(_is_boundary_net(net) for net in seed_nets.values()):
        return frozenset({pcb_net_name})

    closure_nets = dict(seed_nets)
    for net in seed_nets.values():
        for result in trace_from_net(net, is_boundary=_is_boundary_net):
            for waypoint in result.series_path:
                closure_nets[waypoint.exit_net.id] = waypoint.exit_net

    pcb_net_by_key = _pcb_net_names_by_pad_key(board)
    names = {pcb_net_name}
    for net in closure_nets.values():
        for pin in net.pins:
            key = (pin.component.reference.upper(), pin.designator.upper())
            pcb_name = pcb_net_by_key.get(key)
            if pcb_name is not None and not is_power_net(pcb_name):
                names.add(pcb_name)
    return frozenset(names)


def _pcb_pad_keys_on_net(board: Board, net_name: str) -> set[tuple[str, str]]:
    return {
        (pad.footprint.reference.upper(), pad.number.upper())
        for pad in board.pads
        if pad.net is not None
        and pad.net.name == net_name
        and pad.footprint is not None
        and pad.number
    }


def _pcb_net_names_by_pad_key(board: Board) -> dict[tuple[str, str], str]:
    return {
        (pad.footprint.reference.upper(), pad.number.upper()): pad.net.name
        for pad in board.pads
        if pad.net is not None and pad.footprint is not None and pad.number
    }


def _schematic_pins_by_ref_and_number(schematic: Schematic) -> dict[tuple[str, str], Pin]:
    return {
        (component.reference.upper(), pin.designator.upper()): pin
        for component in schematic.components
        for pin in component.pins
    }
