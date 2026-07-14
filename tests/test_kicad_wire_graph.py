"""Tests for KiCad wire and bus graph geometry."""

from __future__ import annotations

import sexpdata

from phosphor_eda.formats.kicad.wire_graph import build_bus_graph, build_wire_graph


def test_isolated_junction_does_not_touch_wire_or_bus_segments() -> None:
    data = sexpdata.loads(
        """
        (kicad_sch
          (wire (pts (xy 0 0) (xy 10 0)))
          (bus (pts (xy 0 10) (xy 10 10)))
          (junction (at 20 20))
        )
        """
    )

    wire_graph = build_wire_graph(data)
    bus_graph = build_bus_graph(data)

    assert wire_graph.touches_wire((5, 0))
    assert bus_graph.touches_bus((5, 10))
    assert not wire_graph.touches_wire((20, 20))
    assert not bus_graph.touches_bus((20, 20))


def test_label_on_diagonal_wire_is_attached() -> None:
    # KiCad 45-degree / free-angle wires are first-class; a label at the
    # midpoint of a diagonal wire must attach to it.
    data = sexpdata.loads(
        """
        (kicad_sch
          (wire (pts (xy 0 0) (xy 10 10)))
          (bus (pts (xy 0 20) (xy 10 30)))
        )
        """
    )

    wire_graph = build_wire_graph(data)
    bus_graph = build_bus_graph(data)

    assert wire_graph.touches_wire((5, 5))
    assert bus_graph.touches_bus((5, 25))
    # Off the line and past the endpoint must still miss.
    assert not wire_graph.touches_wire((5, 6))
    assert not wire_graph.touches_wire((11, 11))
