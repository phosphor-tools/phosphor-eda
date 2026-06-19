from __future__ import annotations

import pytest

from phosphor_eda.domain.schematic import Component, Parameter, Schematic
from phosphor_eda.formats.common.raw_models import (
    DsnCisBom,
    DsnCisGroup,
    DsnCisGroupMember,
    DsnCisStringList,
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
        metadata={"dsn_component_source_ids": "page:Main:component:100", "internal": "NI"},
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
