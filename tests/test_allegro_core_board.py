from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import phosphor_eda.formats.allegro.build as allegro_build
from phosphor_eda.domain.pcb import LayerRole, PcbArtworkPurpose, PcbPadType
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.oracle import parse_packaged_netlist_summary
from phosphor_eda.formats.allegro.parser import parse_allegro_pcb, parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet
from phosphor_eda.render.inventory import build_inventory
from phosphor_eda.render.modes import build_realistic_layers
from phosphor_eda.render.settings import (
    CliOverrides,
    load_render_settings_json,
    resolve_effective_settings,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
BREAKOUT_NETLIST = UPSTREAM_FIXTURES / "opencellular/electronics/breakout" / "schematic/Netlist"
SYNC_BOARD = (
    UPSTREAM_FIXTURES / "opencellular/electronics/sync" / "board" / "Fb_Connect1_SYNC_Life-3.brd"
)
ROHM_BOARD = (
    UPSTREAM_FIXTURES
    / "rohm-stepper-driver"
    / "Design Files for Rev 1.0"
    / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd"
)
LAUNCHXL_BOARD = (
    UPSTREAM_FIXTURES
    / "cp-smartgarden"
    / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
    / "LAUNCHXL-CC1310.brd"
)


def _assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, abs_tol=1e-6)


def test_allegro_launchxl_board_profile_from_single_shape_outline() -> None:
    """LAUNCHXL's board outline is a single 0x28 shape on BOARD GEOMETRY (0x01/0xFD).

    Without a 0x28 branch in graphics extraction the outline was dropped and the
    build failed with "board profile is required". The shape must now assemble
    into a board profile with a sane bounding box.
    """
    board = parse_allegro_pcb(LAUNCHXL_BOARD)

    assert board.board_profile is not None
    assert board.board_profile.elements
    bbox = board.bbox()
    assert bbox is not None
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y
    # Outline measures ~58.5 x 95.3 mm; lock a generous board-sized envelope.
    assert 40.0 < width < 120.0
    assert 40.0 < height < 120.0


def test_allegro_shape_symbol_pads_resolve_placed_flash_geometry() -> None:
    """Placed pads on a shape-symbol padstack render their true flash copper.

    Sync carries 58 pads whose selected copper component references a 0x28 shape
    record. Before resolution these degraded to a plain bounding rect; now each
    placed pad carries a custom flash polygon positioned on the pad center.
    """
    board = parse_allegro_pcb(SYNC_BOARD)

    custom_pads = [pad for pad in board.pads if pad.shape == "custom"]
    assert custom_pads, "expected sync to place shape-symbol pads"
    assert all(pad.custom_shapes for pad in custom_pads), (
        "every custom pad must carry resolved flash geometry, not a rect fallback"
    )

    sample = next(pad for pad in custom_pads if len(pad.custom_shapes[0].points) > 4)
    polygon = sample.custom_shapes[0]
    xs = [x for x, _ in polygon.points]
    ys = [y for _, y in polygon.points]
    # The placed flash straddles the pad center (positioned, not left pad-local).
    assert min(xs) <= sample.x <= max(xs)
    assert min(ys) <= sample.y <= max(ys)


def test_allegro_board_assembly_emits_connectivity_padstacks_and_drills() -> None:
    """Proves native Allegro records assemble into strict board-domain objects.

    Packaged netlist sidecars prove component, net, and pin counts. They cannot
    prove physical padstack geometry, routed copper, board profile, or pours.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    oracle = parse_packaged_netlist_summary(BREAKOUT_NETLIST)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert len(board.footprints) == oracle.unique_refdes_count
    assert (
        len({footprint.reference for footprint in board.footprints}) == oracle.unique_refdes_count
    )
    assert (
        len({footprint.reference for footprint in board.footprints} & oracle.component_refs) >= 40
    )
    assert len(board.nets) >= oracle.net_count - 1
    assert 0 not in board.nets

    footprint_pads = [pad for pad in board.pads if pad.footprint is not None]
    connected_footprint_pads = [pad for pad in footprint_pads if pad.net is not None]
    assert len(connected_footprint_pads) >= oracle.node_count - oracle.no_connect_node_count
    assert footprint_pads[0].number == "1"
    assert all(pad.drill is None or pad.drill.owner is pad for pad in board.pads)
    assert all(pad.footprint in board.footprints for pad in footprint_pads)

    assert board.vias
    assert board.drills
    assert all(via.drill.owner is via for via in board.vias)
    assert all(drill.layers for drill in board.drills)


def test_allegro_vias_surface_rotation_metadata_and_stay_through() -> None:
    from phosphor_eda.domain.pcb import PcbViaType

    record_set = parse_allegro_records(SYNC_BOARD.read_bytes(), source_name=SYNC_BOARD.name)

    board = build_allegro_board(record_set, name=SYNC_BOARD.stem)

    assert board.vias
    # No span field exists in the corpus, so every via is THROUGH.
    assert all(via.via_type is PcbViaType.THROUGH for via in board.vias)
    # Rotation is surfaced as via metadata; SYNC has rotated vias.
    rotations = {
        via.metadata.properties["via_rotation_deg"]
        for via in board.vias
        if "via_rotation_deg" in via.metadata.properties
    }
    assert rotations
    assert rotations & {"90", "180", "270"}


def test_allegro_board_assembly_places_padstack_objects_in_profile_coordinate_space() -> None:
    """Proves pads, vias, drills, and placements share the profile coordinate frame."""
    record_set = parse_allegro_records(SYNC_BOARD.read_bytes(), source_name=SYNC_BOARD.name)

    board = build_allegro_board(record_set, name=SYNC_BOARD.stem)

    bbox = board.bbox()
    assert bbox is not None
    min_x, min_y, max_x, max_y = bbox
    assert min_y < 0 <= max_y
    assert board.footprints
    assert board.pads
    assert board.vias
    assert board.drills
    for x, y in (
        *((footprint.x, footprint.y) for footprint in board.footprints),
        *((pad.x, pad.y) for pad in board.pads),
        *((via.x, via.y) for via in board.vias),
        *((drill.x, drill.y) for drill in board.drills),
    ):
        assert min_x <= x <= max_x
        assert min_y <= y <= max_y


def test_allegro_board_assembly_centers_placed_pads_from_native_extent() -> None:
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)

    board = build_allegro_board(record_set, name=ROHM_BOARD.stem)

    pad = next(pad for pad in board.pads if pad.id == "pad-109476824")
    assert pad.number == "1"
    assert pad.footprint is not None
    assert pad.footprint.reference == "TP29"
    _assert_close(pad.x, 102.87)
    _assert_close(pad.y, -38.735)
    _assert_close(pad.x, pad.footprint.x)
    _assert_close(pad.y, pad.footprint.y)


def test_allegro_board_assembly_centers_pad_drills_from_native_extent() -> None:
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)

    board = build_allegro_board(record_set, name=ROHM_BOARD.stem)

    pad = next(pad for pad in board.pads if pad.id == "pad-109494784")
    assert pad.number == "1"
    assert pad.footprint is not None
    assert pad.footprint.reference == "POT1"
    assert pad.drill is not None
    _assert_close(pad.x, 45.72)
    _assert_close(pad.y, -62.23)
    _assert_close(pad.drill.x, 45.72)
    _assert_close(pad.drill.y, -62.23)


def test_allegro_board_assembly_inherits_pad_rotation_from_footprint_instance() -> None:
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)

    board = build_allegro_board(record_set, name=ROHM_BOARD.stem)

    rotated_pad = next(pad for pad in board.pads if pad.id == "pad-109479776")
    assert rotated_pad.footprint is not None
    assert rotated_pad.footprint.reference == "S1"
    _assert_close(rotated_pad.footprint.rotation, 90.0)
    _assert_close(rotated_pad.rotation, 90.0)

    unrotated_pad = next(pad for pad in board.pads if pad.id == "pad-109494784")
    assert unrotated_pad.footprint is not None
    _assert_close(unrotated_pad.footprint.rotation, 0.0)
    _assert_close(unrotated_pad.rotation, 0.0)


def test_allegro_refdes_detection_preserves_lowercase_source_identifiers() -> None:
    assert allegro_build._looks_like_refdes("r1")
    assert allegro_build._looks_like_refdes(" R1 ")


def test_allegro_board_assembly_classifies_device_type_text_as_fabrication() -> None:
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)

    board = build_allegro_board(record_set, name=ROHM_BOARD.stem)

    device_type_artwork = [
        artwork
        for artwork in board.artwork
        if artwork.metadata.properties.get("native_class_id") == "3"
        and artwork.metadata.properties.get("native_subclass_id") == "251"
    ]
    assert device_type_artwork
    assert {artwork.purpose for artwork in device_type_artwork} == {PcbArtworkPurpose.FABRICATION}
    assert all(
        artwork.layer is not None and not artwork.layer.has_role(LayerRole.SILKSCREEN)
        for artwork in device_type_artwork
    )


def test_allegro_realistic_projection_excludes_part_numbers_from_silkscreen() -> None:
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)
    board = build_allegro_board(record_set, name=ROHM_BOARD.stem)
    base = load_render_settings_json('{"extends": "phosphor:realistic"}')
    settings = resolve_effective_settings(base, CliOverrides(side="front"))

    layers = build_realistic_layers(build_inventory(board, side="front"), settings)

    realistic_silkscreen = next(layer for layer in layers if layer.role.function == "silkscreen")
    text_content = {
        primitive.text.content
        for primitive in realistic_silkscreen.primitives
        if primitive.text is not None
    }
    assert "RES_0_1/10W_0603PKG_RESC2612X65" not in text_content
    assert "BD8377FV-M_2_SOP65P640X125-20N_" not in text_content
    assert "ROHM STEPPER MOTOR" in text_content
    assert "CONTINUOUS MODE" in text_content
    assert "R42" in text_content


def test_allegro_board_assembly_reports_unresolved_via_padstack_reference() -> None:
    """Proves degraded native connectivity records surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    via_record = next(record for record in source.records if record.tag == 0x33)
    missing_padstack_key = 999_999_999
    payload = dict(via_record.payload)
    payload["padstack_key"] = missing_padstack_key
    mutated_via = replace(via_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_via if record is via_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert f"via-{via_record.key}" not in {via.id for via in board.vias}
    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "unresolved-via-padstack" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(via_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_padstack_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_unresolved_footprint_instance_chain() -> None:
    """Proves degraded package-instance chains preserve native diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    definition_record = next(
        record
        for record in source.records
        if record.tag == 0x2B and record.payload.get("first_instance_key") == 0
    )
    missing_instance_key = 999_999_998
    payload = dict(definition_record.payload)
    payload["first_instance_key"] = missing_instance_key
    mutated_definition = replace(definition_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_definition if record is definition_record else record
            for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) > base_count
    assert "unresolved-footprint-instance-chain" in board.metadata.properties[
        "parse_diagnostic_codes"
    ].split(";")
    assert str(definition_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(
        ";"
    )
    assert str(missing_instance_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_component_pad_chain_cycles() -> None:
    """Proves cyclic native pad ownership chains surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    component = next(
        record
        for record in source.records
        if record.tag == 0x07
        and isinstance(record.payload.get("first_pad_key"), int)
        and record.payload["first_pad_key"]
    )
    first_pad_key = component.payload["first_pad_key"]
    assert isinstance(first_pad_key, int)
    pad_record = source.by_key[first_pad_key]
    payload = dict(pad_record.payload)
    payload["next_in_component_key"] = first_pad_key
    mutated_pad = replace(pad_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_pad if record is pad_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "component-pad-chain-cycle" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(component.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(first_pad_key) in board.metadata.properties["parse_diagnostic_reference_keys"].split(
        ";"
    )


def test_allegro_board_assembly_terminates_ring_chains_without_diagnostics() -> None:
    """Proves owner-ring chain terminators no longer surface as parser noise.

    Segment, pad, void, net-assignment, and footprint-instance chains all ring
    back to their owning record. Those clean terminators previously produced
    ~18.5k ``segment-owner-mismatch``, one ``unresolved-component-pad`` per
    component, and one ``unresolved-footprint-instance-chain`` per footprint
    definition; all are genuine non-anomalies and must not appear in board
    diagnostics.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    codes = board.metadata.properties.get("parse_diagnostic_codes", "").split(";")
    assert "segment-owner-mismatch" not in codes
    assert "unresolved-component-pad" not in codes
    assert "unresolved-footprint-instance-chain" not in codes
    assert "invalid-shape-void-record" not in codes
    assert "invalid-net-assignment-record" not in codes


def test_allegro_board_assembly_includes_footprint_mechanical_pads() -> None:
    """Proves NPTH pads on the footprint-instance chain are placed as pads.

    Mechanical pads (mounting posts, tooling holes) hang off the footprint
    instance's own ``first_pad_key`` chain rather than the component's electrical
    pin chain. They are through-hole, carry no net, and must still be added to
    the board. The SW16 switch is a worked example: three SMD electrical pins
    plus two drilled mounting posts.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    switch = next(footprint for footprint in board.footprints if footprint.reference == "SW16")
    switch_pads = [pad for pad in board.pads if pad.footprint is switch]
    mounting_pads = [pad for pad in switch_pads if pad.net is None and pad.drill is not None]
    electrical_pads = [pad for pad in switch_pads if pad.net is not None]
    assert len(mounting_pads) == 2
    assert {pad.number for pad in electrical_pads} == {"1", "2", "3"}
    assert all(pad.pad_type is PcbPadType.THROUGH_HOLE for pad in mounting_pads)

    expected_mechanical_keys = _footprint_mechanical_pad_keys(record_set)
    placed_pad_ids = {pad.id for pad in board.pads}
    assert len(expected_mechanical_keys) == 16
    mechanical_ids = {f"pad-{key}" for key in expected_mechanical_keys}
    assert mechanical_ids <= placed_pad_ids
    mechanical_footprints = {
        pad.footprint.reference
        for pad in board.pads
        if pad.id in mechanical_ids and pad.footprint is not None
    }
    assert len(mechanical_footprints) == 7


def _walk_pad_keys(
    record_set: AllegroRecordSet, *, owner: AllegroRecord, head_key: object, next_field: str
) -> set[int]:
    """Ring-terminated walk of a 0x32 pad chain, returning visited pad keys."""
    by_key = record_set.by_key
    keys: set[int] = set()
    current = head_key or 0
    seen: set[int] = set()
    while isinstance(current, int) and current and current not in seen and current != owner.key:
        seen.add(current)
        pad = by_key.get(current)
        if pad is None or pad.tag != 0x32:
            break
        keys.add(current)
        current = pad.payload.get(next_field) or 0
    return keys


def _footprint_mechanical_pad_keys(record_set: AllegroRecordSet) -> set[int]:
    """Independent oracle: pad keys reachable only via footprint-instance chains.

    Walks the electrical component chains (``next_in_component_key``) and the
    footprint-instance chains (``next_in_footprint_key``), returning pad keys
    that appear on an instance chain but never on a component chain.
    """
    components = [record for record in record_set.records if record.tag == 0x07]
    electrical: set[int] = set()
    for component in components:
        electrical |= _walk_pad_keys(
            record_set,
            owner=component,
            head_key=component.payload.get("first_pad_key"),
            next_field="next_in_component_key",
        )

    component_instances = {
        component.payload.get("footprint_instance_key") for component in components
    }
    mechanical: set[int] = set()
    for instance in (record for record in record_set.records if record.tag == 0x2D):
        if instance.key not in component_instances:
            continue
        mechanical |= _walk_pad_keys(
            record_set,
            owner=instance,
            head_key=instance.payload.get("first_pad_key"),
            next_field="next_in_footprint_key",
        )
    return mechanical - electrical


def test_allegro_board_assembly_reports_missing_footprint_instance_once() -> None:
    """Proves missing footprint instances do not create duplicate diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    component = next(
        record
        for record in source.records
        if record.tag == 0x07 and isinstance(record.payload.get("footprint_instance_key"), int)
    )
    missing_instance_key = 999_999_997
    payload = dict(component.payload)
    payload["footprint_instance_key"] = missing_instance_key
    mutated_component = replace(component, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_component if record is component else record for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)
    codes = board.metadata.properties["parse_diagnostic_codes"].split(";")

    assert "unresolved-footprint-instance" in codes
    assert "unresolved-component-footprint" not in codes
    assert str(component.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_instance_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_unresolved_pad_definition() -> None:
    """Proves degraded pad definition references surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    component = next(
        record
        for record in source.records
        if record.tag == 0x07
        and isinstance(record.payload.get("first_pad_key"), int)
        and record.payload["first_pad_key"]
    )
    first_pad_key = component.payload["first_pad_key"]
    assert isinstance(first_pad_key, int)
    pad_record = source.by_key[first_pad_key]
    missing_definition_key = 999_999_996
    payload = dict(pad_record.payload)
    payload["pad_definition_key"] = missing_definition_key
    mutated_pad = replace(pad_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_pad if record is pad_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "unresolved-pad-definition" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(pad_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_definition_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_via_padstack_without_drill() -> None:
    """Proves unsupported via padstacks without drills surface diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    via_record = next(record for record in source.records if record.tag == 0x33)
    padstack_key = via_record.payload["padstack_key"]
    assert isinstance(padstack_key, int)
    padstack_record = source.by_key[padstack_key]
    payload = dict(padstack_record.payload)
    payload["drill_size"] = 0
    payload["slot_x"] = 0
    payload["slot_y"] = 0
    mutated_padstack = replace(padstack_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_padstack if record is padstack_record else record for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert f"via-{via_record.key}" not in {via.id for via in board.vias}
    assert int(board.metadata.properties["parse_diagnostic_count"]) > base_count
    assert "unsupported-via-without-drill" in board.metadata.properties[
        "parse_diagnostic_codes"
    ].split(";")
    assert str(via_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(padstack_key) in board.metadata.properties["parse_diagnostic_reference_keys"].split(
        ";"
    )


def test_allegro_board_assembly_reports_unknown_placement_side() -> None:
    """Proves an unrecognized placement side is surfaced, not silently fronted."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    component = next(record for record in source.records if record.tag == 0x07)
    instance_key = component.payload["footprint_instance_key"]
    assert isinstance(instance_key, int)
    instance_record = source.by_key[instance_key]
    assert instance_record.tag == 0x2D
    payload = dict(instance_record.payload)
    payload["placement_side"] = 2
    mutated_instance = replace(instance_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_instance if record is instance_record else record for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "unknown-placement-side" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(instance_key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
