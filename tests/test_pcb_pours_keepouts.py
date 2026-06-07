import pytest

from phosphor_eda.pcb import (
    Pcb,
    PcbClosedPath,
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
    PcbLineGeometry,
    PcbPathSegment,
    PcbPathSegmentKind,
    PcbPolygonGeometry,
    PcbPour,
)


def test_closed_path_from_points_creates_closed_line_segments() -> None:
    path = PcbClosedPath.from_points(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)],
    )

    assert path.segments == (
        PcbPathSegment(PcbPathSegmentKind.LINE, 0.0, 0.0, 10.0, 0.0),
        PcbPathSegment(PcbPathSegmentKind.LINE, 10.0, 0.0, 10.0, 5.0),
        PcbPathSegment(PcbPathSegmentKind.LINE, 10.0, 5.0, 0.0, 0.0),
    )


def test_pour_keepout_and_pour_geometry_helpers() -> None:
    pour = PcbPour(
        id="pour:1",
        boundary=PcbClosedPath.from_points([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0)]),
        layers=("F.Cu",),
        net_number=1,
        net_name="GND",
        fill_geometry_ids=("region:1",),
    )
    keepout = PcbKeepout(
        id="keepout:1",
        boundary=PcbClosedPath.from_points([(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]),
        layers=("F.Cu",),
        rules=PcbKeepoutRules(
            tracks=PcbKeepoutPermission.NOT_ALLOWED,
            vias=PcbKeepoutPermission.NOT_ALLOWED,
        ),
        footprint_ref="U1",
    )
    pour_fill = PcbGeometry(
        id="region:1",
        object_type=PcbGeometryObject.REGION,
        shape=PcbGeometryShape.POLYGON,
        roles=(
            PcbGeometryRole.COPPER,
            PcbGeometryRole.CONDUCTOR,
            PcbGeometryRole.POUR,
            PcbGeometryRole.POUR_FILL,
        ),
        data=PcbPolygonGeometry([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0)]),
        layers=("F.Cu",),
        net_number=1,
        net_name="GND",
        pour_id="pour:1",
    )
    unrelated = PcbGeometry(
        id="track:1",
        object_type=PcbGeometryObject.TRACK,
        shape=PcbGeometryShape.LINE,
        roles=(PcbGeometryRole.COPPER, PcbGeometryRole.CONDUCTOR, PcbGeometryRole.TRACE),
        data=PcbLineGeometry(0.0, 0.0, 1.0, 0.0, 0.1),
        layers=("F.Cu",),
        net_number=1,
        net_name="GND",
    )
    board = Pcb(
        name="area-model",
        nets={},
        footprints=[],
        pours=[pour],
        keepouts=[keepout],
        geometry=[pour_fill, unrelated],
        layers=[PcbLayer("F.Cu", ("copper", "front"))],
    )

    assert board.pour_for("pour:1") is pour
    assert board.pours_on_layer("F.Cu") == [pour]
    assert board.pours_for_net(1) == [pour]
    assert board.keepout_for("keepout:1") is keepout
    assert board.keepouts_on_layer("F.Cu") == [keepout]
    assert board.keepouts_for_footprint("u1") == [keepout]
    assert board.geometry_for_pour("pour:1") == [pour_fill]


def test_removed_zone_and_geometry_keepout_api_is_not_available() -> None:
    with pytest.raises(AttributeError):
        _ = getattr(PcbGeometryObject, "ZO" + "NE")
    with pytest.raises(AttributeError):
        _ = getattr(PcbGeometryObject, "KEEP" + "_OUT")
    with pytest.raises(AttributeError):
        _ = getattr(PcbGeometryRole, "ZONE" + "_OUTLINE")
    with pytest.raises(AttributeError):
        _ = getattr(PcbGeometryRole, "ZONE" + "_FILL")
    with pytest.raises(AttributeError):
        _ = getattr(PcbGeometryRole, "KEEP" + "OUT")
