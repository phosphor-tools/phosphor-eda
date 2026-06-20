from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from allegro_oracle_helpers import (
    CP_SMARTGARDEN_NETLIST,
    OPENCELLULAR_BREAKOUT_IPC356,
    OPENCELLULAR_BREAKOUT_NETLIST,
    OPENCELLULAR_SYNC_IPC356,
    OPENCELLULAR_SYNC_NETLIST,
)

from phosphor_eda.formats.allegro.oracle import (
    ALLEGRO_ORACLE_CAPABILITIES,
    ALLEGRO_ORACLE_COVERAGE,
    OracleEvidenceKind,
    parse_ipc356,
    parse_packaged_netlist_summary,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_oracle_coverage_matrix_partitions_known_capabilities() -> None:
    for coverage in ALLEGRO_ORACLE_COVERAGE.values():
        assert coverage.validates
        assert coverage.validates.isdisjoint(coverage.cannot_prove)
        assert coverage.validates | coverage.cannot_prove == ALLEGRO_ORACLE_CAPABILITIES


@pytest.mark.parametrize(
    ("path", "expected_layers", "expected_records", "expected_nets", "expected_refs"),
    (
        (
            OPENCELLULAR_BREAKOUT_IPC356,
            ("TOP", "L2_GND", "L3_PLANE", "BOTTOM"),
            548,
            88,
            68,
        ),
        (
            OPENCELLULAR_SYNC_IPC356,
            ("TOP", "GND", "SIG1", "SIG2", "PWR", "BOTTOM"),
            1189,
            175,
            271,
        ),
    ),
    ids=("opencellular-breakout", "opencellular-sync"),
)
def test_ipc356_oracle_locks_connectivity_layers_and_pad_coordinates(
    path: Path,
    expected_layers: tuple[str, ...],
    expected_records: int,
    expected_nets: int,
    expected_refs: int,
) -> None:
    """Proves IPC-D-356 net/test records, layer stack names, and pad coordinates.

    Cannot prove editable native padstack ownership, routed copper geometry, pours,
    or Allegro Constraint Manager semantics.
    """
    report = parse_ipc356(path)

    assert report.units == "mils"
    assert tuple(layer.name for layer in report.layers) == expected_layers
    assert len(report.test_records) == expected_records
    assert len(report.net_names) == expected_nets
    assert len(report.component_refs) == expected_refs
    assert report.test_records[0].x_mils is not None
    assert report.test_records[0].y_mils is not None
    assert report.test_records[0].access_side.startswith("S")


def test_packaged_netlist_oracle_locks_component_pin_and_net_evidence() -> None:
    """Proves Cadence packaged netlist component, primitive, pin, and net evidence.

    Cannot prove board geometry, physical placement, drills, Gerbers, or constraints.
    """
    breakout = parse_packaged_netlist_summary(OPENCELLULAR_BREAKOUT_NETLIST)
    sync = parse_packaged_netlist_summary(OPENCELLULAR_SYNC_NETLIST)
    smartgarden = parse_packaged_netlist_summary(CP_SMARTGARDEN_NETLIST)

    assert ALLEGRO_ORACLE_COVERAGE[OracleEvidenceKind.PACKAGED_NETLIST].cannot_prove.issuperset(
        {"placement", "placement_count", "geometry"}
    )
    assert (breakout.net_count, breakout.node_count, breakout.part_count) == (76, 348, 68)
    assert (breakout.part_count, breakout.unique_refdes_count) == (68, 68)
    assert (sync.net_count, sync.node_count, sync.part_count, sync.unique_refdes_count) == (
        175,
        790,
        255,
        255,
    )
    assert (smartgarden.net_count, smartgarden.node_count, smartgarden.no_connect_node_count) == (
        117,
        624,
        94,
    )
    assert smartgarden.primitive_count == 68
    assert smartgarden.primitive_pin_count == 415
