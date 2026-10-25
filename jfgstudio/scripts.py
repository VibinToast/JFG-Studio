"""Scripts and triggers (phase 3, P8 / S34): a pack's Lua scripts for the port, and the triggers non-programmers set up
in the editor, compiled to one more script.

    scripts/<name>.lua      the project's own Lua scripts (the port's API: jfg.on, jfg.message, jfg.fog ...)
    triggers.json           {"format": 1, "triggers": [...]}: "when ... then ..." rules, compiled to _triggers.lua

A trigger:
    {"name": "...", "level": "pack:level/<name>" | "stock:level/<n>",
     "when": {"kind": "enter", "level start" | "flag",
              "box": [x0, z0, x1, z1] (enter: the hero's x/z inside it), "flag": n (flag: when it gets set)},
     "once": true,
     "then": [{"do": "message", "text": "...", "seconds": 3},
              {"do": "music", "track": "<music/ name>"}, {"do": "music_stop"},
              {"do": "fog", "rgb": [r, g, b], "near": 990, "far": 1022, "ticks": 60},
              {"do": "flag", "flag": n, "on": true}, {"do": "unlock_door", "door": id},
              {"do": "warp", "level": "pack:level/<name>", "start": 0}]}
"""

import json
from pathlib import Path

from jfgstudio import refs

WHEN = ("level start", "enter", "flag")
DO = ("message", "music", "music_stop", "fog", "flag", "unlock_door", "warp")


def triggers_path(project) -> Path:
    return project.path / "triggers.json"


def load_triggers(project) -> list[dict]:
    path = triggers_path(project)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("triggers", [])


def save_triggers(project, triggers: list[dict]) -> None:
    from jfgstudio.project import write_json
    write_json(triggers_path(project), {"format": 1, "triggers": triggers})


def _lua_str(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _level_expr(ref: str, project=None) -> str:
    r = refs.parse(ref, "level")
    if r.is_stock:
        return str(r.number())
    level = project.levels.get(r.name) if project else None
    fixed = None if level is None else level.base if level.base is not None else level.level.get("fixedId")
    if fixed is not None and fixed < 412:  # a stock level's number (an edit, or a fixedId replacing one)
        return str(fixed)
    return f'jfg.level_id({_lua_str("level_" + r.name)})'


def _action(a: dict, project) -> str:
    kind = a.get("do")
    if kind == "message":
        return f"jfg.message({_lua_str(a.get('text', ''))}, {float(a.get('seconds', 3))})"
    if kind == "music":
        return f"jfg.music({_lua_str(a['track'])})"
    if kind == "music_stop":
        return "jfg.music_stop()"
    if kind == "fog":
        r, g, b = (int(v) for v in a.get("rgb", (0, 0, 0)))
        near, far, ticks = int(a.get("near", 990)), int(a.get("far", 1022)), float(a.get("ticks", 60))
        return f"jfg.fog({r}, {g}, {b}, {near}, {far}, {ticks})"
    if kind == "flag":
        return f"jfg.set_flag({int(a['flag'])}, {'true' if a.get('on', True) else 'false'})"
    if kind == "unlock_door":
        return f"jfg.unlock_door({int(a['door'])})"
    if kind == "warp":
        return f"jfg.warp({_level_expr(a['level'], project)}, {int(a.get('start', 0))})"
    raise ValueError(f"unknown action {kind!r}")


def compile_triggers(triggers: list[dict], project=None) -> str:
    """The triggers as one Lua script for the port."""
    lines = ["-- Made by JFG-Studio from triggers.json: edit the triggers in the editor, not this file.",
             "local fired = {}", "local levels = {}", ""]
    for k, t in enumerate(triggers):
        lines.append(f"levels[{k + 1}] = function() return {_level_expr(t['level'], project)} end")
    lines.append("")
    on_level, on_tick = [], []
    for k, t in enumerate(triggers):
        i = k + 1
        body = "\n".join("      " + _action(a, project) for a in t.get("then", [])) or "      -- (no actions)"
        once = f"      fired[{i}] = true\n" if t.get("once", True) else ""
        guard = f"not fired[{i}] and " if t.get("once", True) else ""
        when = t.get("when", {})
        kind = when.get("kind", "level start")
        header = f"    -- {k + 1}. {t.get('name') or kind}"
        if kind == "level start":
            on_level.append(f"{header}\n    if {guard}level == levels[{i}]() then\n{once}{body}\n    end")
        elif kind == "enter":
            x0, z0, x1, z1 = (float(v) for v in when["box"])
            xa, xb, za, zb = min(x0, x1), max(x0, x1), min(z0, z1), max(z0, z1)
            on_tick.append(f"{header}\n    if {guard}level == levels[{i}]() and x and x >= {xa} and x <= {xb} "
                           f"and z >= {za} and z <= {zb} then\n{once}{body}\n    end")
        elif kind == "flag":
            on_tick.append(f"{header}\n    if {guard}level == levels[{i}]() and jfg.flag({int(when['flag'])}) "
                           f"then\n{once}{body}\n    end")
        else:
            raise ValueError(f"trigger {k + 1}: unknown 'when' {kind!r}")
    if on_level:
        lines += ["jfg.on(\"level\", function(level)", "  fired = {}", *on_level, "end)", ""]
    if on_tick:
        lines += ["jfg.on(\"tick\", function(level, seconds)", "  local x, y, z = jfg.player()", *on_tick, "end)", ""]
    return "\n".join(lines) + "\n"


def pack_scripts(project) -> dict[str, bytes]:
    """The pack's script files: scripts/*.lua, and scripts/_triggers.lua from the triggers."""
    out = {}
    folder = project.path / "scripts"
    if folder.is_dir():
        for f in sorted(folder.glob("*.lua")):
            out[f"scripts/{f.name}"] = f.read_bytes()
    triggers = load_triggers(project)
    if triggers:
        out["scripts/_triggers.lua"] = compile_triggers(triggers, project).encode("utf-8")
    return out


def check(project) -> list[str]:
    """Problems with the triggers (the validator's "triggers" rule)."""
    out = []
    levels = set(project.levels)
    tracks = {f.stem for f in (project.path / "music").glob("*.ogg")}
    for k, t in enumerate(load_triggers(project)):
        where = f"trigger {k + 1} ({t.get('name') or 'unnamed'})"
        try:
            r = refs.parse(t.get("level", ""), "level")
            if r.is_pack and r.name not in levels:
                out.append(f"{where}: the project has no level {r.name}")
        except refs.RefError as e:
            out.append(f"{where}: {e}")
        when = t.get("when", {})
        if when.get("kind", "level start") not in WHEN:
            out.append(f"{where}: unknown 'when' {when.get('kind')!r}")
        if when.get("kind") == "enter" and len(when.get("box", [])) != 4:
            out.append(f"{where}: an 'enter' trigger needs a box [x0, z0, x1, z1]")
        for a in t.get("then", []):
            if a.get("do") not in DO:
                out.append(f"{where}: unknown action {a.get('do')!r}")
            elif a["do"] == "music" and a.get("track") not in tracks:
                out.append(f"{where}: music/{a.get('track')}.ogg does not exist")
    return out
