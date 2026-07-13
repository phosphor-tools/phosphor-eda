"""Parse native KiCad schematic variant overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.common.sexp as sexp
from phosphor_eda.domain.variants import (
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)

if TYPE_CHECKING:
    from pathlib import Path


def parse_kicad_schematic_variant_overrides(path: Path) -> list[VariantOverride]:
    """Parse native KiCad 10 per-instance variant overrides from a schematic."""
    if not path.exists():
        return []
    data: sexp.SExpNode = sexpdata.loads(path.read_text(encoding="utf-8"))
    overrides: list[VariantOverride] = []
    for symbol in sexp.find_all(data[1:], "symbol"):
        instances = sexp.find(symbol[1:], "instances")
        if instances is None:
            continue
        for project_node in sexp.find_all(instances[1:], "project"):
            for path_node in sexp.find_all(project_node[1:], "path"):
                reference = sexp.find_str(path_node[1:], "reference")
                page = sexp.find_str(path_node[1:], "page")
                variant_nodes = sexp.find_all(path_node[1:], "variant")
                for variant_node in variant_nodes:
                    variant_name = sexp.find_str(variant_node[1:], "name")
                    target = _target(reference, page)
                    overrides.extend(_variant_node_overrides(variant_name, target, variant_node))
    return overrides


def _target(reference: str, page: str) -> VariantTarget:
    if reference:
        return VariantTarget(kind=VariantTargetKind.COMPONENT, reference=reference)
    if page:
        return VariantTarget(kind=VariantTargetKind.PAGE, object_id=page)
    return VariantTarget(kind=VariantTargetKind.OTHER)


def _variant_node_overrides(
    variant_name: str,
    target: VariantTarget,
    variant_node: sexp.SExpNode,
) -> list[VariantOverride]:
    overrides: list[VariantOverride] = []
    for tag, field in (
        ("dnp", VariantField.DNP),
        ("exclude_from_bom", VariantField.EXCLUDE_FROM_BOM),
        ("exclude_from_sim", VariantField.EXCLUDE_FROM_SIMULATION),
    ):
        value = _bool_attr(variant_node, tag)
        if value is None:
            continue
        overrides.append(
            VariantOverride(
                variant_name=variant_name,
                target=target,
                field=field,
                value=value,
                native_kind=f"kicad_{tag}",
                metadata={"kicad_variant_field": tag},
            )
        )
    return overrides


def _bool_attr(node: sexp.SExpNode, tag: str) -> bool | None:
    child = sexp.find(node[1:], tag)
    if child is None or len(child) < 2:
        return None
    return sexp.val(child) == "yes"
