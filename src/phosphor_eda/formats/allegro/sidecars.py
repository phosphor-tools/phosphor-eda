"""Discovery and conservative parsing for Allegro package/padstack sidecars."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, TypeGuard

from phosphor_eda.formats.allegro.constants import AllegroBoardUnits, allegro_unit_to_mm

if TYPE_CHECKING:
    from pathlib import Path

_PAD_SUFFIX = ".pad"
_PACKAGE_SYMBOL_SUFFIXES = {".dra", ".psm"}
_ZIP_MAGIC = b"PK\x03\x04"
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# Padstack sidecars are small ZIP-bearing metadata files; cap both the
# container and first decompressed entry to keep malformed local projects bounded.
_MAX_PADSTACK_SIDECAR_BYTES = 64_000_000
_MAX_ZIP_ENTRY_BYTES = 1_000_000


@dataclass(frozen=True)
class _JsonObject:
    values: dict[str, object]


@dataclass(frozen=True)
class AllegroSidecarDiagnostic:
    path: Path
    kind: str
    code: str
    message: str


@dataclass(frozen=True)
class AllegroPadstackSidecar:
    path: Path
    name: str
    units: str
    shape: str
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class AllegroPackageSymbolSidecar:
    path: Path
    name: str
    kind: str
    byte_size: int
    signature_hex: str


@dataclass(frozen=True)
class AllegroSidecarSet:
    root: Path
    padstacks: tuple[AllegroPadstackSidecar, ...]
    package_symbols: tuple[AllegroPackageSymbolSidecar, ...]
    diagnostics: tuple[AllegroSidecarDiagnostic, ...]

    @property
    def padstacks_by_name(self) -> dict[str, AllegroPadstackSidecar]:
        return _unique_sidecars_by_name(self.padstacks)

    @property
    def package_symbols_by_name(self) -> dict[str, AllegroPackageSymbolSidecar]:
        return _unique_sidecars_by_name(self.package_symbols)

    @property
    def has_evidence(self) -> bool:
        return bool(self.padstacks or self.package_symbols or self.diagnostics)


def discover_allegro_sidecars(board_path: Path) -> AllegroSidecarSet:
    """Discover sidecar files in the board directory subtree."""
    root = board_path.parent
    padstacks: list[AllegroPadstackSidecar] = []
    package_symbols: list[AllegroPackageSymbolSidecar] = []
    diagnostics: list[AllegroSidecarDiagnostic] = []
    for path in sorted(root.rglob("*")):
        if path == board_path or not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == _PAD_SUFFIX:
            parsed_pad, parsed_diagnostics = parse_allegro_padstack_sidecar(path)
            diagnostics.extend(parsed_diagnostics)
            if parsed_pad is not None:
                padstacks.append(parsed_pad)
        elif suffix in _PACKAGE_SYMBOL_SUFFIXES:
            parsed_symbol, parsed_diagnostics = parse_allegro_package_symbol_sidecar(path)
            diagnostics.extend(parsed_diagnostics)
            if parsed_symbol is not None:
                package_symbols.append(parsed_symbol)
    diagnostics.extend(
        _duplicate_name_diagnostics(
            padstacks,
            kind="padstack",
            code="padstack-sidecar-duplicate-name",
        )
    )
    diagnostics.extend(
        _duplicate_name_diagnostics(
            package_symbols,
            kind="package_symbol",
            code="package-symbol-sidecar-duplicate-name",
        )
    )
    return AllegroSidecarSet(
        root=root,
        padstacks=tuple(padstacks),
        package_symbols=tuple(package_symbols),
        diagnostics=tuple(diagnostics),
    )


def parse_allegro_package_symbol_sidecar(
    path: Path,
) -> tuple[AllegroPackageSymbolSidecar | None, tuple[AllegroSidecarDiagnostic, ...]]:
    try:
        byte_size = path.stat().st_size
        with path.open("rb") as package_file:
            signature_hex = package_file.read(8).hex()
    except OSError as exc:
        return None, (
            AllegroSidecarDiagnostic(
                path=path,
                kind="package_symbol",
                code="package-symbol-sidecar-read-failed",
                message=str(exc),
            ),
        )
    return (
        AllegroPackageSymbolSidecar(
            path=path,
            name=path.stem,
            kind=path.suffix.lower().removeprefix("."),
            byte_size=byte_size,
            signature_hex=signature_hex,
        ),
        (),
    )


def parse_allegro_padstack_sidecar(
    path: Path,
) -> tuple[AllegroPadstackSidecar | None, tuple[AllegroSidecarDiagnostic, ...]]:
    try:
        byte_size = path.stat().st_size
        if byte_size > _MAX_PADSTACK_SIDECAR_BYTES:
            msg = (
                f"padstack sidecar is {byte_size} bytes, "
                f"which exceeds maximum {_MAX_PADSTACK_SIDECAR_BYTES}"
            )
            raise ValueError(msg)
        data = path.read_bytes()
        zip_offset = data.find(_ZIP_MAGIC)
        if zip_offset < 0:
            return None, (
                AllegroSidecarDiagnostic(
                    path=path,
                    kind="padstack",
                    code="padstack-sidecar-missing-zip",
                    message="padstack sidecar does not contain an embedded ZIP payload",
                ),
            )
        payload = _read_first_zip_payload(data[zip_offset:])
        parsed = _parse_padstack_json_payload(path, payload)
    except (
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        return None, (
            AllegroSidecarDiagnostic(
                path=path,
                kind="padstack",
                code="padstack-sidecar-parse-failed",
                message=str(exc),
            ),
        )
    return parsed, ()


def _duplicate_name_diagnostics(
    sidecars: list[AllegroPadstackSidecar] | list[AllegroPackageSymbolSidecar],
    *,
    kind: str,
    code: str,
) -> tuple[AllegroSidecarDiagnostic, ...]:
    seen: dict[str, Path] = {}
    diagnostics: list[AllegroSidecarDiagnostic] = []
    for sidecar in sidecars:
        key = _match_key(sidecar.name)
        first_path = seen.get(key)
        if first_path is None:
            seen[key] = sidecar.path
            continue
        diagnostics.append(
            AllegroSidecarDiagnostic(
                path=sidecar.path,
                kind=kind,
                code=code,
                message=f"sidecar name {sidecar.name!r} also appears at {first_path}",
            )
        )
    return tuple(diagnostics)


def _read_first_zip_payload(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for name in archive.namelist():
            info = archive.getinfo(name)
            if info.is_dir():
                continue
            if info.file_size > _MAX_ZIP_ENTRY_BYTES:
                msg = (
                    f"embedded ZIP entry {name!r} is {info.file_size} bytes, "
                    f"which exceeds maximum {_MAX_ZIP_ENTRY_BYTES}"
                )
                raise ValueError(msg)
            return archive.read(name).decode("utf-8-sig", errors="replace")
    msg = "embedded ZIP payload did not contain a file"
    raise ValueError(msg)


def _parse_padstack_json_payload(path: Path, payload: str) -> AllegroPadstackSidecar:
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", payload)
    loaded: object = json.loads(cleaned, object_pairs_hook=_json_object_from_pairs)
    root = _object(loaded)
    text = _object(root.get("text"))
    component = _padstack_component(_sequence(text.get("comps")))
    pad_design = _object(root.get("padDesign"))
    pad = _primary_pad(_sequence(pad_design.get("pad")))
    units = _string(component.get("units"))
    scale = _sidecar_unit_to_mm(units)
    width = _number(pad.get("width")) * scale
    height = _number(pad.get("height")) * scale
    return AllegroPadstackSidecar(
        path=path,
        name=_string(component.get("name")) or path.stem,
        units=units,
        shape=_pad_shape(_string(pad.get("shape"))),
        width_mm=width,
        height_mm=height,
    )


def _padstack_component(values: tuple[object, ...]) -> dict[str, object]:
    for value in values:
        item = _object(value)
        if _string(item.get("type")).casefold() == "padstack":
            return item
    msg = "padstack sidecar JSON did not contain a Padstack component"
    raise ValueError(msg)


def _primary_pad(values: tuple[object, ...]) -> dict[str, object]:
    pads = tuple(_object(value) for value in values)
    for pad in pads:
        if (
            _string(pad.get("class_name")).casefold() == "etch"
            and _string(pad.get("subclass_name")).casefold() == "begin layer"
            and _number(pad.get("width")) > 0.0
            and _number(pad.get("height")) > 0.0
        ):
            return pad
    for pad in pads:
        if _number(pad.get("width")) > 0.0 and _number(pad.get("height")) > 0.0:
            return pad
    msg = "padstack sidecar JSON did not contain positive pad geometry"
    raise ValueError(msg)


def _json_object_from_pairs(pairs: list[tuple[str, object]]) -> _JsonObject:
    return _JsonObject(dict(pairs))


def _object(value: object) -> dict[str, object]:
    if isinstance(value, _JsonObject):
        return value.values
    msg = "expected JSON object"
    raise TypeError(msg)


def _sequence(value: object) -> tuple[object, ...]:
    if _is_object_list(value):
        return tuple(value)
    msg = "expected JSON array"
    raise TypeError(msg)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _sidecar_unit_to_mm(units: str) -> float:
    normalized = units.casefold()
    if normalized in {"mil", "mils"}:
        return allegro_unit_to_mm(AllegroBoardUnits.MILS, 1)
    if normalized in {"inch", "inches"}:
        return allegro_unit_to_mm(AllegroBoardUnits.INCHES, 1)
    if normalized in {"millimeter", "millimeters", "mm"}:
        return allegro_unit_to_mm(AllegroBoardUnits.MILLIMETERS, 1)
    raise ValueError(f"unsupported Allegro padstack sidecar unit {units!r}")


def _pad_shape(shape: str) -> str:
    normalized = shape.casefold().replace(" ", "")
    if normalized == "circle":
        return "circle"
    if normalized in {"rectangle", "rect"}:
        return "rect"
    if normalized in {"roundedrectangle", "roundrect"}:
        return "roundrect"
    if normalized == "oval":
        return "oval"
    if normalized == "none":
        return ""
    return normalized


def _match_key(value: str) -> str:
    return value.casefold()


def _unique_sidecars_by_name[
    SidecarT: (AllegroPadstackSidecar, AllegroPackageSymbolSidecar),
](
    sidecars: tuple[SidecarT, ...],
) -> dict[str, SidecarT]:
    counts: dict[str, int] = {}
    for sidecar in sidecars:
        key = _match_key(sidecar.name)
        counts[key] = counts.get(key, 0) + 1
    result: dict[str, SidecarT] = {}
    for sidecar in sidecars:
        key = _match_key(sidecar.name)
        if counts[key] == 1:
            result[key] = sidecar
    return result
