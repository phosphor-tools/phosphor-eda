# phosphor-eda

Command-line tools for parsing, inspecting, rendering, and querying electronic design projects
from Altium, KiCad, Eagle, and Cadence Allegro/OrCAD.

## Getting started

### 1. Install uv

Phosphor EDA uses `uv` for installation and updates. Follow the official
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/), then check that
it is available:

```shell
uv --version
```

### 2. Install Phosphor EDA

```shell
uv tool install phosphor-eda
```

If your terminal cannot find `phosphor-eda` after installation, run `uv tool update-shell` and
restart the terminal. Then check the installation:

```shell
phosphor-eda --help
```

### 3. Add the agent skill

The skill teaches coding agents when and how to use Phosphor EDA. Choose one installation method;
you do not need to install it more than once.

#### Any supported agent with npx

If you already have Node.js and npm, the Skills CLI can install the skill for Claude Code, Codex,
GitHub Copilot, and many other agents:

```shell
npx skills add phosphor-tools/phosphor-eda --skill phosphor-eda --global
```

#### Claude Code

Run these commands inside Claude Code:

```text
/plugin marketplace add phosphor-tools/phosphor-eda
/plugin install phosphor-eda@phosphor-tools
```

#### Codex

Paste this request into Codex:

```text
Install the phosphor-eda skill from
https://github.com/phosphor-tools/phosphor-eda/tree/main/skills/phosphor-eda
```

### 4. Try it

Start with an overview of an Altium, KiCad, or OrCAD project:

```shell
phosphor-eda -P path/to/project.kicad_pro overview
```

Run `phosphor-eda --help` to see the available commands. To update Phosphor EDA later, run:

```shell
uv tool upgrade phosphor-eda
```

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
