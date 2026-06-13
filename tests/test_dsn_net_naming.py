"""OrCAD DSN net naming: stored names are read, never reconstructed.

Capture materializes every resolved net name into the DSN (page net
lists, the Hierarchy mapping) and autonames anonymous nets after their
seed wire's dbid. These tests cover the selection policy on synthetic
sources, the repo fixtures, and the OpenCellular breakout board against
Cadence's own pstxnet netlist (see tests/fixtures/dsn/opencellular-breakout).
"""

from pathlib import Path

from dsn_oracle_helpers import compare_net_names

from phosphor_eda.domain.schematic import Net, NetNameKind, ScopeId
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.raw_models import (
    GraphicInst,
    ParsedDesign,
    SchematicPage,
    Wire,
)
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.resolver import resolve_dsn_source
from phosphor_eda.formats.dsn.source import (
    DsnGlobal,
    DsnHierarchyMapping,
    DsnPageNet,
    DsnPageSource,
    DsnPinOccurrence,
    DsnSourceDesign,
    DsnWire,
    DsnWireAlias,
    dsn_name_key,
)
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PICO_DSN = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PICO_W_DSN = FIXTURES / "dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN"
CMIO_DSN = FIXTURES / "dsn/raspberry-pi-cmio/RPI-CMIO-V3_0-PUBLIC.DSN"
BREAKOUT_DIR = FIXTURES / "dsn/opencellular-breakout"

# --- synthetic-source helpers ---


def _scope(*parts: str) -> ScopeId:
    return ScopeId(path=parts)


def _page(
    name: str,
    scope_id: ScopeId,
    nets: list[DsnPageNet],
    *,
    pins: list[DsnPinOccurrence] | None = None,
    wires: list[DsnWire] | None = None,
    globals_: list[DsnGlobal] | None = None,
) -> DsnPageSource:
    return DsnPageSource(
        id=f"page:{name}",
        name=name,
        scope_id=scope_id,
        nets=nets,
        wires=wires or [],
        pin_occurrences=pins or [],
        ports=[],
        globals=globals_ or [],
        off_page_connectors=[],
    )


def _net(page_name: str, scope_id: ScopeId, net_id: int, name: str) -> DsnPageNet:
    return DsnPageNet(
        id=f"page:{page_name}:net:{net_id}",
        scope_id=scope_id,
        net_id=net_id,
        name=name,
        name_key=dsn_name_key(name),
    )


def _wire(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    db_id: int,
    *,
    alias: str = "",
) -> DsnWire:
    local_net_id = f"page:{page_name}:net:{net_id}"
    aliases = (
        [
            DsnWireAlias(
                id=f"{local_net_id}:alias:{alias}",
                scope_id=scope_id,
                name=alias,
                name_key=dsn_name_key(alias),
                location=(0, 0),
            )
        ]
        if alias
        else []
    )
    return DsnWire(
        id=f"page:{page_name}:wire:{db_id}",
        scope_id=scope_id,
        local_net_id=local_net_id,
        source_net_id=net_id,
        start=(0, 0),
        end=(1, 1),
        points=[],
        aliases=aliases,
        db_id=db_id,
    )


def _global(page_name: str, scope_id: ScopeId, net_id: int, name: str) -> DsnGlobal:
    return DsnGlobal(
        id=f"page:{page_name}:global:{net_id}:{name}",
        scope_id=scope_id,
        local_net_id=f"page:{page_name}:net:{net_id}",
        source_net_id=net_id,
        name=name,
        name_key=dsn_name_key(name),
        location=(net_id, net_id),
    )


def _pin(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    reference: str,
    *,
    component_source_id: str | None = None,
    designator: str = "1",
) -> DsnPinOccurrence:
    local_net_id = f"page:{page_name}:net:{net_id}"
    return DsnPinOccurrence(
        id=f"{local_net_id}:pin:{reference}:{designator}",
        scope_id=scope_id,
        local_net_id=local_net_id,
        source_net_id=net_id,
        component_source_id=component_source_id or f"page:{page_name}:component:{reference}",
        component_reference=reference,
        component_part="Part",
        pin_designator=designator,
        pin_name="",
        location=(net_id, net_id),
    )


def _mapping(db_id: int, name: str) -> DsnHierarchyMapping:
    return DsnHierarchyMapping(
        id=f"hierarchy:net:{db_id}",
        db_id=db_id,
        name=name,
        name_key=dsn_name_key(name),
    )


def _source(
    pages: list[DsnPageSource],
    mappings: list[DsnHierarchyMapping] | None = None,
) -> DsnSourceDesign:
    return DsnSourceDesign(name="Board", pages=pages, hierarchy_mappings=mappings or [])


def _net_named(nets: list[Net], name: str) -> Net:
    for net in nets:
        if net.name == name:
            return net
    raise AssertionError(f"no net named {name!r}; have {[net.name for net in nets]}")


# --- selection policy on synthetic sources ---


def test_stored_page_net_name_wins_over_label_evidence() -> None:
    scope = _scope("Main")
    net = _net("Main", scope, 1, "CLK_24M")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "Main",
                    scope,
                    [net],
                    wires=[_wire("Main", scope, 1, 500, alias="OSC_OUT")],
                    globals_=[_global("Main", scope, 1, "3V3")],
                )
            ]
        )
    )

    resolved = _net_named(design.nets, "CLK_24M")
    assert resolved.aliases == {"OSC_OUT", "3V3"}
    canonical = next(entry for entry in resolved.names if entry.name == "CLK_24M")
    assert canonical.kind is NetNameKind.LABEL
    assert canonical.source == "page_net"


def test_anonymous_cluster_adopts_seed_wire_autoname_confirmed_by_mapping() -> None:
    scope = _scope("Main")
    net = _net("Main", scope, 7, "")
    wires = [_wire("Main", scope, 7, 612), _wire("Main", scope, 7, 540)]

    design = resolve_dsn_source(
        _source(
            [_page("Main", scope, [net], wires=wires)],
            mappings=[_mapping(40, "N00540")],
        )
    )

    resolved = _net_named(design.nets, "N00540")
    assert resolved.metadata["dsn_seed_wire_dbid"] == "540"
    assert {(entry.kind, entry.source) for entry in resolved.names} == {
        (NetNameKind.TOOL_AUTO, "seed_wire_dbid"),
        (NetNameKind.TOOL_AUTO, "hierarchy_mapping"),
    }


def test_anonymous_cluster_derives_autoname_when_no_mapping_exists() -> None:
    scope = _scope("Main")
    net = _net("Main", scope, 7, "")

    design = resolve_dsn_source(
        _source([_page("Main", scope, [net], wires=[_wire("Main", scope, 7, 99)])])
    )

    resolved = _net_named(design.nets, "N00099")
    assert resolved.names[0].kind is NetNameKind.TOOL_AUTO
    assert resolved.names[0].source == "seed_wire_dbid"


def test_unconfirmed_derivation_adopts_leftover_mapping_autoname_by_elimination() -> None:
    # The seed wire was deleted: the surviving wires derive N00099, which
    # the mapping does not confirm; the one leftover mapping autoname is
    # the stored name.
    scope = _scope("Main")
    named = _net("Main", scope, 1, "SIG")
    anonymous = _net("Main", scope, 7, "")

    design = resolve_dsn_source(
        _source(
            [_page("Main", scope, [named, anonymous], wires=[_wire("Main", scope, 7, 99)])],
            mappings=[_mapping(1, "SIG"), _mapping(2, "N00540")],
        )
    )

    resolved = _net_named(design.nets, "N00540")
    assert len(resolved.names) == 1
    assert resolved.names[0].kind is NetNameKind.TOOL_AUTO
    assert resolved.names[0].source == "hierarchy_mapping"


def test_synthesis_only_when_no_stored_name_exists() -> None:
    scope = _scope("Main")
    named = _net("Main", scope, 1, "SIG")
    anonymous = _net("Main", scope, 77, "")
    ctx = ParseContext()

    design = resolve_dsn_source(
        _source(
            [_page("Main", scope, [named, anonymous], wires=[_wire("Main", scope, 77, 99)])],
            mappings=[_mapping(1, "SIG")],
        ),
        ctx=ctx,
    )

    resolved = _net_named(design.nets, "N00000077")
    assert resolved.names[0].kind is NetNameKind.SYNTHESIZED
    assert any(issue.category == "dsn_net_name_synthesized" for issue in ctx.issues)


def test_cross_page_stored_name_conflict_resolved_by_mapping() -> None:
    # Hierarchical block occurrences store one name per page; the
    # Hierarchy mapping holds the name Capture resolved to.
    scope_a = _scope("Parent")
    scope_b = _scope("Child")
    net_a = _net("Parent", scope_a, 1, "UNNAMED_101_NPN_I19_B")
    net_b = _net("Child", scope_b, 2, "ADT7481_D2_P")
    shared = "capture:component:Q1"
    pin_a = _pin("Parent", scope_a, 1, "Q1", component_source_id=shared)
    pin_b = _pin("Child", scope_b, 2, "Q1", component_source_id=shared)

    design = resolve_dsn_source(
        _source(
            [
                _page("Parent", scope_a, [net_a], pins=[pin_a]),
                _page("Child", scope_b, [net_b], pins=[pin_b]),
            ],
            mappings=[_mapping(1, "ADT7481_D2_P")],
        )
    )

    resolved = _net_named(design.nets, "ADT7481_D2_P")
    assert resolved.aliases == {"UNNAMED_101_NPN_I19_B"}


def test_stored_autoname_form_is_classified_tool_auto() -> None:
    scope = _scope("Main")
    auto_form = _net("Main", scope, 1, "N12345")
    short_n_name = _net("Main", scope, 2, "N1234")

    design = resolve_dsn_source(_source([_page("Main", scope, [auto_form, short_n_name])]))

    assert _net_named(design.nets, "N12345").names[0].kind is NetNameKind.TOOL_AUTO
    assert _net_named(design.nets, "N1234").names[0].kind is NetNameKind.LABEL


def test_alias_grade_autoname_form_is_classified_tool_auto() -> None:
    scope = _scope("Main")
    net = _net("Main", scope, 1, "")

    design = resolve_dsn_source(
        _source([_page("Main", scope, [net], wires=[_wire("Main", scope, 1, 500, alias="N12345")])])
    )

    resolved = _net_named(design.nets, "N12345")
    [name] = resolved.names
    assert name.kind is NetNameKind.TOOL_AUTO
    assert name.source == "wire_alias"


def test_power_symbol_contributes_net_name_not_symbol_name() -> None:
    # The graphic's own name is the symbol (VCC_ARROW); the net name rides
    # in the _net_name string index.
    page = SchematicPage(
        name="Main",
        wires=[Wire(db_id=300, wire_id=9, start_x=10, start_y=20, end_x=30, end_y=40)],
        globals=[
            GraphicInst(
                name="VCC_ARROW",
                db_id=2,
                loc_x=10,
                loc_y=20,
                props={"_net_name": "3V3"},
            )
        ],
        wire_net_map={(10, 20): {9}, (30, 40): {9}},
    )

    design = dsn_to_design(ParsedDesign(pages=[page]), name="Board")

    resolved = _net_named(design.nets, "3V3")
    assert "VCC_ARROW" not in resolved.aliases
    assert all(entry.name != "VCC_ARROW" for entry in resolved.names)


# --- fixture regressions ---

# Stored autonames read from each fixture's Hierarchy stream; the resolver
# must reproduce them from the seed-wire dbids.
PICO_AUTONAMES = {"N1248345", "N1248352", "N1283798", "N1286133", "N1327876"}
CMIO_AUTONAMES = {
    "N1195355",
    "N1195439",
    "N1202496",
    "N1202592",
    "N1202604",
    "N1202780",
    "N1203166",
    "N1203318",
    "N1313759",
    "N1313939",
    "N1329395",
    "N1357591",
}


def _fixture_design(path: Path) -> tuple[list[Net], ParseContext]:
    ctx = ParseContext()
    raw = parse_dsn(path, ctx)
    return dsn_to_design(raw, name=path.stem, ctx=ctx).nets, ctx


def test_pico_fixture_adopts_stored_autonames_without_synthesis() -> None:
    nets, ctx = _fixture_design(PICO_DSN)

    autonames = {net.name for net in nets if net.name.startswith("N") and net.name[1:].isdigit()}
    assert autonames == PICO_AUTONAMES
    assert all(entry.kind is not NetNameKind.SYNTHESIZED for net in nets for entry in net.names)
    assert not any(issue.category == "dsn_net_name_synthesized" for issue in ctx.issues)


def test_pico_fixture_autoname_evidence_is_tool_auto_and_mapping_confirmed() -> None:
    nets, _ctx = _fixture_design(PICO_DSN)

    net = _net_named(nets, "N1248345")
    assert {entry.source for entry in net.names} == {"seed_wire_dbid", "hierarchy_mapping"}
    assert all(entry.kind is NetNameKind.TOOL_AUTO for entry in net.names)

    gnd = _net_named(nets, "GND")
    assert any(
        entry.kind is NetNameKind.LABEL and entry.source == "page_net" for entry in gnd.names
    )


def test_cmio_fixture_adopts_stored_autonames_and_never_symbol_names() -> None:
    nets, _ctx = _fixture_design(CMIO_DSN)

    autonames = {net.name for net in nets if net.name.startswith("N") and net.name[1:].isdigit()}
    assert autonames == CMIO_AUTONAMES
    # Graphic symbol names must not surface as net names (the historical
    # ranking bug promoted these).
    names = {net.name for net in nets}
    assert not names & {"VCC_BAR", "VCC_ARROW", "OFFPAGELEFT-L", "OFFPAGELEFT-R"}


def test_picow_fixture_resolves_cross_page_conflicts_to_mapping_names() -> None:
    nets, _ctx = _fixture_design(PICO_W_DSN)

    # GPIO23/GPIO25 are page-local spellings; Capture resolved these nets
    # to WL_ON / WL_CS (the Hierarchy mapping names).
    wl_on = _net_named(nets, "WL_ON")
    assert "GPIO23" in wl_on.aliases
    wl_cs = _net_named(nets, "WL_CS")
    assert "GPIO25" in wl_cs.aliases


def test_breakout_fixture_matches_pstxnet_oracle_names() -> None:
    # OpenCellular breakout (CC-BY): Cadence's own packaged netlist is the
    # naming oracle. Every membership-matched net must carry the oracle's
    # name; autonames must be byte-exact.
    result = compare_net_names(
        BREAKOUT_DIR / "OC_CONNECT_1_BRKOUT_BRD.DSN",
        BREAKOUT_DIR / "pstxnet.dat",
    )

    assert result.mismatched == []
    assert len(result.matched) >= 19
    autonames = result.matched_autonames
    assert len(autonames) >= 19
    assert all(oracle == ours for oracle, ours in autonames)
