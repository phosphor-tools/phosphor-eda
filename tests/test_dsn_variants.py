from __future__ import annotations

import pytest

from phosphor_eda.domain.schematic import Component, FootprintModel, Parameter, Schematic
from phosphor_eda.domain.variants import VariantField
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.raw_models import (
    DsnCisBom,
    DsnCisGroup,
    DsnCisGroupMember,
    DsnCisStringList,
    DsnCisUpdateStorageRow,
    DsnCisVariantStore,
    ParsedDesign,
)
from phosphor_eda.formats.dsn.variants import map_orcad_cis_not_fitted_variants


def _component(*, parameters: list[Parameter] | None = None) -> Component:
    return Component(
        id="component:r1",
        reference="R1",
        part="RESISTOR",
        description="",
        parameters=parameters or [],
        metadata={
            "dsn_component_source_ids": "page:Main:component:100",
            "dsn_component_db_id": "100",
            "internal": "NI",
        },
    )


def _raw_store(group_name: str, *, bom_values: list[str]) -> ParsedDesign:
    group = DsnCisGroup(
        name=group_name,
        stream_path="CIS/VariantStore/Groups/GroupsDataStream",
        row_order=0,
        raw_fields=[group_name, "0"],
        members=[
            DsnCisGroupMember(
                stream_path=f"CIS/VariantStore/Groups/{group_name}/{group_name}",
                row_order=0,
                state="0",
                occurrence_id=10,
                resolved_instance_db_id=100,
                resolution_kind="hierarchy_occurrence",
            )
        ],
    )
    return ParsedDesign(
        cis_variant_store=DsnCisVariantStore(
            present=True,
            groups=[group],
            boms=[
                DsnCisBom(
                    name="Standard",
                    child_string_lists=[
                        DsnCisStringList(
                            stream_path="CIS/VariantStore/BOM/Standard/Standard",
                            values=bom_values,
                        )
                    ],
                )
            ],
        )
    )


@pytest.mark.parametrize("group_name", ["DNP_RANGE", "MINI"])
def test_orcad_cis_not_fitted_variants_require_whole_tokens(group_name: str) -> None:
    raw = _raw_store(group_name, bom_values=[group_name])
    schematic = Schematic(
        name="demo",
        components=[_component(parameters=[Parameter(name="MPN", value="DNP")])],
    )

    assert map_orcad_cis_not_fitted_variants(raw, schematic) == []


def test_orcad_cis_not_fitted_variants_require_independent_evidence() -> None:
    raw = _raw_store("DNP", bom_values=["Common"])
    schematic = Schematic(name="demo", components=[_component()])

    assert map_orcad_cis_not_fitted_variants(raw, schematic) == []


def _dni_member(db_id: int, *, row_order: int = 0) -> DsnCisGroupMember:
    return DsnCisGroupMember(
        stream_path="CIS/VariantStore/Groups/DNI/DNI",
        row_order=row_order,
        state="0",
        occurrence_id=10 + row_order,
        resolved_instance_db_id=db_id,
        resolution_kind="hierarchy_occurrence",
    )


def _dni_store(
    *, members: list[DsnCisGroupMember], rows: list[DsnCisUpdateStorageRow]
) -> ParsedDesign:
    group = DsnCisGroup(
        name="DNI",
        stream_path="CIS/VariantStore/Groups/GroupsDataStream",
        row_order=0,
        raw_fields=["DNI", "0"],
        members=members,
        update_storage_rows=rows,
    )
    return ParsedDesign(
        cis_variant_store=DsnCisVariantStore(
            present=True,
            groups=[group],
            boms=[
                DsnCisBom(
                    name="Standard",
                    child_string_lists=[
                        DsnCisStringList(
                            stream_path="CIS/VariantStore/BOM/Standard/Standard",
                            values=["DNI"],
                        )
                    ],
                )
            ],
        )
    )


def test_variant_identity_uses_typed_db_id_not_string_index_collision() -> None:
    # A component whose db_id was 0 falls back to its instance index in the
    # source id string; parsing that index back out let it collide with a real
    # db_id == index component. The typed dsn_component_db_id key (set only for a
    # real db_id) resolves the DNI member to the correct component (A7 / R5).
    real = Component(
        id="component:real5",
        reference="R_REAL",
        part="RES",
        description="",
        metadata={
            "dsn_component_source_ids": "page:P:component:5",
            "dsn_component_db_id": "5",
        },
    )
    index_fallback = Component(
        id="component:idx5",
        reference="R_IDX",
        part="RES",
        # db_id 0 -> source id built from instance index 5; no typed db_id key.
        description="",
        metadata={"dsn_component_source_ids": "page:P:component:5"},
    )
    raw = _dni_store(members=[_dni_member(5)], rows=[])
    schematic = Schematic(name="demo", components=[real, index_fallback])

    variants = map_orcad_cis_not_fitted_variants(raw, schematic)

    assert len(variants) == 1
    dnp = [o for o in variants[0].overrides if o.field == VariantField.DNP]
    assert len(dnp) == 1
    assert dnp[0].target.reference == "R_REAL"


def test_repeated_sheet_shared_db_id_is_ambiguous_and_skipped() -> None:
    # A repeated hierarchical sheet places one page-level instance db id once per
    # occurrence, so several placed components share a db_id and differ only by
    # per-occurrence refdes. db-id-keyed CIS matching cannot tell them apart, so
    # rather than silently targeting the last-placed occurrence the collision is
    # flagged and the db_id dropped — no override is emitted.
    def _occurrence(reference: str) -> Component:
        return Component(
            id=f"component:{reference}",
            reference=reference,
            part="RES",
            description="",
            metadata={
                "dsn_component_source_ids": f"page:CH:{reference}:component:7",
                "dsn_component_db_id": "7",
            },
        )

    occ_a = _occurrence("R_CH0")
    occ_b = _occurrence("R_CH1")
    raw = _dni_store(members=[_dni_member(7)], rows=[])
    schematic = Schematic(name="demo", components=[occ_a, occ_b])
    ctx = ParseContext()

    variants = map_orcad_cis_not_fitted_variants(raw, schematic, ctx)

    assert variants == []
    ambiguous_warnings = [
        issue
        for issue in ctx.issues
        if issue.category == "dsn_cis_variant" and "multiple occurrences" in issue.message
    ]
    assert len(ambiguous_warnings) == 1
    assert "7" in ambiguous_warnings[0].message


def test_update_storage_differing_footprint_emits_typed_change_override() -> None:
    # When a snapshot value provably differs from the recoverable base, the
    # column maps to a typed change override with base_value (G3). This is the
    # only path that yields a change-claiming override; no known fixture hits it.
    component = Component(
        id="component:r1",
        reference="R1",
        part="RES",
        description="",
        footprints=[FootprintModel(name="RES_0402", is_current=True)],
        metadata={
            "dsn_component_source_ids": "page:P:component:9",
            "dsn_component_db_id": "9",
        },
    )
    row = DsnCisUpdateStorageRow(
        stream_path="CIS/VariantStore/Groups/DNI/UpdateStorageGroupDataStream",
        row_order=0,
        occurrence_id=19,
        resolved_instance_db_id=9,
        resolution_kind="hierarchy_occurrence",
        columns=["PCB Footprint", "Part Number"],
        values=["RES_0603", "UNDEFINED"],
    )
    raw = _dni_store(members=[_dni_member(9)], rows=[row])
    schematic = Schematic(name="demo", components=[component])

    variants = map_orcad_cis_not_fitted_variants(raw, schematic)

    footprint_overrides = [o for o in variants[0].overrides if o.field == VariantField.FOOTPRINTS]
    assert len(footprint_overrides) == 1
    override = footprint_overrides[0]
    assert override.native_kind == "orcad_cis_update_storage_row"
    assert override.value == (FootprintModel(name="RES_0603", is_current=True),)
    assert override.base_value == (FootprintModel(name="RES_0402", is_current=True),)
    # "UNDEFINED" Part Number is a sentinel and never becomes an override.
    assert not any(o.field == VariantField.PART_NUMBERS for o in variants[0].overrides)
