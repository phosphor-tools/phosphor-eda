"""Discovery and conservative parsing for Allegro package/padstack sidecars."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

_PAD_SUFFIX = ".pad"
_PACKAGE_SYMBOL_SUFFIXES = {".dra", ".psm"}
_ZIP_MAGIC = b"PK\x03\x04"
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


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
        return {_match_key(sidecar.name): sidecar for sidecar in self.padstacks}

    @property
    def package_symbols_by_name(self) -> dict[str, AllegroPackageSymbolSidecar]:
        return {_match_key(sidecar.name): sidecar for sidecar in self.package_symbols}

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
        data = path.read_bytes()
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
            byte_size=len(data),
            signature_hex=data[:8].hex(),
        ),
        (),
    )


def parse_allegro_padstack_sidecar(
    path: Path,
) -> tuple[AllegroPadstackSidecar | None, tuple[AllegroSidecarDiagnostic, ...]]:
    try:
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
            return archive.read(name).decode("utf-8-sig", errors="replace")
    msg = "embedded ZIP payload did not contain a file"
    raise ValueError(msg)


def _parse_padstack_json_payload(path: Path, payload: str) -> AllegroPadstackSidecar:
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", payload)
    root = cast("dict[str, object]", json.loads(cleaned))
    text = _object(root.get("text"))
    component = _padstack_component(_sequence(text.get("comps")))
    pad_design = _object(root.get("padDesign"))
    pad = _primary_pad(_sequence(pad_design.get("pad")))
    units = _string(component.get("units"))
    scale = _unit_to_mm(units)
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


def _object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    msg = "expected JSON object"
    raise TypeError(msg)


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, list):
        return tuple(cast("list[object]", value))
    msg = "expected JSON array"
    raise TypeError(msg)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _unit_to_mm(units: str) -> float:
    normalized = units.casefold()
    if normalized in {"mil", "mils"}:
        return 0.0254
    if normalized in {"inch", "inches"}:
        return 25.4
    if normalized in {"millimeter", "millimeters", "mm"}:
        return 1.0
    return 1.0


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
