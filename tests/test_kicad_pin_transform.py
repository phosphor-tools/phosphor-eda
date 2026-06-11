"""Pin placement for mirrored/rotated KiCad symbols.

Ground truth was established empirically with `kicad-cli sch export netlist`
(Eeschema 10.0.0): probe schematics placed an asymmetric symbol under every
mirror/rotation combination with labels at all candidate pin positions, and
the netlist revealed which position each pin actually occupies. KiCad's
semantics: y-flip lib coords to screen coords, rotate (positive angle is
screen-CCW), then apply the mirror in screen coordinates.

The fixture `kicad-mirrored-pins/mirrored.kicad_sch` is hand-written; its
label positions were validated against the same kicad-cli oracle.
"""

from pathlib import Path

import pytest

from phosphor_eda.formats.kicad.lib_symbols import transform_pin
from phosphor_eda.formats.kicad.resolver import resolve_kicad_source
from phosphor_eda.formats.kicad.to_schematic import kicad_to_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MIRRORED = FIXTURES / "kicad-mirrored-pins" / "mirrored.kicad_sch"

# (lib_x, lib_y, rot, mirror) -> screen offset from anchor, per kicad-cli.
# Pin 1 of the probe symbol sits at lib (2.54, 5.08).
ORACLE_CASES = [
    (0, None, (2.54, -5.08)),
    (0, "x", (2.54, 5.08)),
    (0, "y", (-2.54, -5.08)),
    (90, None, (-5.08, -2.54)),
    (90, "x", (-5.08, 2.54)),
    (90, "y", (5.08, -2.54)),
    (180, None, (-2.54, 5.08)),
    (270, "x", (5.08, -2.54)),
    (270, "y", (-5.08, 2.54)),
]


@pytest.mark.parametrize(("rot", "mirror", "expected"), ORACLE_CASES)
def test_transform_pin_matches_kicad_oracle(
    rot: float, mirror: str | None, expected: tuple[float, float]
) -> None:
    assert transform_pin(2.54, 5.08, 0.0, 0.0, rot, mirror) == expected


def test_mirrored_symbols_connect_correct_pins() -> None:
    design = resolve_kicad_source(kicad_to_source(MIRRORED))

    expected = {
        "N_V0_P1": ("U1", "1"),
        "N_V0_P2": ("U1", "2"),
        "N_VX_P1": ("U2", "1"),
        "N_VX_P2": ("U2", "2"),
        "N_VY_P1": ("U3", "1"),
        "N_VY_P2": ("U3", "2"),
        "N_VXR90_P1": ("U4", "1"),
        "N_VXR90_P2": ("U4", "2"),
        "N_VR90_P1": ("U5", "1"),
        "N_VR90_P2": ("U5", "2"),
        "N_VR270_P1": ("U6", "1"),
        "N_VR270_P2": ("U6", "2"),
    }
    nets_by_name = {net.name: net for net in design.nets}
    for net_name, (ref, designator) in expected.items():
        net = nets_by_name.get(net_name)
        assert net is not None, f"net {net_name} missing (got {sorted(nets_by_name)})"
        members = {(pin.component.reference, pin.designator) for pin in net.pins}
        assert members == {(ref, designator)}, f"{net_name}: {members}"
