import pytest

from phosphor_eda.domain.pcb import LayerRole, Pcb, PcbLayer, normalize_roles


def test_normalize_roles_preserves_every_role() -> None:
    # The canonical order is derived from the enum, so normalizing the full
    # set returns every member — no role is silently dropped.
    assert set(normalize_roles(*LayerRole)) == set(LayerRole)


def test_normalize_roles_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="not-a-real-role"):
        normalize_roles("not-a-real-role")


def test_normalize_roles_removes_duplicates_and_uses_canonical_order() -> None:
    assert normalize_roles(
        LayerRole.FRONT,
        "copper",
        "signal",
        "front",
    ) == (LayerRole.COPPER, LayerRole.FRONT, LayerRole.SIGNAL)


def test_empty_roles_normalize_to_unknown() -> None:
    assert normalize_roles() == (LayerRole.UNKNOWN,)


def test_layer_side_is_derived_from_roles() -> None:
    assert PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)).side == "front"
    assert PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK)).side == "back"
    assert PcbLayer("In1.Cu", (LayerRole.COPPER, LayerRole.INNER)).side == "inner"
    assert PcbLayer("Edge.Cuts", (LayerRole.EDGE,)).side == ""


def test_layers_do_not_expose_primary_role() -> None:
    assert not hasattr(
        PcbLayer(
            "F.CrtYd",
            (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.FRONT),
        ),
        "primary_role",
    )


def test_pcb_role_helpers_match_multi_role_layers() -> None:
    board = Pcb(
        name="roles",
        layers=[
            PcbLayer("F.Fab", (LayerRole.FABRICATION, LayerRole.FRONT)),
            PcbLayer(
                "F.CrtYd",
                (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.FRONT),
            ),
            PcbLayer("In1.Cu", (LayerRole.COPPER, LayerRole.INNER, LayerRole.SIGNAL)),
        ],
        nets={},
        footprints=[],
        pads=[],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
    )

    assert {layer.name for layer in board.layers_by_role("fabrication")} == {
        "F.Fab",
        "F.CrtYd",
    }
    assert [layer.name for layer in board.layers_with_all_roles(["copper", "inner"])] == ["In1.Cu"]
    assert {layer.name for layer in board.layers_with_any_role(["courtyard", "copper"])} == {
        "F.CrtYd",
        "In1.Cu",
    }
