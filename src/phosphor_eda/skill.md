---
name: phosphor-eda
description: >
  Query electronic schematics (Altium, KiCad, Eagle, OrCAD) as structured
  data and render PCB layouts as SVG. Use when working with hardware designs,
  PCB layouts, or EE datasheets — to trace signals, identify components,
  render board images, or answer questions about circuit connectivity.
---

# phosphor-eda

Query electronic schematics as structured data — components, pins, nets, and
connectivity. Render PCB layouts as SVG for documentation, design review, and
user manuals. CLI entry is project-first: use Altium `.PrjPcb`, KiCad
`.kicad_pro`, or OrCAD `.opj` with `-P/--project`. Bare schematic/PCB
documents remain lower-level parser inputs, including native OrCAD/Allegro
`.brd` board files.

The schematic is the ground truth for **what is connected to what**. It does
not tell you how to configure anything — pin mux, register values, firmware
behavior, timing, and protocol details live in reference documents. Most
hardware questions require both the schematic and at least one reference
document.

After querying the schematic, cross-reference component specs against
datasheets using `datasheet_search` with the manufacturer part number (from
`show component`). The schematic tells you connectivity; the datasheet and
reference manual tell you what it means.

When component output includes `MPN:`, use it as the authoritative component
identity for datasheet lookup, part review, and electrical interpretation.
Treat `SYMBOL:` and `Desc:` as source-library context that may be generic or
stale.

## How to work with hardware designs

**Investigate before answering.** Never speculate about a net, pin function,
or component you haven't queried. Use the CLI to extract the specific data,
then reason from what you found. If you need information beyond the schematic
(and you usually will), find and read the reference document before making
claims.

**Separate what the schematic proves from what needs verification.** The
schematic is authoritative for connectivity — which pins are wired to which
nets. But interpreting what those connections _mean_ (which peripheral
instance, which alternate function mode, whether a swap is intentional)
requires the device's reference manual. Present schematic findings and
reference-verified findings distinctly. If you haven't read the reference
manual, say "the schematic shows X is wired to Y — I need to verify the pin
mux to confirm this is [peripheral]."

**Answer from evidence, not recall.** Every factual claim should trace back to
CLI output or a document you read in this conversation. Training knowledge is
especially unreliable for: pin alternate function assignments (ALT mode
numbers), peripheral-to-pin mappings, register addresses and field layouts,
electrical characteristics, and protocol timing. These are exactly the kind of
details that are almost-right in training data — close enough to sound
confident, wrong enough to waste hours of debugging.

**Wrong answers are worse than no answer.** If the schematic and available
documents don't give you enough to answer confidently — say so and ask. The
user may have the document locally, know which manual covers it, or be able to
clarify. A wrong claim about a pin mux assignment or power rail can send
someone debugging a phantom problem.

**Show, don't just tell.** When answering a question about a specific part of
the PCB — a bus, a component, a routing path, a probe point — render an SVG
that highlights the relevant area. Use `pcb render` with `--render-settings`
(JSON file or `--render-settings -` for stdin) to specify a bundled preset,
highlights with colors, annotations, and any source/token overrides in a single
config. Investigate with `sql` or `list`/`show` first to discover the net names and component
references, then render with those names. A highlighted board image with
labeled annotations is worth more than a paragraph of coordinates.

**After drafting, check your work.** For each claim in your response, can you
point to the specific CLI output or document passage that supports it? If not,
either find the evidence or remove the claim.

## Reference documents

The schematic shows wiring. Almost everything else — pin alternate functions,
peripheral configuration, timing constraints, protocol behavior, errata —
lives in supporting documents. Default to looking for a reference rather than
answering from memory.

**What to look for:** Datasheets, reference manuals, user guides, application
notes, errata. The answer is often not in the datasheet — reference manuals
and user guides cover the pin mux tables, register maps, and configuration
details that datasheets omit. These documents frequently cover a part family
(e.g., an entire MCU series), not just the specific model.

**How to find them:** Use the `datasheet_search` tool with the manufacturer
part number (from `show component`). It returns structured specs and direct
PDF URLs. If the part isn't found there, check the working directory for local
PDFs or ask the user.

**How to read them:** Use `open_document` with the PDF URL, then
`grep_document` and `read_document` to navigate. For large reference manuals
(1000+ pages), use the TOC to target specific sections rather than reading
the whole document.

## CLI reference

Project-backed commands take an explicit project entry file through the root
`-P/--project` option. Supported project entries are `.PrjPcb` (Altium),
`.kicad_pro` (KiCad), and `.opj` (OrCAD). Bare schematic and PCB documents are
lower-level parser/API inputs, not CLI entry points. Native OrCAD/Allegro
`.brd` board files are supported by the lower-level board parser/API and
standalone board project loading, and resolved `.opj` board documents populate
loaded project boards when the board path is local and supported. Full-board
fidelity is strongest for V16.5/V16.6 fixtures; newer Allegro families may have
less fixture evidence, and degraded records should be treated as parser
diagnostics rather than silently trusted.

Run `phosphor-eda -P <PROJECT> overview` first when working with any schematic
or board. The overview is the project orientation pass: it shows documents,
schematic pages, boards, important components, rails, buses, notes, and what is
intentionally omitted. Use it to choose the next `show`, `list`, `sql`, or
`pcb render` command.

### Querying

```sh
# Start every schematic/board investigation here
phosphor-eda -P <PROJECT> overview

# Indexes
phosphor-eda -P <PROJECT> list pages
phosphor-eda -P <PROJECT> list components
phosphor-eda -P <PROJECT> list nets
phosphor-eda -P <PROJECT> list buses

# Detail
phosphor-eda -P <PROJECT> show component U1
phosphor-eda -P <PROJECT> show net SPI_CLK
phosphor-eda -P <PROJECT> show net "USB*"
phosphor-eda -P <PROJECT> show bus "DATA[0..7]"
phosphor-eda -P <PROJECT> show page "Power"

# Inter-component paths
phosphor-eda -P <PROJECT> trace U1 U2
```

Use these CLI commands. Don't grep `.txt` files.

### Selectors

Object selector flags and `show` arguments use shell-style glob syntax:
`*`, `?`, `[abc]`, `[a-z]`, and `[!abc]`. Repeat a selector flag to include
multiple patterns. Prefix a selector with `!` to exclude matches, and use
`\!` for a literal leading bang. Matching is case-sensitive.

```sh
phosphor-eda -P <PROJECT> list components --component "U*" --component "!U99"
phosphor-eda -P <PROJECT> list nets --net "USB*" --net "!*_SHIELD"
phosphor-eda -P <PROJECT> list pages --page "Power*"
phosphor-eda -P <PROJECT> show net "USB*"
```

Exact object names, scoped IDs, aliases, and physical designators still work.
If an exact selector is unknown, the CLI errors. Glob selectors that match
nothing return no rows. `show` can return multiple detail blocks separated by
blank lines. `trace` is the exception: it requires two exact single component
references.

### Filters

All `list` commands accept composable filters (AND logic):

```sh
# Nets
list nets -c U1                                  # nets touching U1
list nets -c U1 -c U2 --trace                    # shared by U1 AND U2, through passives
list nets --no-power                             # signal nets only
list nets --power                                # power rails only
list nets --page "SPI" --multi-page              # cross-page nets on SPI page
list nets --min-pins 3                           # nets with 3+ connections
list nets --bus "DATA[0..7]"                     # member nets in a bus

# Components
list components --component "U*"                 # ICs only
list components --component "TP*"                # test points
list components --passive / --no-passive         # passives only / exclude
list components --page "Power"                   # on a specific page
list components --net SPI_CLK                    # on a specific net
list components --min-pins 4                     # 4+ pins

# Pages
list pages --net GND                             # pages containing a net
list pages --component U1                        # pages containing a component

# Buses
list buses --kind vector                         # vector/group/harness buses
list buses --net DATA0                           # buses containing DATA0
list buses --min-members 8                       # buses with 8+ member nets
```

Bus names such as `DATA[0..7]` are aggregate grouping evidence, not scalar net
names. Use `list buses` and `show bus` to inspect bus membership, then use
`show net` on member nets. A scalar net can belong to multiple buses; `list
nets` and `show net` include all bus memberships.

### Signal tracing

`show component` traces through 2-pin passives by default:

```
Pin 5  SPI_CLK  -> SPI_CLK  [R33 -> U2.3]  (R50 to P3V3)
```

- `[R33 -> U2.3]` — series: through R33 to U2 pin 3
- `(R50 to P3V3)` — shunt: R50 is a pull-up

`trace` shows all signal paths between two components:

```
U1.42  SPI_CLK   -- R33 -- U2.12  SCK        (R50 to P3V3)
U1.43  SPI_MOSI  ---------- U2.13  MOSI
```

### Large designs

For broad questions, launch **parallel sub-agents** running CLI queries:

1. `overview` for the project inventory and orientation
2. Spawn sub-agents for `show page` / `show component` / `show net` in parallel
3. Synthesize

### SQL queries (PCB + schematic)

Run arbitrary SQL against the full project data using DuckDB with the Spatial
extension. Use this for physical/spatial PCB questions that the structured
commands can't answer: distances between components, trace lengths, drill
sizes, layer stackup, design rule values, net class properties, and
cross-referencing schematic connectivity with physical layout.

```sh
# Inspect the schema
phosphor-eda -P <PROJECT> sql --schema

# Query PCB layout data
phosphor-eda -P board.kicad_pro sql "SELECT reference, x, y, side FROM footprints"
phosphor-eda -P board.PrjPcb sql "SELECT * FROM drill_histogram"

# Spatial queries
phosphor-eda -P board.kicad_pro sql "SELECT ST_Distance(a.geom, b.geom) FROM footprints a, footprints b WHERE a.reference='U1' AND b.reference='U7'"

# Find pads on a specific net
phosphor-eda -P board.PrjPcb sql "SELECT reference, pad_number, x, y, side FROM pads WHERE net_name = 'SPI_CLK'"

# Find pads with explicit or derived solder-mask openings
phosphor-eda -P board.PrjPcb sql "SELECT reference, pad_number, mask_aperture_width, mask_aperture_height, mask_aperture_source FROM pads WHERE mask_aperture_source IS NOT NULL"

# Check design rules
phosphor-eda -P board.PrjPcb sql "SELECT name, kind, scope1, preferred_value_mm FROM design_rules WHERE kind = 'SolderMaskExpansion'"

# Trace routing by layer
phosphor-eda -P board.kicad_pro sql "SELECT net_name, layer, SUM(length_mm) FROM conductors WHERE kind IN ('trace', 'trace_arc') GROUP BY net_name, layer"
```

**When to use `sql` vs. structured commands:**

- **Connectivity** (what's wired to what, pinouts, signal tracing) → use `list`,
  `show`, `trace` — they're faster and format output for reading
- **Physical properties** (coordinates, dimensions, clearances, routing,
  spatial relationships, design rules, layer stackup) → use `sql`
- **Cross-referencing** (which schematic nets have the longest traces, which
  components are closest together) → use `sql`

**Key tables:** `footprints`, `pads`, `vias`, `drills`, `conductors`,
`artwork`, `board_profile`, `pours`, `keepouts`, `layers`, `board`,
`net_classes`, `net_class_members`, `design_rules`, `components`,
`component_occurrences`, `component_metadata`, `pins`, `pin_occurrences`,
`nets`, `net_occurrences`, `net_metadata`, `pages`, `project_documents`,
`project_parameters`, `project`. Occurrence metadata tables preserve parser
provenance when needed.

**Key views:** `net_routes` (trace length per net/layer), `net_summary`
(combined schematic + PCB stats per net), `width_violations` (traces not
matching net class width), `drill_histogram` (drill sizes with counts).

**Geometry columns** (`geom`, `centerline`, `drill_geom`, `boundary`) support
DuckDB Spatial functions: `ST_Distance`, `ST_Area`, `ST_Contains`,
`ST_Intersects`, `ST_Buffer`, `ST_AsText`. Geometry is in board-space
millimetres.

**Impedance and thermal calculations:** The `layers` table includes every
physical layer in the stackup — not just copper. Solder mask (with its Dk and
thickness), prepreg, core, and copper orientation are all present. When
computing impedance or thermal resistance, use ALL layers between the
conductor and its reference plane, including solder mask on outer layers.
Solder mask Dk (~3.5–4.1) significantly affects microstrip impedance.
Copper orientation (normal vs reversed foil) affects roughness loss at high
frequencies. Query the full picture:

```sql
SELECT position, name, layer_type, thickness_mm, epsilon_r, loss_tangent, copper_orientation
FROM layers WHERE position IS NOT NULL ORDER BY position
```

**Iterative exploration pattern:** Start broad (counts, group-bys), then
narrow to specific nets/components. Example flow for "where can I probe
this bus?":

1. Find the nets: `WHERE name LIKE '%SPI%'`
2. Find the pins: `FROM pins WHERE net_name IN (...)`
3. Check pad sizes and positions: `FROM pads WHERE net_name IN (...)`
4. Check via positions: `FROM vias WHERE net_name IN (...)`
5. Check routing layers and lengths: `FROM conductors WHERE kind IN ('trace', 'trace_arc') AND net_name IN (...)`
6. Spatial proximity: `ST_Distance` between features
7. **Render**: `pcb render` with `--render-settings` — highlights for
   the nets and endpoints, annotations to label probe points or areas

Always finish a PCB investigation with a rendered SVG. The SQL/structured
queries give you the data; the render makes it actionable for the user.

### PCB stackup

Print the physical layer stackup (top to bottom) as a readable table — layer
name, type, thickness, material, dielectric constant, and loss tangent — with
total board thickness. Placeholder layer slots that carry no physical
construction are omitted.

```sh
phosphor-eda -P <PROJECT> pcb stackup
```

This is the same stackup table `overview` shows, on its own. Pair it with the
`conductors` table via `sql` (trace width and the actual coupled-pair gap) to
support design-level impedance calculations without leaving the CLI.

### PCB rendering

Render PCB layouts as SVG through a project entry file. Native OrCAD/Allegro
`.brd` boards are supported by the lower-level board parser/API, standalone
board project loading, and resolved `.opj` board documents, while CLI rendering
still starts from a project entry. For OrCAD/Allegro projects, prefer the `.opj`
when available so schematic context, sidecars, and the native board load
together. Full-board fidelity is strongest for V16.5/V16.6 fixtures; newer
Allegro families may carry less fixture evidence, so inspect parser diagnostics
before relying on rendered geometry. If a project contains multiple boards,
pass `--board` with the board name, source filename, or source path suffix.
`--render-settings-schema` does not require a project.

```sh
# Discover the render settings JSON schema
phosphor-eda pcb render --render-settings-schema

# Default engineering review render
phosphor-eda -P board.kicad_pro pcb render --render-settings - -o board.svg <<< '{"extends":"phosphor:realistic"}'

# Full render with highlights, annotations, and colors
phosphor-eda -P board.PrjPcb pcb render --board Board.PcbDoc --render-settings render.json -o board.svg
```

Always write to `-o` file — raw SVG in conversation is not useful. Present
the file using whatever image-display convention the host environment
provides.

#### Render settings

**Use `--render-settings` for all nontrivial renders.** Write a JSON file
(or pipe via `--render-settings -` for stdin) that specifies bundled settings,
highlights with colors, annotations, `fontSizePt`, source layer selection, and
style tokens. Use `extends` to layer board-specific settings over bundled settings.
Default to `phosphor:realistic` unless the user asks for a different style or
gives a specific output use case. The renderer handles all dimming,
inner-layer visibility, via rendering, and preset integration; do not replicate
any of this in custom CSS. Use
`phosphor-eda pcb render --render-settings-schema` when you need the exact
machine-readable schema.

All bundled presets render native SVG
primitives from PCB source geometry. EDA mode renders selected source layers in
reverse source file/display order for front-side views and source file/display
order for back-side views, and keeps board outline and drills as top overlays.
Realistic mode renders a physical, side-aware board appearance.
Layers are serialized as groups of separate child paths, and opacity is applied
on the layer group so overlapping same-layer primitives do not darken each
other. Avoid CSS that moves opacity onto individual paths.

Silkscreen is clipped with manufacturable solder-mask openings in both EDA and
realistic modes. Openings come from the full source geometry inventory, not only
the visibly selected source layers: explicit KiCad and Altium mask-layer
polygons, lines, and arcs plus side-visible pads, including pad mask expansion
and Altium pad-template-derived apertures when the board data provides them. In
realistic mode, these openings punch through `realistic.solder_mask`; exposed board
material is rendered as `realistic.exposed_substrate`, and exposed copper is
rendered above it where copper exists under the aperture. Via tenting metadata
is not fully modeled yet; parsed vias and drills are treated as through-board
openings for masking, so confirm tented-via details against source PCB data
before making manufacturing claims from the render alone.

```json
{
  "extends": "phosphor:realistic",
  "fontSizePt": 14,
  "highlights": [{ "pad": "CN11.[12]", "color": "#c00000" }],
  "annotations": {
    "pointers": [{ "target": "CN11.30", "label": "PA1 / REF_CLK" }]
  }
}
```

**Top-level fields** (all optional):

| Field         | Type                     | Description                                                                                                                                                                                      |
| ------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `extends`     | string                   | Base render settings loaded before this file. Use `phosphor:<name>` for bundled settings or a relative/absolute JSON path.                                                                       |
| `renderMode`  | `"eda"` \| `"realistic"` | Projection mode for selected PCB artwork                                                                                                                                                         |
| `side`        | `"front"` \| `"back"`    | Board side to view (default: `"front"`)                                                                                                                                                          |
| `rotation`    | 0 \| 90 \| 180 \| 270    | Clockwise view rotation in degrees, applied after the back-side mirror. Board artwork rotates; annotation labels stay upright                                                                    |
| `width`       | integer                  | SVG raster width in pixels (default: 800). Resolution only — annotation proportions are unaffected. Usually omit it                                                                              |
| `fontSizePt`  | number                   | Annotation label font size in points, as seen when the image is viewed at a standard content-column width (~1000 px). Independent of render width and board size; default 20                     |
| `background`  | string                   | Canvas background CSS color (default `#ffffff`); use `"none"` for transparent                                                                                                                    |
| `debugAttributes` | boolean              | Emit per-element `data-*` provenance attributes for CSS targeting/debugging (default false — they multiply file size)                                                                            |
| `source`      | object                   | Source layer selection rules for derived artwork                                                                                                                                                 |
| `tokens`      | object                   | Semantic style tokens such as `eda.copper.front.fill`, `eda.layer[F.Cu].fill`, or `highlight.copper.front.opacity`                                                                               |
| `dimming`     | object                   | `{"mode": "off" \| "on" \| "auto"}` — `auto` (default) dims base layers under a translucent scrim whenever a highlight resolves; tune with `highlight.dim.fill` / `highlight.dim.opacity` tokens |
| `highlights`  | array                    | Net/component/pad selectors to highlight. Optional per-entry `color` (fill), and `stroke` + `strokeWidthMm` for an outline on just that highlight                                                |
| `annotations` | object                   | Boxes, pointers, labels, legend                                                                                                                                                                  |
| `custom_css`  | string                   | Inline JSON string only; last-resort CSS injected after structured render styles                                                                                                                 |

#### Source layers and styling control

Source layer rules decide which PCB-authored artwork is available to the
derived renderer before EDA or realistic projection. Layer selectors can match
portable `role`, `side`, or exact native layer `name`.

| Layer selector field | Values                                                                                                                                                               |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `role`               | Normalized layer roles such as `copper`, `silkscreen`, `solder_mask`, `fabrication`, `courtyard`, `edge`, `drill`, `keepout`, `mechanical`, `designator`, or `value` |
| `side`               | `front`, `back`, `inner`, `active`                                                                                                                                   |
| `name`               | Exact native layer name, such as `F.Cu`, `B.SilkS`, or `Mechanical 13`                                                                                               |

Each rule may set `visible`, `itemKinds`, `purposes`, and `contentKinds`.
`itemKinds` selects typed inventory entries such as `pad`, `via`, `drill`,
`conductor`, `artwork`, and `board_profile`. `purposes` selects the renderer
projection purpose, such as `copper`, `silkscreen`, `designator`, `value`,
`solder_mask`, `solder_paste`, `mechanical`, or `board_profile`.
`contentKinds` selects typed source content such as `trace`, `trace_arc`,
`line`, `arc`, `text`, or `dimension`.

`source.excludeComponents` is an array of component selectors. Use globs such
as `"R*"`, `"C*"`, and `"L*"` to hide passive component-owned artwork, and
negative selectors such as `"!C100"` to keep a specific match visible.

In inherited settings, rules merge by their selector identity (`match.name`,
`match.role`, `match.side`). A child rule with the same selector overrides rule
settings such as `visible`, `itemKinds`, `purposes`, and `contentKinds`; a child
rule with a new selector appends after inherited rules. Use `visible: false` to
hide an inherited source layer, and `visible: true` to show an additional source
layer that a preset normally omits. The old `objects` setting is intentionally
unsupported.

Tokens are semantic, not raw SVG selectors. EDA presets use `eda.*` tokens for
source-layer artwork; realistic presets use `realistic.*` tokens for the
physical board stack. Highlight overlays use `highlight.*` tokens and explicit
highlight colors override only the fill color. The `cad` render mode and
`cad.*` token namespace have been removed.

EDA default colors are Altium-like for common layers: front copper red, bottom
copper blue, top silkscreen white, bottom silkscreen yellow, board outline light
gray, and drills dark gray. Inner copper colors are generated deterministically
and remain unique for up to 160 copper layers. Explicit native-layer tokens such
as `eda.layer[In42.Cu].fill` override semantic tokens and generated defaults.
Drills serve two roles: they are clipping infrastructure for filled board and
copper geometry, and in EDA mode they can also render as a visible outline-only
source layer.

Annotation tokens use `annotation.*`. Color, halo, and width tokens are stable
presentation controls. Boolean visibility tokens are stable sparse-documentation
controls: `annotation.label.pillVisible` toggles the label pill background and
defaults to `true`; `annotation.connector.dotVisible` toggles the connector
target dot and defaults to `true`.

`highlight.marker.enabled: true` draws a minimum-size ring around each
highlighted pad so tiny pads (0402 and below) stay findable. Rings are off by
default and in every preset — enable them only when highlighted pads are too
small to see, and expect overlap on fine-pitch connectors.

Use `highlights` for semantic emphasis and overlay inclusion. Use
`tokens` for visual presentation. Use `custom_css` only as a last-resort escape
hatch when a presentation tweak is not yet expressible as structured settings.

#### Extending render settings

`extends` composes render settings without requiring multiple CLI arguments.
The base file loads first, then the current file overrides it, then explicit
CLI flags override the merged result. Built-in settings use the `phosphor:`
namespace:

```json
{
  "extends": "phosphor:documentation",
  "side": "back",
  "highlights": [{ "pad": "CN11.[12]" }]
}
```

Bundled settings:

| Name                     | Use                                                                                                                                                                                                      |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `phosphor:realistic`     | Default. The board as a physical object (soldermask, silkscreen, exposed copper). Use for bench/bring-up callouts and whenever no other style clearly fits.                                              |
| `phosphor:design`        | EDA/routing style, like the design tool. Shows all copper at reduced opacity, silkscreen artwork, board outline, and drills; no soldermask surface. Use for routing, layer, and construction discussion. |
| `phosphor:print`         | The design view restyled for black-and-white printing: white canvas, light-grey copper, dark silkscreen, solid-black highlights. Use for printable documents.                                            |
| `phosphor:documentation` | Sparse callout figure: board outline, warm putty copper and silkscreen on white, viewed-side pads only (no traces, vias, or passives — `R*`/`C*`/`L*`/`FB*` are excluded, though highlighting one still shows it), deep-red highlights, large bold haloed labels. Use for pin/connector callouts, work instructions, and isolated feature figures. |

Bundled presets omit fab/reference/designator layers by default. Design-mode
silkscreen selection includes silk primitives and board-level graphic text, not
component reference/value text. Select `role: "fabrication"` explicitly when
documentation layers are needed.

Local files can extend relative or absolute JSON paths. Relative paths resolve
from the file containing `extends`. `custom_css` concatenates base CSS first
and child CSS second; scalar fields override; arrays such as `highlights`
replace the base array; nested objects such as `annotations` merge by key;
`source.layers` merges by selector identity so board-specific settings can hide
or add layers without restating the whole preset.

```json
{
  "extends": "phosphor:realistic",
  "source": {
    "layers": [
      {
        "match": { "role": "silkscreen", "side": "active" },
        "visible": false
      },
      {
        "match": { "name": "Mechanical 13" },
        "visible": true,
        "itemKinds": ["artwork"],
        "purposes": ["mechanical"]
      }
    ]
  }
}
```

#### Highlights

Each entry highlights exactly one net, component, or pad selector. The `color`
field is optional — omit it to use the default per-layer copper colors.

```json
{ "net": "SPI*", "color": "#3b8bff" }
{ "component": "U*" }
{ "pad": "CN11.[12]", "color": "#c00000" }
```

Highlight targets use the same shell-style selector syntax as CLI object
selectors, including leading-bang exclusions in repeated CLI flags or settings
arrays. Annotation targets are still exact component/pad/net-near-component
references because annotation placement needs one concrete target.

Net highlights restore selected layer artwork for the matching net.
Component highlights restore selected component artwork where the source
geometry carries component metadata. Pad highlights restore a single pad
above the board artwork. They are independent —
include both to see a component _and_ its connected traces.

**Net highlights follow the signal through series passives by default.**
A physical signal often spans several PCB nets, split at series resistors or
ESD diodes (`USB_D+` continues as another net past its termination). When a
schematic is available from the loaded project, the highlight covers every net
in that group, on all copper layers. Power nets and high-fanout rails are
boundaries: highlighting `GND` highlights only `GND`, and a pullup to a
rail does not drag the rail in. Add `"exact": true` to a highlight to match
only the literal net name. Without a loadable schematic in the project, net
highlights match exact names and the CLI prints a warning.

For quick one-off terminal debugging, the CLI also has repeatable
`--highlight-pad CN11.30` flags. For agent work, prefer render settings JSON
so the complete render request is reproducible.

**Use saturated colors for highlights** — they need to pop against the
dimmed board. Use the highlight palette below, not the muted annotation
palette.

**Do not write dimming or opacity rules in `custom_css`.** The highlight
engine generates these automatically with correct handling for all
bundled presets, inner copper layers, and via rendering.

#### Color palettes

Highlights and annotations serve different roles and need different
levels of saturation.

**Highlight colors** — saturated, high-contrast, must pop against
dimmed copper and green solder mask:

| Name         | Hex       | Use for                     |
| ------------ | --------- | --------------------------- |
| Vivid gold   | `#ff9f1a` | Power, primary signal group |
| Bright blue  | `#3b8bff` | Clocks, digital signals     |
| Hot green    | `#2ecc40` | Data lines, status          |
| Signal red   | `#e8433e` | Resets, critical paths      |
| Vivid violet | `#a855f7` | Tertiary signals, debug     |

**Annotation colors** — muted tones that label without competing with
highlights. White contrast text on dark PCB backgrounds:

| Name        | Hex       | Use for                             |
| ----------- | --------- | ----------------------------------- |
| Warm gold   | `#d4a843` | Primary labels, power annotations   |
| Slate blue  | `#5b8abf` | Secondary labels, clock annotations |
| Sage green  | `#6ba368` | Data line labels                    |
| Dusty coral | `#c2685a` | Warning labels, critical paths      |
| Soft violet | `#8b7bbf` | Tertiary labels, debug annotations  |

#### Annotations

Annotate rendered PCBs with boxes, pointers, labels, and legends using
schematic vocabulary (component refs, net names, pad numbers). Declare
_what_ to annotate in the `annotations` object of the render settings —
the renderer handles coordinates and placement automatically.

**Do not set position hints** (`position`, `label_position`) unless the
user explicitly asks for a specific side. Auto-placement routes each
label to the nearest board edge, which produces the shortest connectors
and avoids crossing the board interior.

**Pick the right annotation type — don't double up:**

- **Pointer** — when you want to call out a specific component, pad, or
  net location. The label says what it is.
- **Box** — when you want to visually group multiple components into a
  functional block. The label names the block.
- **Legend** — when you need to explain what the highlight colors mean,
  or add context that doesn't belong on a specific component (e.g.
  "all bypass caps within 5mm of VDD pins"). A legend is a key, not a
  label — use it when there's nothing specific to point at.
- **Label** — muted supplementary info on a component (lighter than a
  pointer).

Don't combine a legend key with pointers/boxes that already label the
same information. If you're pointing at U1 with "MCU", you don't also
need a legend entry for "MCU". Use a legend when the color-coding or
context isn't obvious from the annotations alone.

**Annotations schema:**

```json
{
  "annotations": {
    "boxes": [
      {
        "targets": ["U1", "U2"],
        "label": "SPI Controller",
        "color": "#d4a843"
      }
    ],
    "pointers": [
      { "target": "U7", "label": "FPGA", "color": "#5b8abf" },
      { "target": "U7.10", "label": "SPI_CLK pin" },
      { "target_net": "SPI_CLK", "target_near": "U7", "label": "Clock" }
    ],
    "labels": [{ "target": "J1", "content": "USB-C" }],
    "legend": {
      "title": "SPI Bus",
      "entries": [
        { "color": "#3b8bff", "label": "SCLK" },
        { "color": "#ff9f1a", "label": "MOSI" },
        { "color": "#2ecc40", "label": "MISO" },
        { "label": "All traces routed on inner layer 1" }
      ]
    }
  }
}
```

Required fields: `targets` (boxes), `entries` (legend). For pointers,
either `target` or the `target_net` + `target_near` pair is required.
`legend.title` and label `target` are optional. All other fields are
optional.

**Targets:**

| Syntax                       | Resolves to                                     | Used by                 |
| ---------------------------- | ----------------------------------------------- | ----------------------- |
| `"U7"`                       | Component center + bounding box                 | Boxes, pointers, labels |
| `"U7.10"`                    | Specific pad center                             | Pointers                |
| `target_net` + `target_near` | Pad on `target_near` connecting to `target_net` | Pointers                |

**Position hints** (`position`, `label_position`):

Labels are placed in margins outside the board outline, connected to
targets by orthogonal right-angle connector lines. The placement solver
prevents overlaps.

**Leave position hints empty or omit them — auto-placement picks the
nearest board edge, which is almost always correct.** Only set a hint
when the user explicitly requests a specific side.

For back-side renders, `position` and `label_position` hints use rendered-view
sides, not original board-file coordinates. `"left"` means the left side of
the SVG the user sees after the board is mirrored for the back view.

| Hint                        | Margin                                                      |
| --------------------------- | ----------------------------------------------------------- |
| `""` (empty/omit)           | **Auto — nearest board edge (default, strongly preferred)** |
| `"above"` / `"top"`         | Top margin (only if user requests)                          |
| `"below"` / `"bottom"`      | Bottom margin (only if user requests)                       |
| `"left"` / `"board-left"`   | Left margin (only if user requests)                         |
| `"right"` / `"board-right"` | Right margin (only if user requests)                        |

**Styling:**

- Boxes: solid 2px border + semi-transparent fill (same color at 15% opacity)
- Pointer/box pills: solid fill matching the annotation color, white or
  black text chosen automatically for contrast
- Label pills: muted dark fill (supplementary appearance)
- Connectors: 2px orthogonal lines; pointers have a dot at the target,
  box connectors terminate at the box edge
- Legend: dark semi-transparent background with rounded corners
- Font: embedded Inter Regular at 10px, board-size-independent

#### Custom CSS

The `custom_css` field in render settings injects CSS after structured render
styles. It is an inline JSON string only. Prefer `source`, `tokens`, and
`highlights`; use custom CSS only as a last-resort escape hatch for styling
tweaks that are not yet expressible as structured settings. Do **not** use it
for highlight dimming; the highlight engine handles this.

**Data attributes on derived SVG output** — use these in CSS selectors:

| Attribute               | On                   | Example                                  |
| ----------------------- | -------------------- | ---------------------------------------- |
| `data-role`             | derived layer groups | `g[data-role="eda.copper.front"]`        |
| `data-source-layers`    | derived layer groups | `g[data-source-layers~="F.Cu"]`          |
| `data-source-id`        | primitive paths      | provenance IDs such as `pad:U1:1:0`      |
| `data-highlight-target` | highlight groups     | `g[data-highlight-target="net:SPI_CLK"]` |
| `data-source-layer`     | primitive paths      | `path[data-source-layer="F.Cu"]`         |

Group-level attributes (`data-role`, `data-source-layers`,
`data-highlight-target`) are always present. Per-element attributes
(`data-source-id`, `data-source-layer`, and the component/net/pad identity
set) are debug output: enable them with `"debugAttributes": true` in render
settings or `--debug-attributes` on the CLI. They multiply file size
several-fold, so leave them off unless a CSS selector or debugging session
needs them.

Derived SVG output is path-oriented. Avoid relying on raw object classes
such as traces or pads; select source artwork with render settings instead.

#### Full example — SPI bus review

Highlights use saturated colors so traces pop. Annotations use muted
colors and label the endpoints. The legend keys the highlight colors
since they aren't labeled by the pointers.

```json
{
  "extends": "phosphor:realistic",
  "highlights": [
    { "net": "ADC_*", "color": "#3b8bff" },
    { "component": "U[17]" }
  ],
  "annotations": {
    "pointers": [
      { "target": "U1", "label": "MCU", "color": "#d4a843" },
      { "target": "U7", "label": "ADC", "color": "#5b8abf" }
    ],
    "legend": {
      "title": "SPI Bus",
      "entries": [
        { "color": "#3b8bff", "label": "SCLK" },
        { "color": "#ff9f1a", "label": "MOSI" },
        { "color": "#2ecc40", "label": "MISO" }
      ]
    }
  }
}
```

```sh
phosphor-eda -P board.kicad_pro pcb render --render-settings spi.json -o spi_bus.svg
```

#### Workflow

1. Query the schematic to find component refs and net names
2. Write one render settings JSON with `extends`, highlights, annotations
3. Render with `--render-settings`

Labels are plain text — keep them concise (e.g. "SPI Clock", "Debug").

**When to render:** Render a highlighted SVG any time the user asks about
something spatially specific on the board — a bus, a component neighborhood,
probe points, routing, clearances. The investigation (via `sql`, `list`,
`show`, `trace`) gives you the net names and component references; the render
makes them visually concrete. Don't make the user ask for an image — if the
answer involves physical location on the PCB, include a render.

## Output format

Four sections separated by `=== HEADER ===` lines:

**DESIGN SUMMARY** — counts, major ICs (>4 pins), power rails.

**COMPONENTS** — one block per component:

```
COMPONENT: U1 | MPN: NXP MIMXRT1062DVL6B | SYMBOL: MIMXRT1062 | Desc: Microcontroller | Pages: RT1062 Core
  mfr: NXP
  Pin 1     JTAG_TDI      -> JTAG_TDI  [J3.3]
  Pin 2     SPI_CLK       -> SPI_CLK   [R33 -> U2.3]  (R50 to P3V3)
  Pin 3     VDD           -> P3V3
  Pin 4     NC            -> (no-connect)
  Pin 5     BOOT_CFG      -> (unconnected)
```

When `MPN:` is present, prefer it over `SYMBOL:` and `Desc:` for identifying
the actual part. `SYMBOL:` is the schematic/library symbol identity and may be
less specific or stale; `Desc:` is descriptive text, not proof of the fitted
part number.

**NETS** — one block per net, all connected pins. `Also:` shows aliases
(same wire, different name on another page).

**VALIDATION** — parser warnings: single-pin nets, unconnected pins, orphan
ports. Data-quality flags, not necessarily design bugs.

## Quick reference

**Ref designator prefixes:** U=IC, R=resistor, C=capacitor, L=inductor,
D=diode, Q=transistor, J=connector, SW=switch, Y=crystal, FB=ferrite bead,
TP=test point.

**Multi-page designs:** CAD-specific scope rules decide cross-page
connectivity. Same net name on multiple pages is a clue, not proof; use `show`
output and occurrence rows when names or pages are ambiguous.

**Page names are organizational, not architectural.** They help humans navigate
the schematic but carry no technical authority. The same subcircuit can be
renamed, split across pages, or merged — only connectivity proves what a
section of the design actually does.

## Exploring a design

A schematic is a connectivity graph. Understanding a design means understanding
which components talk to each other, through what signals, and what crosses
the board boundary. Parts lists and page names are a starting point — not the
answer.

### `list` is discovery, `show` is understanding

`list` commands tell you what exists — names, counts, pages. They are indexes.
You don't understand a connector until you've seen its pinout (`show
component`). You don't understand a page until you've seen what's on it, what
its engineering notes say, and what signals bridge to other pages (`show
page`). You don't understand a net until you've seen every pin on it (`show
net`).

Never write a summary or draw a conclusion from `list` output alone. Every
`list` that surfaces something interesting must be followed by `show` to
understand what it actually is.

### When you are ready to write

You are not ready to write a summary until you can answer these questions from
`show` output — not from `list` output, not from page names, not from
assumptions:

- What does each connector carry? (`show component` on every non-trivial J)
- What is on each page that isn't self-explanatory? (`show page`)
- Which ICs talk to each other, and through what signals? (`list nets -c`,
  `trace`)

If your understanding comes entirely from `list` output, you are not done
investigating. Go back and run the `show` commands.

### Build understanding from connectivity, not metadata

The order matters: boundaries first, then topology, then details. Each step
has two phases — discover (what exists) then understand (how it connects).

**1. System boundaries — connectors and external interfaces**

Connectors (J-prefix) define what enters and leaves the board. They reveal the
system context: what's on-board vs. off-board, what external modules exist,
what signals are meant to be probed/accessed.

```sh
# Discover
phosphor-eda -P <PROJECT> list components --component "J*"

# Understand — for each non-trivial connector:
phosphor-eda -P <PROJECT> show component J8              # what signals does this connector carry?
```

A connector's part number tells you its form factor. Its _pinout_ tells you
its role. A 26-pin board-to-board receptacle is meaningless until you see it
carries LVDS, I2C, and dedicated power rails for a specific subsystem — that
tells you something lives off-board.

**2. Page contents — what's actually on each page**

`overview` gives you the project inventory, documents, schematic pages, boards,
important components, rails, buses, and bounded notes. Use `show page` to see
the actual components, the internal page title (which often preserves design
intent better than the navigation label), engineering notes, and the nets that
cross the page boundary.

```sh
# Discover
phosphor-eda -P <PROJECT> overview

# Understand — for pages that aren't self-explanatory:
phosphor-eda -P <PROJECT> show page "Sensor IO"           # components, page title, notes, nets
```

`show page` surfaces text annotations placed on the schematic sheet —
revision notes, design rationale, change history, and configuration
documentation left by the engineer. These notes often explain _why_ the
design looks the way it does, not just what's connected.

Don't assume a page's role from its name in the TOC. A page named "Lattice
FPGA" might contain interface circuitry that also touches other subsystems.
Read it.

**3. Inter-component topology — who talks to whom**

Use signal nets (not power) to understand which ICs form functional groups:

```sh
# Discover
phosphor-eda -P <PROJECT> list nets -c U5 --no-power     # what signals touch U5?

# Understand
phosphor-eda -P <PROJECT> list nets -c U1 -c U5 --trace  # shared signals between MCU and FPGA
phosphor-eda -P <PROJECT> trace U1 U5                    # direct paths between them
```

This reveals subsystem relationships that page names might obscure. Two
components on different pages sharing many signal nets are tightly coupled.
A component whose signals only reach a connector is an interface to something
off-board.

**4. Power architecture — rails and domains**

```sh
phosphor-eda -P <PROJECT> list nets --power
phosphor-eda -P <PROJECT> list components --page "Power" --no-passive
phosphor-eda -P <PROJECT> show net P3V3                  # what's powered by this rail?
```

Power domains reveal which subsystems are independently powered, which share
rails, and where isolation boundaries exist.

**5. Detailed pinout — when you need specifics**

```sh
phosphor-eda -P <PROJECT> show component U1              # full pinout with traced destinations
phosphor-eda -P <PROJECT> list nets -c U1 --no-power     # signal net summary
```

Cross-reference shunt annotations (`(R50 to P3V3)`) with passive values from
`show component` to understand pull-ups, termination, and biasing.

### Common pitfalls

- **Don't infer architecture from page names.** A page called "ASIC Interface"
  might contain the interface circuitry _to_ an ASIC (level shifters, buffers,
  connectors), not the ASIC itself. Verify by checking what components are
  actually on the page and what they connect to.
- **The BOM alone doesn't explain the design.** A component list tells you
  what parts are on the board, but not how they're used or how they relate to
  each other. The netlist and board hierarchy carry that context — which ICs
  form a subsystem, which signals cross the board boundary, which functions are
  on-board vs. off-board. Always query connectivity before drawing conclusions
  about what a design does or how it changed.
- **A `list` command is not the end of a question, it's the beginning.** If
  you ran `list components --component "J*"` and see a Hirose connector, you still
  don't know what it's for. Run `show component` on it. If you ran
  `list pages` and see "Sensor IO", you still don't know what's on that page.
  Run `show page`. Conclusions require connectivity data, not just existence.
- **If you only ran `list` commands, you are not done.** Running several
  `list` queries in parallel produces a broad index of the design — names,
  counts, page structure. It does not produce understanding. Before writing
  any summary, check: did I run `show` on the connectors, the non-obvious
  pages, and the key ICs? If not, go back.
