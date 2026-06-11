"""Tests for the schematic text serializer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from phosphor_eda.domain.schematic import (
    Component as DomainComponent,
)
from phosphor_eda.domain.schematic import (
    ComponentOccurrence,
    NetOccurrence,
    PinOccurrence,
    Schematic,
    ScopeId,
)
from phosphor_eda.domain.schematic import (
    Net as DomainNet,
)
from phosphor_eda.domain.schematic import (
    Page as DomainPage,
)
from phosphor_eda.domain.schematic import (
    Pin as DomainPin,
)
from phosphor_eda.query.serialize import (
    filter_components,
    filter_nets,
    filter_pages,
    format_component_detail,
    format_component_table,
    format_net_detail,
    format_net_table,
    format_page_detail,
    format_page_table,
    format_trace,
    serialize_design,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class Page(DomainPage):
    def __init__(
        self,
        *,
        name: str,
        id: str = "",
        source_file: str = "",
        scope_id: ScopeId | None = None,
        components: list[DomainComponent] | None = None,
        nets: list[DomainNet] | None = None,
        annotations: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"page:{name}",
            name=name,
            source_file=source_file,
            scope_id=scope_id or ScopeId(path=()),
            components=components or [],
            nets=nets or [],
            annotations=annotations or [],
            metadata=metadata or {},
        )


class Component(DomainComponent):
    def __init__(
        self,
        *,
        reference: str,
        part: str,
        description: str,
        id: str = "",
        pins: list[DomainPin] | None = None,
        pages: list[DomainPage] | None = None,
        occurrences: list[ComponentOccurrence] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"component:{reference}",
            reference=reference,
            part=part,
            description=description,
            pins=pins or [],
            pages=pages or [],
            occurrences=occurrences or [],
            metadata=metadata or {},
        )


class Net(DomainNet):
    def __init__(
        self,
        *,
        name: str,
        id: str = "",
        pins: list[DomainPin] | None = None,
        pages: list[DomainPage] | None = None,
        occurrences: list[NetOccurrence] | None = None,
        aliases: set[str] | None = None,
        bus: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"net:{name}",
            name=name,
            pins=pins or [],
            pages=pages or [],
            occurrences=occurrences or [],
            aliases=aliases or set(),
            bus=bus,
            metadata=metadata or {},
        )


class Pin(DomainPin):
    def __init__(
        self,
        *,
        designator: str,
        name: str,
        component: DomainComponent,
        id: str = "",
        net: DomainNet | None = None,
        no_connect: bool = False,
        occurrences: list[PinOccurrence] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"pin:{component.id}:{designator}",
            designator=designator,
            name=name,
            component=component,
            net=net,
            no_connect=no_connect,
            occurrences=occurrences or [],
            metadata=metadata or {},
        )


def _simple_design():
    """Build a minimal design for testing serialization."""
    page = Page(name="ADC")
    comp_u7 = Component(
        reference="U7",
        part="AD7768-1",
        description="IC - ADC - Single",
        pages=[page],
        metadata={"mfr": "Analog Devices", "mfr_pn": "AD7768-1BCPZ"},
    )
    comp_r1 = Component(
        reference="R1",
        part="10k",
        description="Resistor",
        pages=[page],
        metadata={"value": "10k"},
    )
    net_sclk = Net(name="ADC_SCLK")
    net_gnd = Net(name="GND")

    pin_u7_10 = Pin(designator="10", name="SCLK", component=comp_u7, net=net_sclk, metadata={})
    pin_u7_7 = Pin(designator="7", name="DGND", component=comp_u7, net=net_gnd, metadata={})
    pin_u7_nc = Pin(designator="26", name="AIN-", component=comp_u7, no_connect=True, metadata={})
    pin_u7_uc = Pin(designator="28", name="VCM", component=comp_u7, metadata={})
    comp_u7.pins = [pin_u7_10, pin_u7_7, pin_u7_nc, pin_u7_uc]

    pin_r1_1 = Pin(designator="1", name="", component=comp_r1, net=net_sclk, metadata={})
    pin_r1_2 = Pin(designator="2", name="", component=comp_r1, net=net_gnd, metadata={})
    comp_r1.pins = [pin_r1_1, pin_r1_2]

    net_sclk.pins = [pin_u7_10, pin_r1_1]
    net_gnd.pins = [pin_u7_7, pin_r1_2]
    net_sclk.pages = [page]
    net_gnd.pages = [page]

    page.components = [comp_u7, comp_r1]
    page.nets = [net_sclk, net_gnd]

    return Schematic(
        name="TEST",
        pages=[page],
        nets=[net_gnd, net_sclk],
        components=[comp_r1, comp_u7],
        metadata={"Revision": "1.0"},
    )


def _multi_page_component_design() -> Schematic:
    page_a = Page(name="A", scope_id=ScopeId(path=("root", "a")))
    page_b = Page(name="B", scope_id=ScopeId(path=("root", "b")))
    comp = Component(reference="U7", part="AD7768-1", description="ADC", pages=[page_a, page_b])
    net = Net(name="DRDY", pages=[page_a, page_b])
    pin = Pin(designator="10", name="DRDY", component=comp, net=net)
    comp.pins = [pin]
    net.pins = [pin]
    comp.occurrences = [
        ComponentOccurrence(
            id="occ:u7:a",
            component=comp,
            page=page_a,
            scope_id=page_a.scope_id,
            source_id="source-u7-a",
            part_id="A",
        ),
        ComponentOccurrence(
            id="occ:u7:b",
            component=comp,
            page=page_b,
            scope_id=page_b.scope_id,
            source_id="source-u7-b",
            part_id="B",
        ),
    ]
    net.occurrences = [
        NetOccurrence(
            id="net-occ:drdy:a",
            net=net,
            page=page_a,
            scope_id=page_a.scope_id,
            source_local_net_id="local-a",
            source_names={"DRDY"},
        ),
        NetOccurrence(
            id="net-occ:drdy:b",
            net=net,
            page=page_b,
            scope_id=page_b.scope_id,
            source_local_net_id="local-b",
            source_names={"DRDY"},
        ),
    ]
    page_a.components = [comp]
    page_b.components = [comp]
    page_a.nets = [net]
    page_b.nets = [net]
    return Schematic(name="MULTI", pages=[page_a, page_b], nets=[net], components=[comp])


def _duplicate_reference_design() -> Schematic:
    page_a = Page(name="MCU_A", id="page:mcu-a", scope_id=ScopeId(path=("root", "mcu_a")))
    page_b = Page(name="MCU_B", id="page:mcu-b", scope_id=ScopeId(path=("root", "mcu_b")))
    comp_a = Component(
        id="component:mcu-a:u7",
        reference="U7",
        part="MCU",
        description="Processor",
        pages=[page_a],
    )
    comp_b = Component(
        id="component:mcu-b:u7",
        reference="U7",
        part="MCU",
        description="Processor",
        pages=[page_b],
    )
    bus = Net(
        id="net:shared-bus",
        name="SYNC",
        pages=[page_a, page_b],
        metadata={
            "selected_name_source": "global_label",
            "selected_name_source_id": "net-occ:shared-bus",
            "source_format": "constructed",
            "source_local_net_ids": "N$2,N$3",
            "source_scope_ids": "/root/mcu_a,/root/mcu_b",
        },
    )
    reset_a = Net(
        id="net:mcu-a:reset",
        name="RESET",
        pages=[page_a],
        metadata={
            "selected_name_source": "local_label",
            "selected_name_source_id": "net-occ:mcu-a:reset",
            "source_format": "constructed",
            "source_local_net_ids": "N$1",
            "source_scope_ids": "/root/mcu_a",
        },
    )
    reset_b = Net(id="net:mcu-b:reset", name="RESET", pages=[page_b])
    pin_a_10 = Pin(id="pin:mcu-a:u7:10", designator="10", name="SYNC", component=comp_a, net=bus)
    pin_b_10 = Pin(id="pin:mcu-b:u7:10", designator="10", name="SYNC", component=comp_b, net=bus)
    pin_a_1 = Pin(id="pin:mcu-a:u7:1", designator="1", name="RST", component=comp_a, net=reset_a)
    pin_b_1 = Pin(id="pin:mcu-b:u7:1", designator="1", name="RST", component=comp_b, net=reset_b)
    comp_a.pins = [pin_a_10, pin_a_1]
    comp_b.pins = [pin_b_10, pin_b_1]
    bus.pins = [pin_a_10, pin_b_10]
    reset_a.pins = [pin_a_1]
    reset_b.pins = [pin_b_1]
    reset_a.occurrences = [
        NetOccurrence(
            id="net-occ:mcu-a:reset",
            net=reset_a,
            page=page_a,
            scope_id=page_a.scope_id,
            source_local_net_id="N$1",
            source_names={"RESET"},
            metadata={"source": "local-label"},
        )
    ]
    reset_b.occurrences = [
        NetOccurrence(
            id="net-occ:mcu-b:reset",
            net=reset_b,
            page=page_b,
            scope_id=page_b.scope_id,
            source_local_net_id="N$1",
            source_names={"RESET"},
            metadata={"source": "local-label"},
        )
    ]
    page_a.components = [comp_a]
    page_b.components = [comp_b]
    page_a.nets = [bus, reset_a]
    page_b.nets = [bus, reset_b]
    return Schematic(
        name="DUPLICATES",
        pages=[page_a, page_b],
        nets=[bus, reset_a, reset_b],
        components=[comp_a, comp_b],
    )


def test_serialize_contains_summary():
    text = serialize_design(_simple_design())
    assert "=== DESIGN SUMMARY ===" in text
    assert "TEST" in text
    assert "2 components" in text
    assert "2 nets" in text


def test_serialize_contains_component_section():
    text = serialize_design(_simple_design())
    assert "COMPONENT: U7 | AD7768-1 | IC - ADC - Single | Pages: ADC" in text
    assert "mfr: Analog Devices" in text
    assert "Pin 10" in text
    assert "-> ADC_SCLK" in text


def test_serialize_no_connect_vs_unconnected():
    text = serialize_design(_simple_design())
    assert "(no-connect)" in text
    assert "(unconnected)" in text


def test_serialize_contains_net_section():
    text = serialize_design(_simple_design())
    assert "NET: ADC_SCLK" in text
    assert "U7.10" in text
    assert "R1.1" in text
    assert "ADC/U7.10" not in text


def test_serialize_grep_friendly():
    """Grepping for a net name should hit both component and net sections."""
    text = serialize_design(_simple_design())
    lines_with_sclk = [line for line in text.splitlines() if "ADC_SCLK" in line]
    # Should appear in: component pin line(s) + net header + possibly net pin lines
    assert len(lines_with_sclk) >= 3  # U7 pin, R1 pin, NET header


def test_serialize_to_file(tmp_path: Path):
    from phosphor_eda.query.serialize import write_design

    design = _simple_design()
    out = tmp_path / "test.txt"
    write_design(design, out)
    assert out.exists()
    text = out.read_text()
    assert "=== DESIGN SUMMARY ===" in text


def test_serialize_normal_output_hides_ids_and_scope_paths():
    text = serialize_design(_duplicate_reference_design())
    assert "component:mcu-a:u7" not in text
    assert "net:mcu-a:reset" not in text
    assert "pin:mcu-a:u7:10" not in text
    assert "net-occ:mcu-a:reset" not in text
    assert "/root/mcu_a" not in text
    assert "N$1" not in text


def test_serialize_multi_page_logical_component_is_single_block():
    text = serialize_design(_multi_page_component_design())
    assert text.count("COMPONENT: U7 | AD7768-1 | ADC | Pages: A, B") == 1


def test_net_detail_qualifies_duplicate_component_references_only_when_ambiguous():
    simple_detail = format_net_detail(_simple_design(), "ADC_SCLK")
    assert "U7.10" in simple_detail
    assert "ADC/U7.10" not in simple_detail

    duplicate_detail = format_net_detail(_duplicate_reference_design(), "SYNC")
    assert "MCU_A/U7.10" in duplicate_detail
    assert "MCU_B/U7.10" in duplicate_detail


def test_pin_context_page_uses_page_id_identity_for_duplicate_references():
    page_a = Page(id="page:a", name="A")
    page_b = Page(id="page:b", name="B")
    same_page_b = Page(id=page_b.id, name=page_b.name)
    page_c = Page(id="page:c", name="C")
    comp_a = Component(
        id="component:a:u7",
        reference="U7",
        part="MCU",
        description="",
        pages=[page_a, page_b],
    )
    comp_b = Component(
        id="component:c:u7",
        reference="U7",
        part="MCU",
        description="",
        pages=[page_c],
    )
    net = Net(id="net:sig", name="SIG", pages=[same_page_b])
    pin = Pin(designator="10", name="SIG", component=comp_a, net=net)
    comp_a.pins = [pin]
    net.pins = [pin]
    page_a.components = [comp_a]
    page_b.components = [comp_a]
    page_c.components = [comp_b]
    page_b.nets = [net]
    design = Schematic(
        name="PAGE_IDS",
        pages=[page_a, page_b, page_c],
        nets=[net],
        components=[comp_a, comp_b],
    )

    detail = format_net_detail(design, "SIG")

    assert "B/U7.10" in detail
    assert "A/B/U7.10" not in detail


def test_duplicate_reference_labels_use_page_ids_when_page_names_are_ambiguous():
    page_a = Page(id="page:channel-a", name="Channel")
    page_b = Page(id="page:channel-b", name="Channel")
    comp_a = Component(
        id="component:channel-a:u7",
        reference="U7",
        part="MCU",
        description="Processor",
        pages=[page_a],
    )
    comp_b = Component(
        id="component:channel-b:u7",
        reference="U7",
        part="MCU",
        description="Processor",
        pages=[page_b],
    )
    net = Net(id="net:sync", name="SYNC", pages=[page_a, page_b])
    pin_a = Pin(id="pin:channel-a:u7:1", designator="1", name="SYNC", component=comp_a, net=net)
    pin_b = Pin(id="pin:channel-b:u7:1", designator="1", name="SYNC", component=comp_b, net=net)
    comp_a.pins = [pin_a]
    comp_b.pins = [pin_b]
    net.pins = [pin_a, pin_b]
    design = Schematic(
        name="DUPLICATE_PAGES",
        pages=[page_a, page_b],
        nets=[net],
        components=[comp_a, comp_b],
    )

    detail = format_net_detail(design, "SYNC")

    assert "page:channel-a/U7.1" in detail
    assert "page:channel-b/U7.1" in detail
    assert detail.count("Channel/U7.1") == 0


def test_duplicate_net_names_are_separate_blocks_with_minimal_marker():
    text = serialize_design(_duplicate_reference_design())
    reset_headers = [line for line in text.splitlines() if line.startswith("NET: RESET")]
    assert reset_headers == ["NET: RESET | Pages: MCU_A", "NET: RESET | Pages: MCU_B"]
    assert text.count("[name_not_unique: true]") == 2


def test_net_detail_hides_internal_resolver_provenance_metadata_by_default():
    detail = format_net_detail(_duplicate_reference_design(), "SYNC")
    assert "selected_name_source" not in detail
    assert "selected_name_source_id" not in detail
    assert "source_format" not in detail
    assert "source_local_net_ids" not in detail
    assert "source_scope_ids" not in detail


def test_net_provenance_is_not_printed_as_default_ids_in_summary_or_tables():
    text = serialize_design(_duplicate_reference_design())
    summary = text.split("=== COMPONENTS ===", maxsplit=1)[0]
    table = format_net_table(_duplicate_reference_design())
    assert "source_local_net_ids" not in summary
    assert "/root/mcu_a" not in summary
    assert "net-occ:mcu-a:reset" not in summary
    assert "source_local_net_ids" not in text
    assert "/root/mcu_a" not in text
    assert "net-occ:mcu-a:reset" not in text
    assert "source_local_net_ids" not in table
    assert "/root/mcu_a" not in table
    assert "net-occ:mcu-a:reset" not in table
    assert "local-label" not in text


def test_serialize_suppresses_electrical_passive():
    """electrical=passive should not appear in output (it's the default)."""
    page = Page(name="Test")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    net = Net(name="SIG")
    # Passive pin — should NOT show electrical metadata
    pin_passive = Pin(
        designator="1",
        name="A",
        component=comp,
        net=net,
        metadata={"electrical": "passive"},
    )
    # Power pin — SHOULD show electrical metadata
    pin_power = Pin(
        designator="2",
        name="VCC",
        component=comp,
        net=net,
        metadata={"electrical": "power"},
    )
    comp.pins = [pin_passive, pin_power]
    net.pins = [pin_passive, pin_power]
    page.components = [comp]
    page.nets = [net]
    design = Schematic(name="T", pages=[page], nets=[net], components=[comp])

    text = serialize_design(design)
    assert "electrical=passive" not in text
    assert "electrical=power" in text


def test_serialize_pin_metadata_inline():
    """Non-default pin metadata should appear inline on pin lines."""
    page = Page(name="Test")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    net = Net(name="SIG")
    pin = Pin(
        designator="1",
        name="CLK",
        component=comp,
        net=net,
        metadata={"electrical": "input", "owner_part_id": "2"},
    )
    comp.pins = [pin]
    net.pins = [pin]
    page.components = [comp]
    page.nets = [net]
    design = Schematic(name="T", pages=[page], nets=[net], components=[comp])

    text = serialize_design(design)
    pin_line = next(line for line in text.splitlines() if "Pin 1" in line)
    assert "electrical=input" in pin_line
    assert "owner_part_id=2" in pin_line


# ---- Metadata filtering tests ----


def test_passive_metadata_filtered():
    """Passives should only show value if not already in description."""
    page = Page(name="P")
    comp = Component(
        reference="R5",
        part="10k",
        description="Resistor 10k",
        pages=[page],
        metadata={"value": "10k", "Manufacturer": "Yageo", "mfr_pn": "XYZ"},
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Schematic(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    # value "10k" is already in description "Resistor 10k", so no metadata shown
    assert "Manufacturer" not in text
    assert "mfr_pn" not in text


def test_passive_value_shown_when_not_in_description():
    """Passive value is shown when it doesn't appear in the description."""
    page = Page(name="P")
    comp = Component(
        reference="C3",
        part="100nF",
        description="Capacitor",
        pages=[page],
        metadata={"value": "100nF"},
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Schematic(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    assert "value: 100nF" in text


def test_ic_metadata_allowlist():
    """IC metadata should only show allowlisted keys + URLs."""
    page = Page(name="P")
    comp = Component(
        reference="U1",
        part="LM358",
        description="Op-Amp",
        pages=[page],
        metadata={
            "mfr": "TI",
            "mfr_pn": "LM358DR",
            "Supplier": "Digi-Key",
            "SupplierPN": "296-1395-1-ND",
            "datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
            "UniqueId": "ABCDEF123",
        },
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Schematic(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    assert "mfr: TI" in text
    assert "mfr_pn: LM358DR" in text
    assert "https://www.ti.com" in text
    assert "Supplier" not in text
    assert "SupplierPN" not in text
    assert "UniqueId" not in text


# ---- Inline destinations tests ----


def test_inline_destinations_signal_net():
    """Signal net pins should show inline destination refs with trace-through."""
    design = _simple_design()
    text = serialize_design(design)
    # U7 pin 10 on ADC_SCLK — R1 is a shunt to GND (pull-down)
    u7_sclk_line = next(
        line
        for line in text.splitlines()
        if "Pin 10" in line and "SCLK" in line and "COMPONENT" not in line
    )
    assert "(R1 to GND)" in u7_sclk_line


def test_inline_destinations_power_net_excluded():
    """Power net pins should NOT show inline destination refs."""
    design = _simple_design()
    text = serialize_design(design)
    # U7 pin 7 on GND — no inline refs
    u7_gnd_line = next(line for line in text.splitlines() if "Pin 7" in line and "DGND" in line)
    assert "[" not in u7_gnd_line


def test_is_power_net_classname():
    """ClassName=PWR metadata should mark a net as power."""
    from phosphor_eda.query.serialize import is_power_net

    net = Net(name="CUSTOM_RAIL", metadata={"ClassName": "PWR"})
    assert is_power_net("CUSTOM_RAIL", net)
    assert not is_power_net("CUSTOM_RAIL")


# ---- Table formatter tests ----


def test_format_component_table():
    design = _simple_design()
    table = format_component_table(design)
    assert "REF" in table
    assert "PART" in table
    assert "R1" in table
    assert "U7" in table
    assert "AD7768-1" in table


def test_format_net_table():
    design = _simple_design()
    table = format_net_table(design)
    assert "NET" in table
    assert "ADC_SCLK" in table
    assert "GND" in table


def test_format_page_table():
    design = _simple_design()
    table = format_page_table(design)
    assert "PAGE" in table
    assert "ADC" in table
    assert "page:ADC" not in table


def test_format_page_table_includes_ids_for_duplicate_page_names():
    first = Page(name="Channel", id="page:channel-a")
    second = Page(name="Channel", id="page:channel-b")
    design = Schematic(name="REPEATED", pages=[first, second])

    table = format_page_table(design)

    assert "PAGE ID" in table
    assert "page:channel-a" in table
    assert "page:channel-b" in table


# ---- Detail formatter tests ----


def _repeated_instance_design() -> Schematic:
    """Two physically distinct U1 instances (same logical reference, Case B)."""
    page_a = Page(name="CH_A", id="page:ch-a", scope_id=ScopeId(path=("root", "ch_a")))
    page_b = Page(name="CH_B", id="page:ch-b", scope_id=ScopeId(path=("root", "ch_b")))
    comp_a = Component(id="component:ch-a:u1", reference="U1", part="BUF", description="Buffer")
    comp_b = Component(id="component:ch-b:u1", reference="U1", part="BUF", description="Buffer")
    comp_a.pages = [page_a]
    comp_b.pages = [page_b]
    net_a = Net(id="net:ch-a:out", name="OUT_A", pages=[page_a])
    net_b = Net(id="net:ch-b:out", name="OUT_B", pages=[page_b])
    pin_a = Pin(id="pin:ch-a:u1:1", designator="1", name="Y", component=comp_a, net=net_a)
    pin_b = Pin(id="pin:ch-b:u1:1", designator="1", name="Y", component=comp_b, net=net_b)
    comp_a.pins = [pin_a]
    comp_b.pins = [pin_b]
    net_a.pins = [pin_a]
    net_b.pins = [pin_b]
    comp_a.occurrences = [
        ComponentOccurrence(
            id="occ:ch-a:u1",
            component=comp_a,
            page=page_a,
            scope_id=page_a.scope_id,
            source_id="source-ch-a-u1",
            physical_designator="U1.1",
        )
    ]
    comp_b.occurrences = [
        ComponentOccurrence(
            id="occ:ch-b:u1",
            component=comp_b,
            page=page_b,
            scope_id=page_b.scope_id,
            source_id="source-ch-b-u1",
            physical_designator="U1.3",
        )
    ]
    page_a.components = [comp_a]
    page_b.components = [comp_b]
    page_a.nets = [net_a]
    page_b.nets = [net_b]
    return Schematic(
        name="REPEATED",
        pages=[page_a, page_b],
        nets=[net_a, net_b],
        components=[comp_a, comp_b],
    )


def test_component_detail_resolves_by_physical_designator():
    """A physical designator (U1.3) resolves to that specific occurrence."""
    design = _repeated_instance_design()
    detail = format_component_detail(design, "U1.3")
    assert "COMPONENT: U1" in detail
    assert "U1.3" in detail
    assert "CH_B" in detail
    assert "CH_A" not in detail


def test_component_detail_ambiguous_logical_reference_lists_physical_designators():
    """Looking up an ambiguous logical reference names the physical designators."""
    design = _repeated_instance_design()
    with pytest.raises(ValueError, match=r"ambiguous.*U1\.1.*U1\.3"):
        _ = format_component_detail(design, "U1")


def test_component_block_qualifies_ambiguous_reference_with_physical_designator():
    """The component block header qualifies ambiguous instances by designator."""
    design = _repeated_instance_design()
    out = serialize_design(design)
    assert "U1 [U1.1]" in out
    assert "U1 [U1.3]" in out


def test_single_instance_design_shows_no_physical_designator_suffix():
    """An un-annotated design shows no designator suffixes in component output."""
    design = _simple_design()
    out = serialize_design(design)
    component_lines = [line for line in out.splitlines() if line.startswith("COMPONENT:")]
    assert component_lines
    for line in component_lines:
        assert "[" not in line
    detail = format_component_detail(design, "U7")
    assert detail.splitlines()[0] == "COMPONENT: U7 | AD7768-1 | IC - ADC - Single | Pages: ADC"


def test_format_component_detail():
    design = _simple_design()
    detail = format_component_detail(design, "U7")
    assert "COMPONENT: U7" in detail
    assert "Pin 10" in detail
    assert "SCLK" in detail
    # R1 is a shunt (pin 1 on ADC_SCLK, pin 2 on GND)
    assert "(R1 to GND)" in detail


def test_format_component_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        _ = format_component_detail(design, "U99")


def test_format_net_detail():
    design = _simple_design()
    detail = format_net_detail(design, "ADC_SCLK")
    assert "NET: ADC_SCLK" in detail
    assert "U7.10" in detail
    assert "R1.1" in detail


def test_format_net_detail_disambiguates_duplicate_net_names_by_id():
    design = _duplicate_reference_design()

    with pytest.raises(ValueError, match=r"ambiguous.*net:mcu-a:reset.*net:mcu-b:reset"):
        _ = format_net_detail(design, "RESET")

    detail = format_net_detail(design, "net:mcu-b:reset")

    assert "NET: RESET" in detail
    assert "MCU_B/U7.1" in detail
    assert "MCU_A/U7.1" not in detail


def test_format_net_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        _ = format_net_detail(design, "NONEXISTENT")


def test_format_page_detail():
    design = _simple_design()
    detail = format_page_detail(design, "ADC")
    assert "PAGE: ADC" in detail
    assert "U7" in detail
    assert "R1" in detail


def test_format_page_detail_filters_unified_net_pins_to_selected_page():
    design = _filterable_design()
    detail = format_page_detail(design, "SPI")

    assert "P3V3" in detail
    assert "U1.3" in detail
    assert "C1.1" not in detail
    assert "U3.2" not in detail


def test_format_page_detail_filters_unified_component_pins_by_pin_occurrence():
    page_a = Page(name="Core")
    page_b = Page(name="IO")
    comp = Component(reference="U1", part="MCU", description="Processor", pages=[page_a, page_b])
    net = Net(name="P3V3", pages=[page_a, page_b])
    pin_a = Pin(designator="1", name="VDD", component=comp, net=net)
    pin_b = Pin(designator="2", name="VDDIO", component=comp, net=net)

    pin_a.occurrences = [
        PinOccurrence(
            id="pin-occ:u1-core-1",
            pin=pin_a,
            page=page_a,
            scope_id=page_a.scope_id,
            source_id="source:u1-core:pin-1",
        )
    ]
    pin_b.occurrences = [
        PinOccurrence(
            id="pin-occ:u1-io-2",
            pin=pin_b,
            page=page_b,
            scope_id=page_b.scope_id,
            source_id="source:u1-io:pin-2",
        )
    ]
    comp.pins = [pin_a, pin_b]
    net.pins = [pin_a, pin_b]
    page_a.components = [comp]
    page_a.nets = [net]
    page_b.components = [comp]
    page_b.nets = [net]
    design = Schematic(name="MULTIPART", pages=[page_a, page_b], nets=[net], components=[comp])

    detail = format_page_detail(design, "Core")

    assert "U1.1" in detail
    assert "U1.2" not in detail


def test_format_page_detail_ignores_pin_source_id_metadata_for_page_membership():
    page_a = Page(name="Core", id="page:core", source_file="core.kicad_sch")
    page_b = Page(name="IO", id="page:io", source_file="io.kicad_sch")
    comp = Component(reference="U1", part="MCU", description="Processor", pages=[page_a])
    net = Net(name="P3V3", pages=[page_a, page_b])
    pin = Pin(
        designator="1",
        name="VDD",
        component=comp,
        net=net,
        metadata={"kicad_pin_source_id": "symbol:u1:page:io:io.kicad_sch:pin-1"},
    )
    comp.pins = [pin]
    net.pins = [pin]
    page_a.components = [comp]
    page_a.nets = [net]
    page_b.nets = [net]
    design = Schematic(
        name="METADATA_PAGE_IDS",
        pages=[page_a, page_b],
        nets=[net],
        components=[comp],
    )

    detail = format_page_detail(design, "IO")

    assert "P3V3" in detail
    assert "U1.1" not in detail


def test_format_page_detail_rejects_ambiguous_duplicate_page_names():
    first = Page(name="Channel", id="page:channel-a", scope_id=ScopeId(path=("root", "a")))
    second = Page(name="Channel", id="page:channel-b", scope_id=ScopeId(path=("root", "b")))
    design = Schematic(name="REPEATED", pages=[first, second])

    with pytest.raises(ValueError, match=r"ambiguous.*page:channel-a.*page:channel-b"):
        _ = format_page_detail(design, "Channel")

    detail = format_page_detail(design, "page:channel-b")

    assert "PAGE: Channel" in detail


def test_format_page_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        _ = format_page_detail(design, "NONEXISTENT")


# ---- Filterable design helper ----


def _filterable_design():
    """Build a 2-page design with mixed component types and net types."""
    page_power = Page(name="Power")
    page_spi = Page(name="SPI")

    # Components
    u1 = Component(
        reference="U1",
        part="MCU",
        description="Microcontroller",
        pages=[page_spi],
        metadata={},
    )
    u2 = Component(reference="U2", part="AD7768", description="ADC", pages=[page_spi], metadata={})
    r1 = Component(
        reference="R1",
        part="100R",
        description="Resistor",
        pages=[page_spi],
        metadata={},
    )
    r2 = Component(
        reference="R2",
        part="4k7",
        description="Resistor",
        pages=[page_spi],
        metadata={},
    )
    c1 = Component(
        reference="C1",
        part="100nF",
        description="Capacitor",
        pages=[page_power],
        metadata={},
    )
    tp1 = Component(
        reference="TP1",
        part="TestPoint",
        description="Test Point",
        pages=[page_spi],
        metadata={},
    )
    vreg = Component(
        reference="U3",
        part="LM1117",
        description="Regulator",
        pages=[page_power],
        metadata={},
    )

    # Nets
    spi_clk = Net(name="SPI_CLK")
    spi_mosi = Net(name="SPI_MOSI")
    p3v3 = Net(name="P3V3")
    gnd = Net(name="GND")

    # Wiring — SPI page
    # U1.1 -> SPI_CLK -> R1.1, R1.2 -> SPI_CLK_B -> U2.1 (series)
    # R2 pull-up: SPI_CLK -> R2.1, R2.2 -> P3V3
    # TP1 on SPI_CLK
    spi_clk_b = Net(name="SPI_CLK_B")

    def connect(pin: DomainPin, net: DomainNet) -> None:
        pin.net = net
        net.pins.append(pin)

    pin_u1_1 = Pin(designator="1", name="SCK", component=u1, metadata={})
    pin_u1_2 = Pin(designator="2", name="MOSI", component=u1, metadata={})
    pin_u1_3 = Pin(designator="3", name="VDD", component=u1, metadata={})
    u1.pins = [pin_u1_1, pin_u1_2, pin_u1_3]

    pin_u2_1 = Pin(designator="1", name="SCLK", component=u2, metadata={})
    pin_u2_2 = Pin(designator="2", name="DIN", component=u2, metadata={})
    pin_u2_3 = Pin(designator="3", name="GND", component=u2, metadata={})
    u2.pins = [pin_u2_1, pin_u2_2, pin_u2_3]

    pin_r1_1 = Pin(designator="1", name="", component=r1, metadata={})
    pin_r1_2 = Pin(designator="2", name="", component=r1, metadata={})
    r1.pins = [pin_r1_1, pin_r1_2]

    pin_r2_1 = Pin(designator="1", name="", component=r2, metadata={})
    pin_r2_2 = Pin(designator="2", name="", component=r2, metadata={})
    r2.pins = [pin_r2_1, pin_r2_2]

    pin_c1_1 = Pin(designator="1", name="", component=c1, metadata={})
    pin_c1_2 = Pin(designator="2", name="", component=c1, metadata={})
    c1.pins = [pin_c1_1, pin_c1_2]

    pin_tp1 = Pin(designator="1", name="", component=tp1, metadata={})
    tp1.pins = [pin_tp1]

    pin_vreg_1 = Pin(designator="1", name="IN", component=vreg, metadata={})
    pin_vreg_2 = Pin(designator="2", name="OUT", component=vreg, metadata={})
    pin_vreg_3 = Pin(designator="3", name="GND", component=vreg, metadata={})
    vreg.pins = [pin_vreg_1, pin_vreg_2, pin_vreg_3]

    connect(pin_u1_1, spi_clk)
    connect(pin_r1_1, spi_clk)
    connect(pin_r2_1, spi_clk)
    connect(pin_tp1, spi_clk)
    connect(pin_r1_2, spi_clk_b)
    connect(pin_u2_1, spi_clk_b)

    connect(pin_u1_2, spi_mosi)
    connect(pin_u2_2, spi_mosi)

    connect(pin_r2_2, p3v3)
    connect(pin_u1_3, p3v3)
    connect(pin_c1_1, p3v3)
    connect(pin_vreg_2, p3v3)

    connect(pin_u2_3, gnd)
    connect(pin_c1_2, gnd)
    connect(pin_vreg_3, gnd)

    page_spi.components = [u1, u2, r1, r2, tp1]
    page_spi.nets = [spi_clk, spi_clk_b, spi_mosi, p3v3]
    page_power.components = [c1, vreg]
    page_power.nets = [p3v3, gnd]

    # P3V3 spans both pages (U1.VDD is on SPI page, vreg/C1 on Power page)
    all_nets: Sequence[DomainNet] = [spi_clk, spi_clk_b, spi_mosi, p3v3, gnd]
    all_comps: Sequence[DomainComponent] = [u1, u2, r1, r2, c1, tp1, vreg]

    return Schematic(
        name="FILTER_TEST",
        pages=[page_power, page_spi],
        nets=list(all_nets),
        components=list(all_comps),
    )


# ---- Filter tests ----


def test_filter_nets_by_component():
    design = _filterable_design()
    result = filter_nets(design, components=["U1"])
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "SPI_MOSI" in names
    assert "P3V3" in names
    assert "GND" not in names


def test_filter_nets_component_intersection():
    design = _filterable_design()
    result = filter_nets(design, components=["U1", "U2"])
    names = {n.name for n in result}
    # U1 and U2 share SPI_MOSI directly
    assert "SPI_MOSI" in names
    # SPI_CLK only has U1 (not U2, which is on SPI_CLK_B via R1)
    assert "SPI_CLK" not in names


def test_filter_nets_component_intersection_with_trace():
    design = _filterable_design()
    result = filter_nets(design, components=["U1", "U2"], trace=True)
    names = {n.name for n in result}
    # With trace, SPI_CLK reaches U2 through R1
    assert "SPI_CLK" in names
    assert "SPI_MOSI" in names


def test_filter_nets_by_page():
    design = _filterable_design()
    result = filter_nets(design, pages=["Power"])
    names = {n.name for n in result}
    assert "P3V3" in names
    assert "GND" in names
    assert "SPI_CLK" not in names


def test_filter_nets_power_only():
    design = _filterable_design()
    result = filter_nets(design, power=True)
    names = {n.name for n in result}
    assert names == {"P3V3", "GND"}


def test_filter_nets_no_power():
    design = _filterable_design()
    result = filter_nets(design, power=False)
    names = {n.name for n in result}
    assert "P3V3" not in names
    assert "GND" not in names
    assert "SPI_CLK" in names


def test_filter_nets_min_pins():
    design = _filterable_design()
    result = filter_nets(design, min_pins=3)
    # SPI_CLK has 4 pins (U1, R1, R2, TP1), P3V3 has 4 pins
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "P3V3" in names
    # SPI_MOSI has 2 pins, SPI_CLK_B has 2
    assert "SPI_MOSI" not in names


def test_filter_nets_multi_page():
    design = _filterable_design()
    result = filter_nets(design, multi_page=True)
    names = {n.name for n in result}
    # P3V3 spans Power (vreg, C1) and SPI (U1) pages
    assert "P3V3" in names


def test_filter_nets_composable():
    design = _filterable_design()
    result = filter_nets(design, components=["U1"], power=False)
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "P3V3" not in names


def test_filter_components_by_page():
    design = _filterable_design()
    result = filter_components(design, pages=["Power"])
    refs = {c.reference for c in result}
    assert refs == {"C1", "U3"}


def test_filter_components_by_prefix():
    design = _filterable_design()
    result = filter_components(design, prefixes=["U"])
    refs = {c.reference for c in result}
    assert refs == {"U1", "U2", "U3"}


def test_filter_components_by_prefix_tp():
    design = _filterable_design()
    result = filter_components(design, prefixes=["TP"])
    refs = {c.reference for c in result}
    assert refs == {"TP1"}


def test_filter_components_passive_only():
    design = _filterable_design()
    result = filter_components(design, passive=True)
    refs = {c.reference for c in result}
    assert refs == {"R1", "R2", "C1"}


def test_filter_components_no_passive():
    design = _filterable_design()
    result = filter_components(design, passive=False)
    refs = {c.reference for c in result}
    assert "R1" not in refs
    assert "U1" in refs
    assert "TP1" in refs


def test_filter_components_min_pins():
    design = _filterable_design()
    result = filter_components(design, min_pins=3)
    refs = {c.reference for c in result}
    assert "U1" in refs  # 3 pins
    assert "R1" not in refs  # 2 pins


def test_filter_components_by_net():
    design = _filterable_design()
    result = filter_components(design, net="SPI_CLK")
    refs = {c.reference for c in result}
    assert "U1" in refs
    assert "R1" in refs
    assert "TP1" in refs
    assert "U2" not in refs  # U2 is on SPI_CLK_B


def test_filter_pages_by_net():
    design = _filterable_design()
    result = filter_pages(design, nets=["P3V3"])
    names = {p.name for p in result}
    assert "Power" in names


def test_filter_pages_by_component():
    design = _filterable_design()
    result = filter_pages(design, components=["U1"])
    names = {p.name for p in result}
    assert "SPI" in names
    assert "Power" not in names


# ---- Trace formatting tests ----


def test_format_trace_series():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # Should show SPI_CLK path through R1
    assert "R1" in output
    assert "U1" in output
    assert "U2" in output


def test_format_trace_direct():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # SPI_MOSI is a direct connection
    assert "MOSI" in output


def test_format_trace_no_connection():
    design = _filterable_design()
    output = format_trace(design, "U2", "U3")
    assert "No signal paths" in output


def test_format_trace_shunts_shown():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # R2 is a pull-up on SPI_CLK
    assert "R2" in output
    assert "P3V3" in output


def test_format_trace_duplicate_reference_lookup_is_ambiguous():
    with pytest.raises(ValueError, match="ambiguous"):
        _ = format_trace(_duplicate_reference_design(), "U7", "U7")


def test_format_component_detail_trace_through():
    """show component should trace through series passives."""
    design = _filterable_design()
    detail = format_component_detail(design, "U1")
    # SCK pin: R1 is series to U2, R2 is shunt to P3V3, TP1 is direct
    assert "R1 -> U2.1" in detail
    assert "R2 to P3V3" in detail
    assert "TP1.1" in detail


def test_inline_destinations_fan_out_lists_each_endpoint():
    """A series passive feeding two ICs renders one destination per endpoint."""
    from phosphor_eda.query.serialize import _trace_destinations

    page = Page(name="P")
    u1 = Component(reference="U1", part="MCU", description="", pages=[page])
    r1 = Component(reference="R1", part="100R", description="", pages=[page])
    u2 = Component(reference="U2", part="ADC", description="", pages=[page])
    u3 = Component(reference="U3", part="DAC", description="", pages=[page])

    sig_in = Net(name="SIG_IN", pages=[page])
    sig_out = Net(name="SIG_OUT", pages=[page])

    pin_u1 = Pin(designator="1", name="", component=u1, net=sig_in)
    pin_r1_1 = Pin(designator="1", name="", component=r1, net=sig_in)
    pin_r1_2 = Pin(designator="2", name="", component=r1, net=sig_out)
    pin_u2 = Pin(designator="1", name="", component=u2, net=sig_out)
    pin_u3 = Pin(designator="1", name="", component=u3, net=sig_out)
    u1.pins = [pin_u1]
    r1.pins = [pin_r1_1, pin_r1_2]
    u2.pins = [pin_u2]
    u3.pins = [pin_u3]
    sig_in.pins = [pin_u1, pin_r1_1]
    sig_out.pins = [pin_r1_2, pin_u2, pin_u3]

    page.components = [u1, r1, u2, u3]
    page.nets = [sig_in, sig_out]
    design = Schematic(
        name="FANOUT",
        pages=[page],
        nets=[sig_in, sig_out],
        components=[u1, r1, u2, u3],
    )

    rendered = _trace_destinations(design, pin_u1, u1)
    assert "R1 -> U2.1" in rendered
    assert "R1 -> U3.1" in rendered
