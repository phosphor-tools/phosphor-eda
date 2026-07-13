"""pstxnet.dat net-naming oracle helpers for OrCAD DSN tests.

Cadence's packaged netlist (pstxnet.dat) carries the tool's own resolved
net names; comparing against it checks that the parser reads Capture's
stored names (including N##### autonames) instead of synthesizing.
Adapted from a local corpus oracle and trimmed to the name comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.text import strip_overline
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.schematic import Schematic

_NODE_RE = re.compile(r"^NODE_NAME\s+(\S+)\s+(\S+)")
_QUOTED_RE = re.compile(r"^\s*'([^']*)'")
_AUTONAME_RE = re.compile(r"N\d{5,}")


def parse_pstxnet(path: Path) -> dict[str, set[tuple[str, str]]]:
    """net name -> {(refdes, upper-cased pin name)} from a pstxnet.dat.

    The packager-generated ``NC`` pseudo-net (pins carrying the NC
    property) has no DSN-side net and is dropped.
    """
    nets: dict[str, set[tuple[str, str]]] = {}
    lines = path.read_text(errors="replace").splitlines()
    i = 0
    current: set[tuple[str, str]] | None = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("NET_NAME"):
            quoted = _QUOTED_RE.match(lines[i + 1])
            name = quoted.group(1) if quoted else ""
            current = nets.setdefault(name, set())
            i += 2
            continue
        node = _NODE_RE.match(line)
        if node and current is not None:
            refdes = node.group(1)
            # The pin name is quoted on the line ending "':;" shortly
            # after the instance-path line(s).
            pin_name = ""
            j = i + 1
            while j < len(lines) and j < i + 5:
                stripped = lines[j].rstrip()
                if stripped.endswith("':;") or stripped.endswith("': ;"):
                    quoted = _QUOTED_RE.match(lines[j])
                    if quoted:
                        pin_name = strip_overline(quoted.group(1))[0]
                    break
                j += 1
            current.add((refdes, pin_name.upper()))
            i = j + 1
            continue
        i += 1
    nets.pop("NC", None)
    return nets


@dataclass(slots=True)
class OracleNameComparison:
    """Net-name diff between a parsed DSN and its pstxnet oracle.

    Nets are paired by exact pin-membership signature (refdes + pin
    name); signatures that are empty, absent on the other side, or
    ambiguous (shared by several nets) are not compared.
    """

    matched: list[tuple[str, str]] = field(default_factory=list)
    mismatched: list[tuple[str, str]] = field(default_factory=list)
    unmatched: int = 0
    ambiguous: int = 0

    @property
    def matched_autonames(self) -> list[tuple[str, str]]:
        return [
            (oracle, ours)
            for oracle, ours in self.matched
            if _AUTONAME_RE.fullmatch(oracle) is not None
        ]


def compare_net_names(dsn_path: Path, pstxnet_path: Path) -> OracleNameComparison:
    """Compare resolved DSN net names against the pstxnet oracle.

    Names are compared case-insensitively (pstxnet preserves design
    casing, but Allegro treats names case-insensitively); the per-pair
    spellings are kept so callers can assert byte-exact autonames.
    """
    ctx = ParseContext()
    raw = parse_dsn(dsn_path, ctx)
    design = dsn_to_design(raw, name=dsn_path.stem, ctx=ctx)
    return compare_schematic_net_names(design, pstxnet_path)


def compare_schematic_net_names(design: Schematic, pstxnet_path: Path) -> OracleNameComparison:
    """Compare resolved schematic net names against the pstxnet oracle."""
    oracle = parse_pstxnet(pstxnet_path)

    ours_by_sig: dict[frozenset[tuple[str, str]], list[str]] = {}
    for net in design.nets:
        signature = frozenset((pin.component.reference, pin.name.upper()) for pin in net.pins)
        ours_by_sig.setdefault(signature, []).append(net.name)
    oracle_by_sig: dict[frozenset[tuple[str, str]], list[str]] = {}
    for name, members in oracle.items():
        oracle_by_sig.setdefault(frozenset(members), []).append(name)

    result = OracleNameComparison()
    for signature, oracle_names in oracle_by_sig.items():
        if not signature:
            continue
        our_names = ours_by_sig.get(signature)
        if not our_names:
            result.unmatched += 1
            continue
        if len(oracle_names) > 1 or len(our_names) > 1:
            result.ambiguous += 1
            continue
        pair = (oracle_names[0], our_names[0])
        if oracle_names[0].upper() == our_names[0].upper():
            result.matched.append(pair)
        else:
            result.mismatched.append(pair)
    return result
