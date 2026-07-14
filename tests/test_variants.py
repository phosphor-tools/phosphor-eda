from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import phosphor_eda.cli_project as cli_project_module
from phosphor_eda.cli import main
from phosphor_eda.domain.project import Project
from phosphor_eda.domain.schematic import Component, DnpSource, Parameter, PartNumber, Schematic
from phosphor_eda.domain.variant_materializer import materialize_project_variant
from phosphor_eda.domain.variants import (
    Variant,
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)
from phosphor_eda.query.project_loader import load_project

FIXTURES = Path(__file__).parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
PI_MX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release/PiMX8MP_r0.3_release.PrjPcb"
)


def _project_with_component(component: Component, *, variants: list[Variant]) -> Project:
    return Project(
        name="demo",
        schematic=Schematic(name="demo", components=[component]),
        variants=variants,
    )


def test_project_active_variant_returns_selected_variant():
    project = Project(
        name="demo",
        selected_variant_name="production",
        variants=[Variant(name="base"), Variant(name="production")],
    )

    assert project.active_variant is project.variants[1]


def test_materialize_not_fitted_component_sets_active_variant_dnp():
    component = Component(id="component:R1", reference="R1", part="10k", description="")
    variant = Variant(
        name="no-r1",
        overrides=[
            VariantOverride(
                variant_name="no-r1",
                target=VariantTarget(kind=VariantTargetKind.COMPONENT, object_id="component:R1"),
                field=VariantField.FITTED,
                value=False,
            )
        ],
    )
    project = _project_with_component(component, variants=[variant])

    materialize_project_variant(project, variant_name="no-r1")

    assert project.active_variant is variant
    assert component.dnp is True
    assert component.dnp_source is DnpSource.ACTIVE_VARIANT
    assert component.variant_overrides[0].applied is True
    assert component.variant_overrides[0].base_value is True


def test_materialize_parameter_override_updates_part_fields():
    component = Component(
        id="component:U1",
        reference="U1",
        part="MCU",
        description="",
        parameters=[Parameter(name="Manufacturer Part Number", value="OLD")],
    )
    variant = Variant(
        name="prod",
        overrides=[
            VariantOverride(
                variant_name="prod",
                target=VariantTarget(
                    kind=VariantTargetKind.COMPONENT,
                    reference="U1",
                    parameter_name="Manufacturer Part Number",
                ),
                field=VariantField.PARAMETER,
                value=Parameter(name="Manufacturer Part Number", value="NEW"),
            )
        ],
    )
    project = _project_with_component(component, variants=[variant])

    materialize_project_variant(project, variant_name="prod")

    assert [param.value for param in component.parameters] == ["NEW"]
    assert [part.number for part in component.part_numbers] == ["NEW"]


def test_materialize_part_number_override_updates_component_part_numbers():
    component = Component(
        id="component:U1",
        reference="U1",
        part="MCU",
        description="",
        part_numbers=[PartNumber(manufacturer="Acme", number="OLD")],
    )
    variant = Variant(
        name="alternate",
        overrides=[
            VariantOverride(
                variant_name="alternate",
                target=VariantTarget(kind=VariantTargetKind.COMPONENT, object_id="component:U1"),
                field=VariantField.PART_NUMBERS,
                value=(PartNumber(manufacturer="Acme", number="NEW"),),
            )
        ],
    )
    project = _project_with_component(component, variants=[variant])

    materialize_project_variant(project, variant_name="alternate")

    assert component.part_numbers == [PartNumber(manufacturer="Acme", number="NEW")]


def test_selected_unresolved_component_override_errors():
    variant = Variant(
        name="missing",
        overrides=[
            VariantOverride(
                variant_name="missing",
                target=VariantTarget(kind=VariantTargetKind.COMPONENT, reference="NOPE"),
                field=VariantField.DNP,
                value=True,
            )
        ],
    )
    project = _project_with_component(
        Component(id="component:R1", reference="R1", part="10k", description=""),
        variants=[variant],
    )

    with pytest.raises(ValueError, match="could not resolve selected variant override"):
        materialize_project_variant(project, variant_name="missing")


def test_cli_variant_option_is_passed_to_project_loader(monkeypatch, tmp_path):
    project_path = tmp_path / "demo.kicad_pro"
    project_path.write_text("{}", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_load_project(_path: object, **kwargs: object) -> Project:
        calls.append(kwargs)
        return Project(name="demo", variants=[Variant(name="production")])

    monkeypatch.setattr(cli_project_module, "load_project", fake_load_project)

    result = CliRunner().invoke(
        main,
        ["-P", str(project_path), "--variant", "production", "list", "variants"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"variant_name": "production", "base_variant": False}]
    assert "production" in result.output


def test_cli_rejects_variant_and_base_variant_together(tmp_path):
    project_path = tmp_path / "demo.kicad_pro"
    project_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["-P", str(project_path), "--variant", "production", "--base-variant", "overview"],
    )

    assert result.exit_code == 1
    assert "--variant and --base-variant are mutually exclusive" in result.output


def test_altium_project_variants_use_current_variant_by_default():
    project = load_project(PI_MX8_PRJPCB)

    assert project.selected_variant_name == "TPU"
    assert [variant.name for variant in project.variants] == ["Default_Config_0", "TPU"]
    assert project.active_variant is project.variants[1]
    assert project.documents[1].unique_id
    assert project.schematic is not None
    assert sum(1 for component in project.schematic.components if component.dnp) == 56


def test_altium_base_variant_preserves_variant_definitions_without_applying():
    project = load_project(PI_MX8_PRJPCB, base_variant=True)

    assert project.selected_variant_name == ""
    assert project.active_variant is None
    assert [variant.name for variant in project.variants] == ["Default_Config_0", "TPU"]
    assert project.schematic is not None
    assert sum(1 for component in project.schematic.components if component.dnp) == 0


def test_kicad_native_variant_applies_dnp_and_exclude_from_sim(tmp_path: Path):
    project_path = tmp_path / "variant-demo.kicad_pro"
    schematic_path = tmp_path / "variant-demo.kicad_sch"
    project_path.write_text(
        """
{
  "schematic": {
    "variants": [
      {"name": "Variant 1", "description": "DNP R1"},
      {"name": "Simulation", "description": "Exclude R1 from sim"}
    ]
  }
}
""",
        encoding="utf-8",
    )
    schematic_path.write_text(
        """
(kicad_sch (version 20251028) (generator eeschema)
  (uuid 11111111-1111-1111-1111-111111111111)
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "10k" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 2.54)
          (name "A" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 10 0) (unit 1)
    (in_bom yes) (on_board yes) (dnp no) (exclude_from_sim no)
    (uuid 22222222-2222-2222-2222-222222222222)
    (property "Reference" "R1" (at 10 8 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 10 12 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 33333333-3333-3333-3333-333333333333))
    (instances
      (project "variant-demo"
        (path "/11111111-1111-1111-1111-111111111111"
          (reference "R1")
          (unit 1)
          (variant
            (name "Variant 1")
            (dnp yes)
          )
          (variant
            (name "Simulation")
            (exclude_from_sim yes)
          )
        )
      )
    )
  )
)
""",
        encoding="utf-8",
    )

    dnp_project = load_project(project_path, variant_name="Variant 1")
    assert [variant.name for variant in dnp_project.variants] == ["Variant 1", "Simulation"]
    assert dnp_project.schematic is not None
    [dnp_component] = dnp_project.schematic.components
    assert dnp_component.reference == "R1"
    assert dnp_component.dnp is True
    assert dnp_component.dnp_source is DnpSource.ACTIVE_VARIANT

    sim_project = load_project(project_path, variant_name="Simulation")
    assert sim_project.schematic is not None
    [sim_component] = sim_project.schematic.components
    assert sim_component.exclude_from_simulation is True
