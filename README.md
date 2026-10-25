# JFG-Studio

A standalone level and modpack editor for **Jet Force Gemini: Recompiled**. It builds `.nrm` packs the game loads
from its Mods list. You need your own US ROM; nothing from the game is in this repository or in what it ships.

The design and the plans are in the JFG-Modding repo (`Docs/`): `JFG_STUDIO_DESIGN.md`, `PHASE0_TASKS.md`,
`MODDING_API_PLAN.md`. In the usual layout this repo sits in `JFG-Modding-Tools/JFG-Studio`, so they are at
[../Docs](../Docs).

## Commands

| Command | What |
|---|---|
| `jfgstudio` / `jfgstudio gui [project]` | the editor |
| `jfgstudio new <folder> --id <mod id> --name <name>` | an empty project |
| `jfgstudio validate <project>` | checks a project against the game's rules |
| `jfgstudio catalog` | decodes your ROM into the local catalog (your data folder) |
| `jfgstudio build <project> [-o pack.nrm]` | builds the pack |
| `jfgstudio test <project>` | builds, installs into the port's `mods` and starts the game in the level |

The sample planet: `jfgstudio test samples/gemini.jfgproj` (its ground is drawn by `samples/make_gemini_field.py`).

## Run from source

```
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]     # Linux: .venv/bin/python
.venv/Scripts/python -m jfgstudio                  # the editor
.venv/Scripts/python -m jfgstudio --help           # the command line
.venv/Scripts/python -m unittest discover -s tests -t .   # tests (no ROM needed)
```

## Settings

`jfgstudio settings` shows them; `jfgstudio settings set <key> <path>` sets one. Kept in the user's config folder,
never in a project.

| Key | What |
|---|---|
| `decomp` | the decomp checkout; Studio imports `tools/jfgformats` from it |
| `port` | the Jet Force Gemini: Recompiled folder (for fonts, `bin/mods` and test runs) |
| `rom` | your own US ROM (used only to build the local catalog) |
