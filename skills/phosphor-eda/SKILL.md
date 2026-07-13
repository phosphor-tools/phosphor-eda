---
name: phosphor-eda
description: Query electronic schematics and PCB layouts with Phosphor EDA. Use when working with Altium, KiCad, Eagle, OrCAD, or Allegro designs to inspect projects, trace signals, identify components, examine connectivity, query design data, or render boards.
---

# Phosphor EDA

Use the installed Phosphor EDA CLI to work with the electronic design.

## Load the CLI instructions

1. Run `phosphor-eda --skill`.
2. Treat its output as the authoritative instructions for the installed CLI version.
3. Follow those instructions for the rest of the task.

If `phosphor-eda` is unavailable, tell the user to install it with:

```shell
uv tool install phosphor-eda
```

If the installed CLI does not support `--skill`, tell the user to update it with:

```shell
uv tool upgrade phosphor-eda
```

Do not install or upgrade the CLI without the user's permission.
