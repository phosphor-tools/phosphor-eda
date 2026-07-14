"""Altium net-naming conformance tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from fixture_paths import UPSTREAM_FIXTURES

from phosphor_eda.query.project_loader import load_project

GOLDENS = Path(__file__).resolve().parent / "goldens"

PI_MX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release/PiMX8MP_r0.3_release.PrjPcb"
)
PI_MX8_PCB_NET_ORACLE = GOLDENS / "altium/pi-mx8-pcb-netlist.json"


@dataclass(slots=True)
class AltiumPcbOracleComparison:
    """Net-name comparison against a PCB-side netlist oracle."""

    matched: list[tuple[str, str]] = field(default_factory=list)
    mismatched: list[tuple[str, str]] = field(default_factory=list)
    unmatched: int = 0
    ambiguous: int = 0


def test_pi_mx8_schematic_net_names_match_pcb_net_oracle() -> None:
    comparison = _compare_project_to_pcb_oracle(PI_MX8_PRJPCB, PI_MX8_PCB_NET_ORACLE)

    assert comparison.unmatched == 0
    assert comparison.ambiguous == 0
    assert comparison.mismatched == []
    assert len(comparison.matched) == 477


def _compare_project_to_pcb_oracle(
    project_path: Path,
    oracle_path: Path,
) -> AltiumPcbOracleComparison:
    project = load_project(project_path)
    if project.schematic is None:
        raise AssertionError(f"{project_path} did not load a schematic")

    ours_by_signature: dict[frozenset[tuple[str, str]], list[str]] = {}
    for net in project.schematic.nets:
        signature = frozenset(
            (pin.component.reference, pin.designator)
            for pin in net.pins
            if pin.component.reference and pin.designator
        )
        if signature:
            ours_by_signature.setdefault(signature, []).append(net.name)

    comparison = AltiumPcbOracleComparison()
    for oracle_name, signature in _load_pcb_net_oracle(oracle_path):
        our_names = ours_by_signature.get(signature)
        if our_names is None:
            comparison.unmatched += 1
            continue
        if len(our_names) != 1:
            comparison.ambiguous += 1
            continue
        our_name = our_names[0]
        pair = (oracle_name, our_name)
        if oracle_name.upper() == our_name.upper():
            comparison.matched.append(pair)
        else:
            comparison.mismatched.append(pair)
    return comparison


def _load_pcb_net_oracle(path: Path) -> list[tuple[str, frozenset[tuple[str, str]]]]:
    raw_data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    data = _json_object(raw_data, path)
    net_count = data.get("net_count")
    nets = _json_list(data.get("nets"), path)
    if not isinstance(net_count, int) or net_count != len(nets):
        raise AssertionError(
            f"Invalid Altium oracle net_count in {path}: {net_count!r} != {len(nets)}"
        )

    result: list[tuple[str, frozenset[tuple[str, str]]]] = []
    for net in nets:
        name = net.get("name")
        members = net.get("members")
        if not isinstance(name, str) or not isinstance(members, list):
            raise AssertionError(f"Invalid Altium oracle net in {path}: {net!r}")
        member_values = cast("list[object]", members)
        result.append((name, frozenset(_json_member(member, path) for member in member_values)))
    return result


def _json_object(value: object, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"Invalid Altium oracle root in {path}: {value!r}")
    result: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            raise AssertionError(f"Invalid Altium oracle root in {path}: {value!r}")
        result[key] = item
    return result


def _json_list(value: object, path: Path) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AssertionError(f"Invalid Altium oracle nets in {path}: {value!r}")
    return [_json_object(item, path) for item in cast("list[object]", value)]


def _json_member(value: object, path: Path) -> tuple[str, str]:
    if not isinstance(value, list):
        raise AssertionError(f"Invalid Altium oracle member in {path}: {value!r}")
    member = cast("list[object]", value)
    if len(member) != 2 or not isinstance(member[0], str) or not isinstance(member[1], str):
        raise AssertionError(f"Invalid Altium oracle member in {path}: {value!r}")
    return (member[0], member[1])
