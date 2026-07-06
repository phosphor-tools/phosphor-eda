"""Shared OrCAD native package evidence matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.formats.dsn.pins import normalize_package_name

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.raw_models import (
        DsnPackage,
        DsnPackageDevice,
        DsnPackageDevicePin,
        ParsedDesign,
        PinConnection,
        PlacedInstance,
    )


@dataclass(slots=True)
class PackageLookup:
    exact: dict[str, list[DsnPackage]] = field(default_factory=dict)
    normalized: dict[str, list[DsnPackage]] = field(default_factory=dict)


def package_lookup_key(name: str) -> str:
    return normalize_package_name(name.strip()).casefold()


def build_package_lookup(raw: ParsedDesign) -> PackageLookup:
    lookup = PackageLookup()
    for package in raw.packages.values():
        stripped_name = package.name.strip()
        if stripped_name:
            lookup.exact.setdefault(stripped_name, []).append(package)
        for key in (package.name, normalize_package_name(package.name)):
            normalized = package_lookup_key(key)
            if normalized:
                lookup.normalized.setdefault(normalized, []).append(package)
    return lookup


def single_package_match(candidates: list[DsnPackage]) -> DsnPackage | None:
    unique: dict[str, DsnPackage] = {
        package.stream_path or package.name: package for package in candidates
    }
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def native_package_for_instance(
    instance: PlacedInstance,
    lookup: PackageLookup,
    ctx: ParseContext | None = None,
) -> DsnPackage | None:
    for key in (instance.package_name, instance.source_package):
        stripped = key.strip()
        if not stripped:
            continue
        if match := single_package_match(lookup.exact.get(stripped, [])):
            return match

    for key in (
        package_lookup_key(instance.package_name),
        package_lookup_key(instance.source_package),
    ):
        if not key:
            continue
        candidates = lookup.normalized.get(key, [])
        if match := single_package_match(candidates):
            return match
        if candidates and ctx is not None:
            names = ", ".join(sorted({package.name for package in candidates}))
            ctx.warn(
                "dsn_package_evidence",
                f"{instance.reference}: native package match for {key!r} is ambiguous: {names}",
            )
            return None
    return None


def native_package_device(
    instance: PlacedInstance,
    package: DsnPackage,
    ctx: ParseContext | None = None,
) -> DsnPackageDevice | None:
    if len(package.devices) == 1:
        return package.devices[0]

    keys = {
        instance.source_package.strip(),
        normalize_package_name(instance.package_name).strip(),
        package.name.strip(),
    }
    keys.discard("")
    matches = [
        device
        for device in package.devices
        if device.refdes_suffix.strip() in keys or device.unit_ref.strip() in keys
    ]
    if len(matches) == 1:
        return matches[0]
    if ctx is not None:
        ctx.warn(
            "dsn_package_evidence",
            f"{instance.reference}: native package {package.name!r} has "
            f"{len(package.devices)} devices and no unambiguous device match",
        )
    return None


def native_package_pin(
    pin: PinConnection,
    device: DsnPackageDevice,
    instance: PlacedInstance,
    ctx: ParseContext | None = None,
) -> DsnPackageDevicePin | None:
    # ``pin_order`` is the decoded 1-based display order (sign bit stripped),
    # so no-connect-marked pins map to their device pin instead of leaking the
    # raw u16 sentinel.
    order = pin.pin_order - 1
    if 0 <= order < len(device.pins):
        return device.pins[order]
    if ctx is not None:
        ctx.warn(
            "dsn_package_evidence",
            f"{instance.reference}: symbol pin order {pin.pin_order} is outside "
            f"native package device {device.refdes_suffix!r}",
        )
    return None
