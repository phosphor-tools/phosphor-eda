# phosphor-eda

Python tools for parsing, inspecting, and querying electronic design projects from Altium,
KiCad, Eagle, and Cadence Allegro/OrCAD.

## Installation

```shell
pip install phosphor-eda
```

Import the package as `phosphor_eda` or run `phosphor-eda --help`.

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

The package bundles the [Inter](https://github.com/rsms/inter) font
(`Inter-Regular.ttf`), which is licensed under the SIL Open Font License 1.1; its
license text ships alongside the font as
`src/phosphor_eda/geometry/fonts/OFL.txt`.
