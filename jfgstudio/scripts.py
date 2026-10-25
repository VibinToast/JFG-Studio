"""Scripts and triggers (phase 3, P8 / S34): a pack's Lua scripts for the port, and the triggers non-programmers set up
in the editor, compiled to one more script.

    scripts/<name>.lua      the project's own Lua scripts (the port's API: jfg.on, jfg.message, jfg.fog ...)
    triggers.json           {"format": 1, "triggers": [...]}: "when ... then ..." rules, compiled to _triggers.lua
    weapons.json            {"format": 1, "weapons": {"<0-15>": {field: value}}}: the pack's values for the heroes'
                            weapons (jfg.weapon), compiled to _weapons.lua; the player's own Weapons tab still wins

A trigger:
    {"name": "...", "level": "pack:level/<name>" | "stock:level/<n>",
     "when": {"kind": "enter", "level start" | "flag" | "door opens",
              "box": [x0, z0, x1, z1] (enter: the hero's x/z inside it), "flag": n (flag: when it gets set),
              "door": id (door opens: the door with that id opens after it was seen locked: its squadrons are
              dead, its key was used)},
     "once": true,
     "then": [{"do": "message", "text": "...", "seconds": 3},
              {"do": "music", "track": "<music/ name>"}, {"do": "music_stop"},
              {"do": "fog", "rgb": [r, g, b], "near": 990, "far": 1022, "ticks": 60},
              {"do": "flag", "flag": n, "on": true}, {"do": "unlock_door", "door": id},
              {"do": "warp", "level": "pack:level/<name>", "start": 0},
              {"do": "spawn", "type": "stock:objtype/<id>", "pos": [x, y, z], "near_player": false,
               "fields": {<the type's schema fields>}}]}
A spawn makes the object as if it were in the level's list (pickups, crates, props). With "near_player", "pos" is an
offset from the hero. Spawned objects are not saved: don't spawn types with saved flags (squadrons, crates that
remember being opened); enemies belong in squads.
"""

import json
from pathlib import Path

from jfgstudio import refs

WHEN = ("level start", "enter", "flag", "door opens")
DO = ("message", "music", "music_stop", "fog", "flag", "unlock_door", "warp", "spawn")
NO_SPAWN = {5, 0x60, 0x61, 0x69}  # region and patrol data: the game refuses them


# The weapon fields a pack can set (the port's jfg.weapon), with their ranges. The decomp's mod #6 "The heroes'
# weapons": damage is the projectile's power, delay the frames between shots, lifetime the projectile's life, spread in
# angle units (65536 = a full turn), speed the projectile's speed. throw_delay only counts for thrown weapons.
WEAPON_FIELDS = {"damage": (0, 255), "delay": (0, 255), "throw_delay": (1, 127), "lifetime": (1, 255),
                 "spread_x": (0, 8192), "spread_y": (0, 8192), "speed": (0.1, 2000.0)}
WEAPON_COUNT = 16


def weapons_path(project) -> Path:
    return project.path / "weapons.json"


def load_weapons(project) -> dict[str, dict]:
    path = weapons_path(project)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("weapons", {})


def save_weapons(project, weapons: dict[str, dict]) -> None:
    from jfgstudio.project import write_json
    path = weapons_path(project)
    weapons = {k: v for k, v in sorted(weapons.items(), key=lambda kv: int(kv[0])) if v}
    if weapons:
        write_json(path, {"format": 1, "weapons": weapons})
    elif path.exists():
        path.unlink()


def compile_weapons(weapons: dict[str, dict]) -> str:
    lines = ["-- Made by JFG-Studio from weapons.json: edit the weapons in the editor, not this file."]
    for n, fields in sorted(weapons.items(), key=lambda kv: int(kv[0])):
        if fields:
            body = ", ".join(f"{k} = {v}" for k, v in sorted(fields.items()))
            lines.append(f"jfg.weapon({int(n)}, {{{body}}})")
    return "\n".join(lines) + "\n"


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
    if kind == "spawn":
        return _spawn(a)
    raise ValueError(f"unknown action {kind!r}")


def spawn_entry(a: dict) -> tuple[int, bytes]:
    """A spawn action's object id and its entry's parameter bytes (from 0x0A)."""
    from jfgstudio import objects as obj
    r = refs.parse(a.get("type", ""), "objtype")
    if not r.is_stock:
        raise ValueError("spawn: only stock object types can be spawned (a kind's id is given at load)")
    oid = r.number()
    if oid in NO_SPAWN:
        raise ValueError(f"spawn: object {oid} is region or patrol data, the game refuses it")
    schema = obj.schema_for_id(oid, obj.load_schemas())
    size = schema.min_size if schema else 0x10
    size += size & 1
    fields = {**(schema.defaults() if schema else {}), **a.get("fields", {})}
    return oid, obj.encode_entry(oid, (0, 0, 0), size, schema, fields)[obj.PARAMS_OFFSET:]


def _spawn(a: dict) -> str:
    oid, tail = spawn_entry(a)
    x, y, z = (float(v) for v in a.get("pos", (0, 0, 0)))
    params = "{" + ", ".join(str(b) for b in tail) + "}"
    if a.get("near_player"):
        return (f"do local px, py, pz = jfg.player(); if px then jfg.spawn({oid}, px + {x}, py + {y}, pz + {z}, "
                f"{params}) end end")
    return f"jfg.spawn({oid}, {x}, {y}, {z}, {params})"


def compile_triggers(triggers: list[dict], project=None) -> str:
    """The triggers as one Lua script for the port."""
    lines = ["-- Made by JFG-Studio from triggers.json: edit the triggers in the editor, not this file.",
             "local fired = {}", "local seen = {}", "local levels = {}", ""]
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
        elif kind == "door opens":
            inner = "\n".join("  " + line for line in body.split("\n"))
            on_tick.append(f"{header}\n    if not fired[{i}] and level == levels[{i}]() then\n"
                           f"      local locked = jfg.door_locked({int(when['door'])})\n"
                           f"      if locked then seen[{i}] = true\n"
                           f"      elseif locked == false and seen[{i}] then\n        fired[{i}] = true\n"
                           f"{inner}\n      end\n    end")
        elif kind == "flag":
            on_tick.append(f"{header}\n    if {guard}level == levels[{i}]() and jfg.flag({int(when['flag'])}) "
                           f"then\n{once}{body}\n    end")
        else:
            raise ValueError(f"trigger {k + 1}: unknown 'when' {kind!r}")
    if on_level:
        lines += ["jfg.on(\"level\", function(level)", "  fired = {}", "  seen = {}", *on_level, "end)", ""]
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
    weapons = load_weapons(project)
    if any(weapons.values()):
        out["scripts/_weapons.lua"] = compile_weapons(weapons).encode("utf-8")
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
            elif a["do"] == "spawn":
                try:
                    spawn_entry(a)
                except (ValueError, refs.RefError) as e:
                    out.append(f"{where}: {e}")
    for n, fields in load_weapons(project).items():
        if not n.isdigit() or not 0 <= int(n) < WEAPON_COUNT:
            out.append(f"weapons.json: weapon {n!r} is not 0-{WEAPON_COUNT - 1}")
            continue
        for k, v in fields.items():
            if k not in WEAPON_FIELDS:
                out.append(f"weapons.json: weapon {n}: unknown field {k!r}")
            elif not isinstance(v, (int, float)) or not WEAPON_FIELDS[k][0] <= v <= WEAPON_FIELDS[k][1]:
                lo, hi = WEAPON_FIELDS[k]
                out.append(f"weapons.json: weapon {n}: {k} = {v!r} is outside {lo}..{hi}")
    return out
