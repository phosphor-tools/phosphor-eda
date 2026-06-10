"""Parser-agnostic PCB domain builder and reference validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from phosphor_eda.pcb import (
    Pcb,
    PcbArtwork,
    PcbBoardProfile,
    PcbConductor,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbKeepout,
    PcbLayer,
    PcbMetadata,
    PcbNet,
    PcbPad,
    PcbPadType,
    PcbPour,
    PcbVia,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class PcbBuildError(ValueError):
    """Raised when parsed source data cannot form a strict PCB domain model."""


class PcbBuilder:
    """Build a :class:`Pcb` while validating concrete domain references."""

    def __init__(self, name: str, *, metadata: PcbMetadata | None = None) -> None:
        self.name = name
        self.metadata = metadata if metadata is not None else PcbMetadata()
        self.layers: list[PcbLayer] = []
        self.nets: dict[int, PcbNet] = {}
        self.footprints: list[PcbFootprint] = []
        self.pads: list[PcbPad] = []
        self.vias: list[PcbVia] = []
        self.drills: list[PcbDrill] = []
        self.conductors: list[PcbConductor] = []
        self.artwork: list[PcbArtwork] = []
        self.pours: list[PcbPour] = []
        self.keepouts: list[PcbKeepout] = []
        self.board_profile: PcbBoardProfile | None = None
        # Identity/name indexes so reference validation and name resolution are
        # O(1) instead of scanning the collections on every add.
        self._layer_ids: set[int] = set()
        self._footprint_ids: set[int] = set()
        self._drill_ids: set[int] = set()
        self._drill_id_strs: set[str] = set()
        self._layers_by_name: dict[str, PcbLayer] = {}

    def add_layer(self, layer: PcbLayer, *, source: str = "") -> PcbLayer:
        """Add a concrete source layer definition."""
        if self._is_layer_selector(layer.name):
            self._fail(f"Layer selector {layer.name!r} is not a concrete layer", source)
        self.layers.append(layer)
        self._layer_ids.add(id(layer))
        # First occurrence wins, matching the previous linear name scan.
        self._layers_by_name.setdefault(layer.name, layer)
        return layer

    def add_net(self, net: PcbNet, *, source: str = "") -> PcbNet:
        """Add a real electrical net. Net 0 is intentionally invalid."""
        if net.number == 0:
            self._fail("net 0 is forbidden; use net=None for unconnected objects", source)
        if net.number in self.nets:
            self._fail(f"duplicate net number {net.number}", source)
        self.nets[net.number] = net
        return net

    def add_footprint(self, footprint: PcbFootprint, *, source: str = "") -> PcbFootprint:
        """Add a footprint whose placement layer already belongs to the board."""
        self._validate_layer_ref(footprint.layer, source)
        self.footprints.append(footprint)
        self._footprint_ids.add(id(footprint))
        return footprint

    def resolve_layer(self, layer: PcbLayer | str, *, source: str = "") -> PcbLayer:
        """Resolve a concrete layer reference, rejecting source-format selectors."""
        if isinstance(layer, PcbLayer):
            self._validate_layer_ref(layer, source)
            return layer
        if self._is_layer_selector(layer):
            self._fail(f"Layer selector {layer!r} must be resolved by the parser", source)
        resolved = self._layers_by_name.get(layer)
        if resolved is not None:
            return resolved
        self._fail(f"unknown layer {layer!r}", source)

    def resolve_layers(
        self,
        layers: Iterable[PcbLayer | str],
        *,
        source: str = "",
    ) -> tuple[PcbLayer, ...]:
        """Resolve concrete layer references in source order."""
        return tuple(self.resolve_layer(layer, source=source) for layer in layers)

    def resolve_net_number(self, number: int | None, *, source: str = "") -> PcbNet | None:
        """Resolve a source net number. ``None`` is the unconnected-net model."""
        if number is None:
            return None
        if number == 0:
            self._fail("net 0 is forbidden; use net=None for unconnected objects", source)
        net = self.nets.get(number)
        if net is None:
            self._fail(f"unknown net number {number}", source)
        return net

    def resolve_net(self, net: PcbNet | int | None, *, source: str = "") -> PcbNet | None:
        """Resolve a concrete net reference."""
        if net is None:
            return None
        if isinstance(net, int):
            return self.resolve_net_number(net, source=source)
        self._validate_net_ref(net, source)
        return net

    def resolve_footprint(
        self,
        footprint: PcbFootprint | str | None,
        *,
        source: str = "",
        allow_none: bool = False,
    ) -> PcbFootprint | None:
        """Resolve a concrete footprint reference."""
        if footprint is None:
            if allow_none:
                return None
            self._fail("missing footprint reference", source)
        if isinstance(footprint, PcbFootprint):
            self._validate_footprint_ref(footprint, source)
            return footprint
        needle = footprint.upper()
        for candidate in self.footprints:
            if candidate.reference.upper() == needle:
                return candidate
        self._fail(f"unknown footprint {footprint!r}", source)

    def add_drill(
        self,
        *,
        id: str,
        x: float,
        y: float,
        diameter: float,
        shape: PcbDrillShape = PcbDrillShape.ROUND,
        plating: PcbDrillPlating = PcbDrillPlating.UNKNOWN,
        width: float = 0.0,
        height: float = 0.0,
        rotation: float = 0.0,
        layers: Iterable[PcbLayer | str] = (),
        source: str = "",
    ) -> PcbDrill:
        """Add a manufactured hole or slot."""
        drill = PcbDrill(
            id=id,
            x=x,
            y=y,
            diameter=diameter,
            shape=shape,
            plating=plating,
            width=width,
            height=height,
            rotation=rotation,
            layers=self.resolve_layers(layers, source=source),
        )
        return self.add_drill_object(drill, source=source)

    def add_drill_object(self, drill: PcbDrill, *, source: str = "") -> PcbDrill:
        """Add an already-constructed drill after validating references."""
        self._validate_layer_refs(drill.layers, source)
        if drill.id in self._drill_id_strs:
            self._fail(f"duplicate drill {drill.id!r}", source)
        self.drills.append(drill)
        self._drill_ids.add(id(drill))
        self._drill_id_strs.add(drill.id)
        return drill

    def resolve_drill(self, drill: PcbDrill | str | None, *, source: str = "") -> PcbDrill | None:
        """Resolve a concrete drill reference."""
        if drill is None:
            return None
        if isinstance(drill, PcbDrill):
            self._validate_drill_ref(drill, source)
            return drill
        for candidate in self.drills:
            if candidate.id == drill:
                return candidate
        self._fail(f"unknown drill {drill!r}", source)

    def add_pad(
        self,
        *,
        id: str,
        number: str,
        x: float,
        y: float,
        width: float,
        height: float,
        shape: str,
        pad_type: PcbPadType,
        layers: Iterable[PcbLayer | str],
        net: PcbNet | int | None = None,
        footprint: PcbFootprint | str | None = None,
        drill: PcbDrill | str | None = None,
        rotation: float = 0.0,
        source: str = "",
    ) -> PcbPad:
        """Add a pad using concrete object references."""
        pad = PcbPad(
            id=id,
            number=number,
            x=x,
            y=y,
            width=width,
            height=height,
            shape=shape,
            pad_type=pad_type,
            layers=self.resolve_layers(layers, source=source),
            net=self.resolve_net(net, source=source),
            footprint=self.resolve_footprint(footprint, source=source, allow_none=True),
            drill=self.resolve_drill(drill, source=source),
            rotation=rotation,
        )
        return self.add_pad_object(pad, source=source)

    def add_pad_object(self, pad: PcbPad, *, source: str = "") -> PcbPad:
        """Add an already-constructed pad after validating references."""
        self._validate_layer_refs(pad.layers, source)
        self._validate_optional_net_ref(pad.net, source)
        self._validate_optional_footprint_ref(pad.footprint, source)
        if pad.drill is not None:
            self._validate_drill_ref(pad.drill, source)
            pad.drill.owner = pad
        self.pads.append(pad)
        return pad

    def add_via_object(self, via: PcbVia, *, source: str = "") -> PcbVia:
        """Add an already-constructed via after validating references."""
        self._validate_layer_refs(via.layers, source)
        self._validate_optional_net_ref(via.net, source)
        self._validate_drill_ref(via.drill, source)
        via.drill.owner = via
        self.vias.append(via)
        return via

    def add_conductor_object(self, conductor: PcbConductor, *, source: str = "") -> PcbConductor:
        """Add an already-constructed conductor after validating references."""
        self._validate_layer_ref(conductor.layer, source)
        self._validate_optional_net_ref(conductor.net, source)
        self._validate_optional_footprint_ref(conductor.footprint, source)
        if conductor.pour is not None and conductor.pour not in self.pours:
            self._fail(f"unknown pour {conductor.pour.id!r}", source)
        self.conductors.append(conductor)
        return conductor

    def add_artwork_object(self, artwork: PcbArtwork, *, source: str = "") -> PcbArtwork:
        """Add an already-constructed artwork item after validating references."""
        if artwork.layer is not None:
            self._validate_layer_ref(artwork.layer, source)
        self._validate_optional_footprint_ref(artwork.footprint, source)
        self.artwork.append(artwork)
        return artwork

    def add_pour_object(self, pour: PcbPour, *, source: str = "") -> PcbPour:
        """Add an already-constructed pour source after validating references."""
        self._validate_layer_refs(pour.layers, source)
        self._validate_optional_net_ref(pour.net, source)
        self._validate_optional_footprint_ref(pour.footprint, source)
        self.pours.append(pour)
        return pour

    def add_keepout_object(self, keepout: PcbKeepout, *, source: str = "") -> PcbKeepout:
        """Add an already-constructed keepout after validating references."""
        self._validate_layer_refs(keepout.layers, source)
        self._validate_optional_footprint_ref(keepout.footprint, source)
        self.keepouts.append(keepout)
        return keepout

    def set_board_profile(
        self, board_profile: PcbBoardProfile, *, source: str = ""
    ) -> PcbBoardProfile:
        """Set the physical board profile."""
        for element in board_profile.elements:
            if element.layer is not None:
                self._validate_layer_ref(element.layer, source)
        self.board_profile = board_profile
        return board_profile

    def build(self, *, require_board_profile: bool = False) -> Pcb:
        """Return a validated strict PCB domain object."""
        if 0 in self.nets:
            self._fail("net 0 is forbidden; use net=None for unconnected objects")
        if require_board_profile and (
            self.board_profile is None or not self.board_profile.elements
        ):
            self._fail("board profile is required")
        # Pad/via drill references were already validated at add time; no need
        # to re-scan every object here.
        return Pcb(
            name=self.name,
            layers=list(self.layers),
            nets=dict(self.nets),
            footprints=list(self.footprints),
            pads=list(self.pads),
            vias=list(self.vias),
            drills=list(self.drills),
            conductors=list(self.conductors),
            artwork=list(self.artwork),
            pours=list(self.pours),
            keepouts=list(self.keepouts),
            board_profile=self.board_profile,
            metadata=self.metadata,
        )

    def _validate_layer_refs(self, layers: Iterable[PcbLayer], source: str) -> None:
        for layer in layers:
            self._validate_layer_ref(layer, source)

    def _validate_layer_ref(self, layer: PcbLayer, source: str) -> None:
        if id(layer) not in self._layer_ids:
            self._fail(f"unknown layer {layer.name!r}", source)

    def _validate_net_ref(self, net: PcbNet, source: str) -> None:
        if net.number == 0:
            self._fail("net 0 is forbidden; use net=None for unconnected objects", source)
        if self.nets.get(net.number) is not net:
            self._fail(f"unknown net number {net.number}", source)

    def _validate_optional_net_ref(self, net: PcbNet | None, source: str) -> None:
        if net is not None:
            self._validate_net_ref(net, source)

    def _validate_footprint_ref(self, footprint: PcbFootprint, source: str) -> None:
        if id(footprint) not in self._footprint_ids:
            self._fail(f"unknown footprint {footprint.reference!r}", source)

    def _validate_optional_footprint_ref(self, footprint: PcbFootprint | None, source: str) -> None:
        if footprint is not None:
            self._validate_footprint_ref(footprint, source)

    def _validate_drill_ref(self, drill: PcbDrill, source: str) -> None:
        if id(drill) not in self._drill_ids:
            self._fail(f"unknown drill {drill.id!r}", source)

    @staticmethod
    def _is_layer_selector(name: str) -> bool:
        return "*" in name

    def _fail(self, message: str, source: str = "") -> NoReturn:
        if source:
            raise PcbBuildError(f"{source}: {message}")
        raise PcbBuildError(message)
