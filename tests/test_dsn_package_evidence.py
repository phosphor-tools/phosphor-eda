"""Rejection-path tests for OrCAD native package evidence matching (H2/T2).

Directly exercises the ``dsn_package_evidence`` warn branches: ambiguous
package match, ambiguous device match, and out-of-range pin order (which also
covers a non-numeric designator, whose ``pin_order`` falls back to 0). Each
asserts the diagnostic content and that no evidence is attached.
"""

from __future__ import annotations

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.package_evidence import (
    build_package_lookup,
    native_package_device,
    native_package_for_instance,
    native_package_pin,
)
from phosphor_eda.formats.dsn.raw_models import (
    DsnPackage,
    DsnPackageDevice,
    DsnPackageDevicePin,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
)


def test_ambiguous_package_match_warns_and_attaches_no_evidence() -> None:
    raw = ParsedDesign(
        packages={
            "a": DsnPackage(stream_path="Packages/1", name="PARTX"),
            "b": DsnPackage(stream_path="Packages/2", name="PARTX"),
        }
    )
    lookup = build_package_lookup(raw)
    instance = PlacedInstance(package_name="PARTX", reference="U1")
    ctx = ParseContext()

    assert native_package_for_instance(instance, lookup, ctx) is None
    assert any(
        issue.category == "dsn_package_evidence"
        and "U1" in issue.message
        and "ambiguous" in issue.message
        for issue in ctx.issues
    )


def test_ambiguous_device_match_warns_and_attaches_no_evidence() -> None:
    package = DsnPackage(
        name="PARTX",
        devices=[DsnPackageDevice(refdes_suffix="A"), DsnPackageDevice(refdes_suffix="B")],
    )
    instance = PlacedInstance(package_name="PARTX", reference="U1")
    ctx = ParseContext()

    assert native_package_device(instance, package, ctx) is None
    assert any(
        issue.category == "dsn_package_evidence"
        and "U1" in issue.message
        and "2 devices and no unambiguous device match" in issue.message
        for issue in ctx.issues
    )


def test_out_of_range_pin_order_warns_and_attaches_no_evidence() -> None:
    device = DsnPackageDevice(refdes_suffix="A", pins=[DsnPackageDevicePin(package_pin="1")])
    instance = PlacedInstance(package_name="PARTX", reference="U1")
    pin = PinConnection(pin_number="5")
    ctx = ParseContext()

    assert native_package_pin(pin, device, instance, ctx) is None
    assert any(
        issue.category == "dsn_package_evidence"
        and "symbol pin order 5 is outside" in issue.message
        for issue in ctx.issues
    )


def test_non_numeric_pin_order_falls_back_to_zero_and_warns() -> None:
    # A non-numeric designator leaves pin_order at 0, which is out of range and
    # attaches no native package pin.
    device = DsnPackageDevice(refdes_suffix="A", pins=[DsnPackageDevicePin(package_pin="1")])
    instance = PlacedInstance(package_name="PARTX", reference="U1")
    pin = PinConnection(pin_number="NC")
    ctx = ParseContext()

    assert pin.pin_order == 0
    assert native_package_pin(pin, device, instance, ctx) is None
    assert any(
        issue.category == "dsn_package_evidence"
        and "symbol pin order 0 is outside" in issue.message
        for issue in ctx.issues
    )
