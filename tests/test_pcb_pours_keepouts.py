import pytest

from phosphor_eda.domain.pcb import (
    Pcb,
    PcbClosedPath,
    PcbConductor,
    PcbConductorKind,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
    PcbPathSegment,
    PcbPathSegmentKind,
    PcbPolygon,
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


def test_pour_keepout_and_conductor_helpers() -> None:
    layer = PcbLayer("F.Cu", ("copper", "front"))
    pour = PcbPour(
        id="pour:1",
        boundary=PcbClosedPath.from_points([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0)]),
        layers=(layer,),
    )
    keepout = PcbKeepout(
        id="keepout:1",
        boundary=PcbClosedPath.from_points([(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]),
        layers=(layer,),
        rules=PcbKeepoutRules(
            tracks=PcbKeepoutPermission.NOT_ALLOWED,
            vias=PcbKeepoutPermission.NOT_ALLOWED,
        ),
        footprint=None,
    )
    pour_fill = PcbConductor(
        id="region:1",
        kind=PcbConductorKind.POUR_FILL,
        layer=layer,
        data=PcbPolygon([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0)]),
        pour=pour,
    )
    pour.fills = (pour_fill,)
    board = Pcb(
        name="area-model",
        layers=[layer],
        nets={},
        footprints=[],
        pads=[],
        vias=[],
        drills=[],
        conductors=[pour_fill],
        artwork=[],
        pours=[pour],
        keepouts=[keepout],
    )

    assert board.pour_for("pour:1") is pour
    assert board.pours_on_layer(layer) == [pour]
    assert board.keepout_for("keepout:1") is keepout
    assert board.keepouts_on_layer(layer) == [keepout]
    assert board.conductors_for_pour(pour) == [pour_fill]


def test_removed_zone_and_geometry_keepout_api_is_not_available() -> None:
    with pytest.raises(AttributeError):
        _ = PcbConductorKind.ROUTE
