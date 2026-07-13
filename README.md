# phosphor-eda

Python tools for parsing, inspecting, and querying electronic design projects from Altium,
KiCad, Eagle, and Cadence Allegro/OrCAD.

## Installation

```shell
pip install phosphor-eda
```

Import the package as `phosphor_eda` or run `phosphor-eda --help`.

## Usage

```python
from pathlib import Path

import phosphor_eda

# Load a schematic, a PCB, or a full project from any supported format.
schematic = phosphor_eda.load_design(Path("design.kicad_sch"))
board = phosphor_eda.load_pcb(Path("board.kicad_pcb"))
project = phosphor_eda.load_project(Path("board.kicad_pro"))

# Render a board to SVG.
settings = phosphor_eda.RenderSettings(side="top", width=1600, font_size=12.0)
svg = phosphor_eda.render_pcb_svg(board, settings).svg

# Query a project as an in-memory DuckDB database.
from phosphor_eda.query.sql import load_database

connection = load_database(project)
```

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
