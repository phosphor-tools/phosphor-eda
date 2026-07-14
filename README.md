# phosphor-eda

Python tools for parsing, inspecting, and querying electronic design projects from Altium,
KiCad, Eagle, and Cadence Allegro/OrCAD.

## Installation

```shell
pip install phosphor-eda
```

Import the package as `phosphor_eda` or run `phosphor-eda --help`.

## Usage

CLI commands are project-first: point `-P/--project` at a project entry file
(`.PrjPcb` for Altium, `.kicad_pro` for KiCad, `.opj` for OrCAD).

```shell
# Orientation pass: documents, pages, boards, key components, rails, buses
phosphor-eda -P board.kicad_pro overview

# Inspect connectivity — every pin on a net, aliases included
phosphor-eda -P board.kicad_pro show net SPI_CLK

# Query the full project (schematic + PCB) with DuckDB SQL
phosphor-eda -P board.kicad_pro sql "SELECT reference, x, y, side FROM footprints"

# Render a PCB layout to SVG using a bundled preset
phosphor-eda -P board.kicad_pro pcb render --render-settings - -o board.svg <<< '{"extends":"phosphor:realistic"}'
```

Run `phosphor-eda --help` for the full command set (`list`, `show`, `trace`,
`sql`, `pcb`).

## Development

The project requires Python 3.13 or newer and uses [uv](https://docs.astral.sh/uv/).

```shell
git submodule update --init --depth 1 --jobs 8
uv sync --locked
uv run pytest
```

The repositories under `tests/upstream/` are external fixture projects managed as Git submodules.

## License

phosphor-eda is licensed under the [Mozilla Public License 2.0](LICENSE). Third-party fixtures
retain their own licenses.
