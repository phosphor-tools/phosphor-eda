# phosphor-eda

`phosphor-eda` parses and queries electronic design projects from Altium, KiCad, Eagle,
Cadence Allegro/OrCAD, and related EDA formats.

## Installation

```shell
pip install phosphor-eda
```

The Python package is imported as `phosphor_eda`, and the command-line interface is available as
`phosphor-eda`.

## Development

The project requires Python 3.13 or newer and uses [uv](https://docs.astral.sh/uv/) for dependency
and environment management.

```shell
git submodule update --init --depth 1
uv sync --locked
uv run pytest
```

The repositories under `tests/upstream/` are external fixture projects managed as Git submodules.
