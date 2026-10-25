# JFG-Studio

Level and modpack editor for **Jet Force Gemini: Recompiled**. Design: [../Docs/JFG_STUDIO_DESIGN.md](../Docs/JFG_STUDIO_DESIGN.md).
Current work: [../Docs/PHASE0_TASKS.md](../Docs/PHASE0_TASKS.md).

## Run from source

```
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]     # Linux: .venv/bin/python
.venv/Scripts/python -m jfgstudio                  # the editor
.venv/Scripts/python -m jfgstudio --help           # the command line
.venv/Scripts/python -m unittest discover -s tests # tests (no ROM needed)
```

## Settings

`jfgstudio settings` shows them; `jfgstudio settings set <key> <path>` sets one. Kept in the user's config folder,
never in a project.

| Key | What |
|---|---|
| `decomp` | the decomp checkout; Studio imports `tools/jfgformats` from it |
| `port` | the Jet Force Gemini: Recompiled folder (for fonts, `bin/mods` and test runs) |
| `rom` | your own US ROM (used only to build the local catalog) |
