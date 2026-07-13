"""KiCad net-naming conformance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from phosphor_eda.domain.schematic import Net, NetNameKind
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
GOLDENS = Path(__file__).resolve().parent / "goldens"
CORPUS_ROOT = Path(os.environ.get("PHOSPHOR_EDA_CORPUS_ROOT", "__external_corpus_missing__"))
KICAD_CORPUS = CORPUS_ROOT / "designs/kicad"

COMMITTED_SCHEMATICS = [
    UPSTREAM_FIXTURES / "rp2040-minimal/RP2040_minimal_r2/RP2040_minimal_r2.kicad_sch",
    FIXTURES / "kicad-hierarchy/root.kicad_sch",
    FIXTURES / "kicad-orangecrab/OrangeCrab.kicad_sch",
    UPSTREAM_FIXTURES / "jetson-orin/jetson-orin-baseboard.kicad_sch",
]

CORPUS_SCHEMATICS = [
    KICAD_CORPUS / "ZSWatch-HW/devkit/ZSWatch-Watch-DevKit.kicad_sch",
]

KICAD_CLI_ORACLES = [
    (
        FIXTURES / "kicad-orangecrab/OrangeCrab.kicad_sch",
        GOLDENS / "kicad/orangecrab.kicad-cli-netlist.json",
    ),
    (
        UPSTREAM_FIXTURES / "jetson-orin/jetson-orin-baseboard.kicad_sch",
        GOLDENS / "kicad/jetson-orin-baseboard.kicad-cli-netlist.json",
    ),
]


@pytest.mark.parametrize(
    "schematic_path",
    COMMITTED_SCHEMATICS,
    ids=[path.parent.name for path in COMMITTED_SCHEMATICS],
)
def test_committed_kicad_fixtures_have_driver_backed_names(schematic_path: Path) -> None:
    assert _synthesized_net_names(schematic_path) == []


@pytest.mark.corpus
@pytest.mark.skipif(not KICAD_CORPUS.exists(), reason="external KiCad corpus not present")
@pytest.mark.parametrize(
    "schematic_path",
    CORPUS_SCHEMATICS,
    ids=[path.parent.name for path in CORPUS_SCHEMATICS],
)
def test_kicad_corpus_schematics_have_driver_backed_names(schematic_path: Path) -> None:
    if not schematic_path.exists():
        pytest.skip(f"{schematic_path.name} not present in local KiCad corpus")

    assert _synthesized_net_names(schematic_path) == []


@pytest.mark.parametrize(
    ("schematic_path", "oracle_path"),
    KICAD_CLI_ORACLES,
    ids=[path.parent.name for path, _oracle in KICAD_CLI_ORACLES],
)
def test_committed_kicad_fixtures_match_kicad_cli_oracle(
    schematic_path: Path,
    oracle_path: Path,
) -> None:
    assert _net_members_by_name(kicad_to_design(schematic_path).nets) == _load_kicad_cli_oracle(
        oracle_path
    )


def _synthesized_net_names(schematic_path: Path) -> list[str]:
    design = kicad_to_design(schematic_path)
    return [
        net.name
        for net in design.nets
        if any(name.kind is NetNameKind.SYNTHESIZED for name in net.names)
    ]


def _net_members_by_name(nets: list[Net]) -> dict[str, frozenset[tuple[str, str]]]:
    return {
        net.name: frozenset((pin.component.reference, pin.designator) for pin in net.pins)
        for net in nets
    }


def _load_kicad_cli_oracle(path: Path) -> dict[str, frozenset[tuple[str, str]]]:
    raw_data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    data = _json_object(raw_data, path)
    net_count = data.get("net_count")
    nets = _json_list(data.get("nets"), path)
    if not isinstance(net_count, int) or net_count != len(nets):
        raise AssertionError(
            f"Invalid KiCad oracle net_count in {path}: {net_count!r} != {len(nets)}"
        )

    result: dict[str, frozenset[tuple[str, str]]] = {}
    for net in nets:
        name = net.get("name")
        members = net.get("members")
        if not isinstance(name, str) or not isinstance(members, list):
            raise AssertionError(f"Invalid KiCad oracle net in {path}: {net!r}")
        if name in result:
            raise AssertionError(f"Duplicate KiCad oracle net name in {path}: {name!r}")
        member_values = cast("list[object]", members)
        result[name] = frozenset(_json_member(member, path) for member in member_values)
    return result


def _json_object(value: object, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"Invalid KiCad oracle root in {path}: {value!r}")
    result: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            raise AssertionError(f"Invalid KiCad oracle root in {path}: {value!r}")
        result[key] = item
    return result


def _json_list(value: object, path: Path) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AssertionError(f"Invalid KiCad oracle nets in {path}: {value!r}")
    return [_json_object(item, path) for item in cast("list[object]", value)]


def _json_member(value: object, path: Path) -> tuple[str, str]:
    if not isinstance(value, list):
        raise AssertionError(f"Invalid KiCad oracle member in {path}: {value!r}")
    member = cast("list[object]", value)
    if len(member) != 2 or not isinstance(member[0], str) or not isinstance(member[1], str):
        raise AssertionError(f"Invalid KiCad oracle member in {path}: {value!r}")
    return (member[0], member[1])
