"""Cadence packaged netlist sidecar parsing for OrCAD projects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.text import strip_overline
from phosphor_eda.formats.dsn.package_evidence import (
    build_package_lookup,
    native_package_device,
    native_package_for_instance,
    native_package_pin,
)
from phosphor_eda.formats.dsn.pins import resolve_pin_name
from phosphor_eda.formats.dsn.raw_models import DsnNoConnectPin, PinConnection
from phosphor_eda.formats.dsn.source import (
    dsn_component_source_id,
    dsn_page_id,
    dsn_pin_public_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.package_evidence import PackageLookup
    from phosphor_eda.formats.dsn.raw_models import ParsedDesign, PlacedInstance

_PART_RE = re.compile(r"^\s+(\S+)\s+'([^']+)':;")
_PRIMITIVE_RE = re.compile(r"^primitive '([^']+)';")
_PIN_RE = re.compile(r"^\s+'([^']+)':")
_PIN_NUMBER_RE = re.compile(r"^\s+PIN_NUMBER='\(([^)]*)\)';")
_PSTXNET_NODE_RE = re.compile(r"^NODE_NAME\s+(\S+)\s+(\S+)")
_PSTXNET_QUOTED_RE = re.compile(r"^\s*'([^']*)'")


@dataclass(frozen=True, slots=True)
class PstChipPinEvidence:
    order: int
    pin_name: str
    package_pin: str


def parse_pstxnet_no_connects(path: Path) -> list[DsnNoConnectPin]:
    """Parse Cadence's generated NC pseudo-net members from ``pstxnet.dat``."""
    no_connects: list[DsnNoConnectPin] = []
    lines = path.read_text(errors="replace").splitlines()
    i = 0
    current_net = ""
    while i < len(lines):
        line = lines[i]
        if line.startswith("NET_NAME"):
            quoted = _PSTXNET_QUOTED_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
            current_net = quoted.group(1) if quoted is not None else ""
            i += 2
            continue
        node = _PSTXNET_NODE_RE.match(line)
        if node is not None and current_net == "NC":
            pin_name = ""
            found_pin_line = False
            j = i + 1
            while j < len(lines) and j < i + 5:
                stripped = lines[j].rstrip()
                if stripped.endswith("':;") or stripped.endswith("': ;"):
                    found_pin_line = True
                    quoted = _PSTXNET_QUOTED_RE.match(lines[j])
                    if quoted is not None:
                        pin_name = strip_overline(quoted.group(1))[0]
                    break
                j += 1
            no_connects.append(
                DsnNoConnectPin(
                    source_path=str(path),
                    refdes=node.group(1),
                    pin_token=node.group(2),
                    pin_name=pin_name,
                    raw_net_name=current_net,
                )
            )
            i = j + 1 if found_pin_line else i + 1
            continue
        i += 1
    return no_connects


def apply_packaged_pin_names(
    raw: ParsedDesign,
    netlist_dir: Path,
    ctx: ParseContext | None = None,
) -> None:
    """Apply pstxprt/pstchip primitive pin-number maps to placed instances."""
    pstxprt = netlist_dir / "pstxprt.dat"
    pstchip = netlist_dir / "pstchip.dat"
    if not pstxprt.exists() or not pstchip.exists():
        return

    primitive_by_ref = _parse_pstxprt_primitives(pstxprt)
    pin_evidence_by_primitive = parse_pstchip_pin_evidence(pstchip)
    pins_by_primitive = {
        primitive: {order: evidence.pin_name for order, evidence in pins.items()}
        for primitive, pins in pin_evidence_by_primitive.items()
    }
    pin_numbers_by_primitive = parse_pstchip_pin_number_maps(pstchip)
    if not primitive_by_ref or (not pin_evidence_by_primitive and not pin_numbers_by_primitive):
        return
    packages_by_key = build_package_lookup(raw)

    for page in raw.pages:
        for instance in page.instances:
            primitive = primitive_by_ref.get(instance.reference)
            if primitive is None:
                continue
            pin_names = pins_by_primitive.get(primitive)
            if pin_names:
                instance.pin_name_overrides = pin_names
            _compare_native_package_evidence(
                instance,
                pin_evidence_by_primitive.get(primitive, {}),
                packages_by_key,
                ctx,
            )
            pin_numbers = pin_numbers_by_primitive.get(primitive, {})
            for pin in instance.pin_connections:
                package_pin_number = pin_numbers.get(pin.pin_number)
                if package_pin_number is not None:
                    pin.package_pin_number = package_pin_number


def apply_packaged_no_connects(
    raw: ParsedDesign,
    netlist_dir: Path,
    ctx: ParseContext | None = None,
) -> None:
    """Apply pstxnet NC pseudo-net members to matched raw pin connections."""
    pstxnet = netlist_dir / "pstxnet.dat"
    if not pstxnet.exists():
        return
    source_path = str(pstxnet)
    if any(no_connect.source_path == source_path for no_connect in raw.no_connect_pins):
        return

    no_connects = parse_pstxnet_no_connects(pstxnet)
    if not no_connects:
        return
    raw.no_connect_pins.extend(no_connects)

    candidates_by_ref = _no_connect_candidates_by_ref(raw)
    for no_connect in no_connects:
        candidates = candidates_by_ref.get(no_connect.refdes, [])
        match = _resolve_no_connect_candidate(no_connect, candidates, ctx)
        if match is None:
            continue
        pin, _pin_name, public_pin_id = match
        no_connect.matched_pin_id = public_pin_id
        pin.no_connect = True
        pin.no_connect_metadata = {
            "dsn_no_connect_source": "pstxnet.dat",
            "dsn_no_connect_source_path": no_connect.source_path,
            "dsn_no_connect_refdes": no_connect.refdes,
            "dsn_no_connect_pin_token": no_connect.pin_token,
            "dsn_no_connect_pin_name": no_connect.pin_name,
            "dsn_no_connect_raw_net_name": no_connect.raw_net_name,
        }


def _parse_pstxprt_primitives(path: Path) -> dict[str, str]:
    primitives: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        match = _PART_RE.match(line)
        if match is None:
            continue
        primitives[match.group(1)] = match.group(2)
    return primitives


def parse_pstchip_pin_evidence(path: Path) -> dict[str, dict[str, PstChipPinEvidence]]:
    return {
        primitive: {
            str(index): PstChipPinEvidence(
                order=index,
                pin_name=name,
                package_pin=_scalar_pin_number(value),
            )
            for index, name, value in scalar_entries
        }
        for primitive, entries in _parse_pstchip_pin_entries(path).items()
        if (scalar_entries := _scalar_pin_entries(entries))
    }


def parse_pstchip_pin_number_maps(path: Path) -> dict[str, dict[str, str]]:
    return {
        primitive: {str(index): _scalar_pin_number(value) for index, _name, value in scalar_entries}
        for primitive, entries in _parse_pstchip_pin_entries(path).items()
        if (scalar_entries := _scalar_pin_entries(entries))
    }


def _parse_pstchip_pin_entries(path: Path) -> dict[str, list[tuple[int, str, str]]]:
    primitives: dict[str, list[tuple[int, str, str]]] = {}
    current_primitive = ""
    current_pin_name = ""
    current_pin_index = 0
    current_pins: list[tuple[int, str, str]] = []
    in_pin_block = False

    for line in path.read_text(errors="replace").splitlines():
        primitive_match = _PRIMITIVE_RE.match(line)
        if primitive_match is not None:
            current_primitive = primitive_match.group(1)
            current_pin_name = ""
            current_pin_index = 0
            current_pins = []
            in_pin_block = False
            if current_primitive not in primitives:
                primitives[current_primitive] = []
            continue
        if not current_primitive:
            continue
        stripped = line.strip()
        if stripped == "pin":
            in_pin_block = True
            continue
        if stripped == "end_pin;":
            # A primitive can hold several pin blocks; accumulate them under one
            # continuous index rather than letting each block overwrite the last.
            primitives[current_primitive] = list(current_pins)
            in_pin_block = False
            current_pin_name = ""
            continue
        if not in_pin_block:
            continue
        pin_match = _PIN_RE.match(line)
        if pin_match is not None:
            current_pin_index += 1
            current_pin_name = pin_match.group(1)
            continue
        number_match = _PIN_NUMBER_RE.match(line)
        if number_match is not None:
            if current_pin_name:
                current_pins.append((current_pin_index, current_pin_name, number_match.group(1)))
            continue

    return primitives


def _scalar_pin_entries(entries: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    if all(_is_scalar_pin_number(value) for _, _, value in entries):
        return entries
    return [(index, name, value) for index, name, value in entries if _is_scalar_pin_number(value)]


def _scalar_pin_number(value: str) -> str:
    return next((part.strip() for part in value.split(",") if part.strip()), "")


def _compare_native_package_evidence(
    instance: PlacedInstance,
    sidecar_pins: dict[str, PstChipPinEvidence],
    packages_by_key: PackageLookup,
    ctx: ParseContext | None,
) -> None:
    if ctx is None or not sidecar_pins:
        return
    package = native_package_for_instance(instance, packages_by_key, ctx)
    if package is None:
        return
    device = native_package_device(instance, package, ctx)
    if device is None:
        return
    for pin in instance.pin_connections:
        sidecar = sidecar_pins.get(pin.pin_number)
        native_pin = native_package_pin(pin, device, instance, ctx)
        if sidecar is None or native_pin is None:
            continue
        if (
            native_pin.package_pin
            and sidecar.package_pin
            and native_pin.package_pin != sidecar.package_pin
        ):
            ctx.warn(
                "dsn_package_evidence",
                f"{instance.reference} pin order {pin.pin_number}: native package pin "
                f"{native_pin.package_pin!r} differs from pstchip.dat pin "
                f"{sidecar.package_pin!r}",
            )


def _is_scalar_pin_number(value: str) -> bool:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return len(parts) == 1 and parts[0] != "0"


type _NoConnectCandidate = tuple[PinConnection, str, str]


def _no_connect_candidates_by_ref(raw: ParsedDesign) -> dict[str, list[_NoConnectCandidate]]:
    candidates: dict[str, list[_NoConnectCandidate]] = {}
    for page in raw.pages:
        page_id = dsn_page_id(page.name)
        for instance_index, instance in enumerate(page.instances):
            component_source_id = dsn_component_source_id(
                page_id,
                instance.db_id,
                instance_index,
            )
            for pin in instance.pin_connections:
                pin_name = resolve_pin_name(
                    instance.package_name,
                    pin.pin_number,
                    raw.symbol_pin_names,
                    None,
                    instance.reference,
                    instance.pin_name_overrides,
                )
                public_pin_id = dsn_pin_public_id(component_source_id, pin.pin_number)
                candidates.setdefault(instance.reference, []).append((pin, pin_name, public_pin_id))
    return candidates


def _resolve_no_connect_candidate(
    no_connect: DsnNoConnectPin,
    candidates: list[_NoConnectCandidate],
    ctx: ParseContext | None,
) -> _NoConnectCandidate | None:
    package_pin_matches = [
        candidate
        for candidate in candidates
        if candidate[0].package_pin_number == no_connect.pin_token
    ]
    if len(package_pin_matches) == 1:
        return package_pin_matches[0]
    if len(package_pin_matches) > 1:
        _warn_no_connect_ambiguous(no_connect, "physical pin number", ctx)
        return None

    if no_connect.pin_name:
        pin_name_key = no_connect.pin_name.casefold()
        name_matches = [
            candidate for candidate in candidates if candidate[1].casefold() == pin_name_key
        ]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            _warn_no_connect_ambiguous(no_connect, "pin name", ctx)
            return None
    if ctx is not None:
        ctx.warn(
            "dsn_sidecar_no_connect_unresolved",
            (
                f"pstxnet NC member {no_connect.refdes} pin {no_connect.pin_token} "
                f"({no_connect.pin_name!r}) did not match a parsed DSN pin"
            ),
        )
    return None


def _warn_no_connect_ambiguous(
    no_connect: DsnNoConnectPin,
    match_kind: str,
    ctx: ParseContext | None,
) -> None:
    if ctx is None:
        return
    ctx.warn(
        "dsn_sidecar_no_connect_ambiguous",
        (
            f"pstxnet NC member {no_connect.refdes} pin {no_connect.pin_token} "
            f"({no_connect.pin_name!r}) matched multiple DSN pins by {match_kind}"
        ),
    )
