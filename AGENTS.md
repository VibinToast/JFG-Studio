# AGENTS.md: JFG-Studio orientation (keep it short)

JFG-Studio is a standalone level and modpack editor for Jet Force Gemini: Recompiled (Python 3.12, imgui-bundle in the
JFG theme, moderngl). It builds `.nrm` packs the port loads. Its own repo: Forgejo `VibinToast/JFG-Studio`, checked
out at `JetForceGemini/JFG-Modding-Tools/JFG-Studio/`.

**Design and plans are not in this repo.** They live in the JFG-Modding repo, one folder up (`../Docs/`):
`JFG_STUDIO_DESIGN.md` (design, decisions, gaps, phases), `PHASE0_TASKS.md` (current tasks and status),
`MODDING_API_PLAN.md` (the port side), `JFG_FORGE_REVIEW.md`. Update them there.

Neighbours in `JetForceGemini/`: the decomp (`Decomp/`, read-only; Studio imports its `tools/jfgformats`), the port
(`Recomp/` and worktrees; phase 0 port build in `C:\jfgs\Recomp`), `Recomp-Docs/`.

## Where things are
| Path | What |
|---|---|
| `jfgstudio/cli.py` | `jfgstudio new / validate / settings / catalog / build / test / gui` |
| `jfgstudio/project.py`, `schemas/project/` | the project folder format and its JSON schemas |
| `jfgstudio/objects.py`, `schemas/objects/` | object-list entries and per-type field schemas (fields, named values, roles, categories) |
| `jfgstudio/prefabs.py` | what the Prefabs window places: single objects and linked groups (squad, exit pair, region) |
| `jfgstudio/agent.py`, `mcp_server.py`, `agent_guide.md` | `jfgstudio mcp`: Studio as tools for AI agents (one `Session` over edit/prefabs/validate/build; stdio MCP; the guide its first tool returns) |
| `jfgstudio/validate.py` | the validator (rules from the decomp's tested pitfalls and its review) |
| `jfgstudio/build.py`, `bps.py` | the pack builder and the map patch encoder |
| `jfgstudio/catalog.py`, `rom.py`, `formats.py`, `settings.py` | the user's ROM, the local catalog, jfgformats, paths |
| `jfgstudio/gui/` | the editor window (Unity-style docked layout) and the theme |
| `samples/` | the sample planet project and the script that draws its geometry |
| `tests/` | unittest, synthetic data only: `python -m unittest discover -s tests -t .` |

## Rules
- **Never commit ROM-derived content**: ROMs, catalogs, exports of game data, built `.nrm` files, decomp code or asm.
  Game names are read from the user's ROM at run time. Tests use synthetic data.
- Our own code only: JFG-Forge and BattleTanx-Studio are references, nothing is copied from them (owner's decision).
- Format code comes from the decomp's `jfgformats` (path setting `decomp`); never fork it here.
- Agents never contact players or modders. Commit author VibinToast, no AI co-author or attribution lines. Ask the
  owner before committing or pushing unless told otherwise. New work on `feature/<name>`.
- Run `ruff check .` and the tests before handing work over; `jfgstudio gui --screenshot x.png` checks the window.
