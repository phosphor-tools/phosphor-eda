"""Altium component enrichment: parameters, links, kind, title blocks."""

from pathlib import Path

import pytest

from phosphor_eda.domain.schematic import (
    ComponentKind,
    FootprintModel,
    LibraryLink,
    Parameter,
    PartNumber,
    Schematic,
)
from phosphor_eda.formats.altium.records import (
    ComponentRec,
    ImplementationRec,
    ParameterRec,
    RecordType,
)
from phosphor_eda.formats.altium.source import (
    _component_info,
    _component_kind,
    _component_parameters,
)
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext

FIXTURES = Path(__file__).parent / "fixtures"
QFSAE_PRJPCB = FIXTURES / "altium" / "qfsae-debugger" / "Debugger.PrjPcb"
PIMX8_PRJPCB = FIXTURES / "altium" / "pi-mx8" / "PiMX8MP_r0.3_release.PrjPcb"


@pytest.fixture(scope="module")
def qfsae() -> Schematic:
    return altium_to_design(QFSAE_PRJPCB, name="QFSAE")


@pytest.fixture(scope="module")
def pimx8() -> Schematic:
    return altium_to_design(PIMX8_PRJPCB)


def _component(design: Schematic, reference: str):
    component = next((c for c in design.components if c.reference == reference), None)
    assert component is not None, f"component {reference} not found"
    return component


def _param_rec(index: int, name: str, text: str, *, hidden: bool = True) -> ParameterRec:
    """Build a RECORD=41 ParameterRec the way record_factory would."""
    props = {"record": "41", "ownerindex": "0", "name": name, "text": text}
    if hidden:
        props["ishidden"] = "T"
    return ParameterRec.from_properties(index, props, ParseContext())


class TestParameters:
    def test_ordered_parameters_with_hidden_flag(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        names = [p.name for p in p1.parameters]
        # RECORD=41 children in document order; Comment is written last.
        assert names[:4] == [
            "Manufacturer URL",
            "Withstanding Voltage",
            "Operating Temperature",
            "Manufacturer",
        ]
        assert names[-1] == "Comment"
        by_name = {p.name: p for p in p1.parameters}
        assert by_name["Manufacturer"] == Parameter(
            name="Manufacturer", value="Wurth Elektronik", visible=False
        )
        assert by_name["Comment"].visible

    def test_parameters_reach_metadata(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        assert p1.metadata["Manufacturer"] == "Wurth Elektronik"
        assert p1.metadata["Comment"] == "61300411121"


class TestIndirectParameters:
    def test_indirect_reference_resolves_case_insensitively(self) -> None:
        records = [
            _param_rec(0, "Value", "10k"),
            _param_rec(1, "Comment", "=value", hidden=False),
        ]
        parameters = _component_parameters(records)
        assert parameters == (
            Parameter(name="Value", value="10k", visible=False),
            Parameter(name="Comment", value="10k", visible=True, indirect=True),
        )

    def test_chained_indirect_reference_resolves(self) -> None:
        records = [
            _param_rec(0, "Resistance", "4k7"),
            _param_rec(1, "Value", "=Resistance"),
            _param_rec(2, "Comment", "=Value"),
        ]
        parameters = _component_parameters(records)
        assert parameters[2].value == "4k7"
        assert parameters[2].indirect

    def test_unresolvable_reference_keeps_literal_text(self) -> None:
        records = [_param_rec(0, "Comment", "=Missing")]
        parameters = _component_parameters(records)
        assert parameters == (
            Parameter(name="Comment", value="=Missing", visible=False, indirect=True),
        )

    def test_cyclic_reference_keeps_literal_text(self) -> None:
        records = [
            _param_rec(0, "A", "=B"),
            _param_rec(1, "B", "=A"),
        ]
        parameters = _component_parameters(records)
        assert parameters[0].value == "=B"
        assert parameters[1].value == "=A"


class TestResolvedFields:
    def test_library_link(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        assert p1.lib == LibraryLink(
            symbol="WE-CN-1P-M-R4",
            library="Altium Content Vault",
            design_item_id="CMP-1502-01064-1",
        )

    def test_footprint_models(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        assert p1.footprints == [
            FootprintModel(
                name="61300411121",
                is_current=True,
                description="Pin Header, pitch 2.54 mm, THT, Vertical, single row, 4p",
            )
        ]
        assert p1.footprint is not None
        assert p1.footprint.name == "61300411121"

    def test_footprint_library_from_modeldatafile(self, pimx8: Schematic) -> None:
        u1 = _component(pimx8, "U1")
        assert u1.footprints == [
            FootprintModel(
                name="QFN40P700X700X100_HS-57L",
                library="QV_SwReg.PcbLib",
                is_current=True,
                description=(
                    "QFN, 56-Leads, Body 7,00x7,00mm, Pitch 0,40mm, "
                    "Thermal Pad 4,70x4,70mm, IPC High Density"
                ),
            )
        ]

    def test_part_number_resolved(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        assert p1.part_numbers == [
            PartNumber(manufacturer="Wurth Elektronik", number="61300411121")
        ]


class TestComponentKind:
    def test_net_tie_from_fixture(self, pimx8: Schematic) -> None:
        nt1 = _component(pimx8, "NT1")
        assert nt1.kind is ComponentKind.NET_TIE

    def test_standard_when_kind_absent(self, qfsae: Schematic) -> None:
        p1 = _component(qfsae, "P1")
        assert p1.kind is ComponentKind.STANDARD

    @pytest.mark.parametrize(
        ("raw_kind", "expected"),
        [
            (0, ComponentKind.STANDARD),
            (1, ComponentKind.MECHANICAL),
            (2, ComponentKind.GRAPHICAL),
            (3, ComponentKind.NET_TIE),
            (4, ComponentKind.NET_TIE),
            (5, ComponentKind.STANDARD),
            (6, ComponentKind.OTHER),
            (99, ComponentKind.OTHER),
        ],
    )
    def test_kind_mapping(self, raw_kind: int, expected: ComponentKind) -> None:
        component = ComponentRec(record_type=RecordType.COMPONENT, index=0, component_kind=raw_kind)
        assert _component_kind(component) is expected

    def test_standard_no_bom_excludes_from_bom(self) -> None:
        component = ComponentRec(record_type=RecordType.COMPONENT, index=0, component_kind=5)
        info = _component_info(component, (), ())
        assert info.kind is ComponentKind.STANDARD
        assert info.exclude_from_bom is True
        # Altium has no native DNP flag; the convention matcher decides.
        assert info.explicit_dnp is None


class TestRecordParsing:
    def test_component_rec_parses_source_library_and_kind(self) -> None:
        props = {
            "record": "1",
            "libreference": "NetTie_0.2mm",
            "sourcelibraryname": "QV_res.SchLib",
            "componentkind": "4",
        }
        rec = ComponentRec.from_properties(0, props, ParseContext())
        assert rec.source_library_name == "QV_res.SchLib"
        assert rec.component_kind == 4

    def test_implementation_rec_parses_model_fields(self) -> None:
        props = {
            "record": "45",
            "modelname": "QFN40P700X700X100_HS-57L",
            "modeltype": "PCBLIB",
            "modeldatafile0": "QV_SwReg.PcbLib",
            "iscurrent": "T",
            "description": "QFN, 56-Leads",
        }
        rec = ImplementationRec.from_properties(0, props, ParseContext())
        assert rec.model_name == "QFN40P700X700X100_HS-57L"
        assert rec.model_library == "QV_SwReg.PcbLib"
        assert rec.is_current is True
        assert rec.description == "QFN, 56-Leads"

    def test_implementation_rec_is_current_defaults_false(self) -> None:
        rec = ImplementationRec.from_properties(
            0, {"record": "45", "modelname": "X"}, ParseContext()
        )
        assert rec.is_current is False


class TestTitleBlock:
    def test_qfsae_title_block_fields(self, qfsae: Schematic) -> None:
        root = qfsae.pages[0]
        assert root.title_block is not None
        assert root.title_block.company == "Altium Limited"
        assert root.title_block.metadata["Address1"] == "L3, 12a Rodborough Rd"
        # The fixture leaves Title/Date at Altium's "*" placeholder — unset.
        assert root.title_block.title == ""
        assert root.title_block.date == ""

    def test_pimx8_title_block_fields(self, pimx8: Schematic) -> None:
        pmic = next(p for p in pimx8.pages if p.name == "02_8MPLUS_PMIC")
        assert pmic.title_block is not None
        assert pmic.title_block.title == "8MPLUS_PMIC"
        assert pmic.title_block.metadata["Author"] == "Lukas Henkel"
        assert pmic.title_block.metadata["DocumentNumber"] == "2"
