"""`jfgstudio mcp`: JFG-Studio as an MCP server (Model Context Protocol, stdio), so an AI agent can make levels with it.

The tools are agent.Session's calls. JSON-RPC 2.0, one message per line on stdin and stdout; nothing else may reach
stdout, so whatever the tools print goes to stderr. Written here rather than with an MCP library: the protocol's stdio
part is small and Studio keeps its dependencies few.
"""

import base64
import contextlib
import copy
import json
import sys
import traceback

from jfgstudio import __version__, agent
from jfgstudio import project as proj

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("JFG-Studio makes levels and modpacks for Jet Force Gemini: Recompiled. Call guide first (workflow, "
                "units, limits, gameplay patterns) and design_guide before laying out a level (outdoor and indoor "
                "levels as the game builds them), then open_project or new_project and project_summary. Edits are "
                "saved as they are made and can be undone per level. Run validate before play.")

_POS = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3,
        "description": "[x, y, z], or [x, z] for the floor there (game units, Y up)"}
_XZ = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_LEVEL = {"type": "string", "description": "a level of the project, by name"}
_TYPE = {"type": ["string", "integer"],
         "description": "a schema key (exit, light, ...), a stock object id (0x29) or pack:kind/<name>"}
_EXAMPLE = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2,
            "description": "[object list, entry]: the stock entry a not fully decoded type starts from (the catalog "
                           "picks one when left out)"}


_RGB = {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3}
_FACING = {"type": "number", "description": "facing in degrees, 0 = towards -z (as start points face); written into "
                                            "the type's yaw field in its own angle steps"}


def _doc_schema(name: str) -> dict:
    """A project file's JSON schema as a tool's input: the agent sees every key and what it means ("format" is added
    for it)."""
    doc = copy.deepcopy(proj._SCHEMAS[name])
    for k in ("$schema", "title"):
        doc.pop(k, None)
    doc["properties"].pop("format", None)
    doc["required"] = [r for r in doc.get("required", []) if r != "format"]
    return doc


def _tool(name, description, run, properties=None, required=()):
    return {"name": name, "description": description, "run": run,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required),
                            "additionalProperties": False}}


TOOLS = [
    _tool("guide", "How to make levels with these tools: workflow, units, limits, objects, gameplay patterns. Read it "
          "first.", lambda s, a: agent.guide()),
    _tool("design_guide", "Level design: how the game lays out outdoor and indoor levels (its numbers), room-graph "
          "templates and the process. Read it before laying out a level.", lambda s, a: agent.design_guide()),
    _tool("open_project", "Open a .jfgproj folder.", lambda s, a: s.open(a["path"]),
          {"path": {"type": "string"}}, ["path"]),
    _tool("new_project", "Create an empty project folder and open it.",
          lambda s, a: s.new(a["path"], a["mod_id"], a["name"], a.get("author", "")),
          {"path": {"type": "string", "description": "folder to create, e.g. C:/mods/my_world.jfgproj"},
           "mod_id": {"type": "string", "description": "e.g. yourname.my_world"},
           "name": {"type": "string"}, "author": {"type": "string"}}, ["path", "mod_id", "name"]),
    _tool("project_summary", "Levels, planets, galaxies, kinds, geometry and music files of the open project.",
          lambda s, a: s.summary()),
    _tool("object_types", "The object-type schemas, and the game's own object types (from the player's catalog) "
          "matching a name search or behaviour.",
          lambda s, a: s.object_types(a.get("search"), a.get("behaviour")),
          {"search": {"type": "string"}, "behaviour": {"type": "integer"}}),
    _tool("describe_type", "One object type: its fields (ranges, named values, links), status and stock examples.",
          lambda s, a: s.describe_type(a["type"]), {"type": _TYPE}, ["type"]),
    _tool("level_info", "A level's settings (level.json) and its objects.", lambda s, a: s.level_info(a["level"]),
          {"level": _LEVEL}, ["level"]),
    _tool("import_geometry", "Copy a .glb (from Blender) into the project and report what the game will see.",
          lambda s, a: s.import_geometry(a["path"], a.get("name")),
          {"path": {"type": "string"}, "name": {"type": "string", "description": "geometry name (default: the "
                                                                                 "file's)"}}, ["path"]),
    _tool("reimport_geometry", "Replace a level's geometry with a new export of it (undoable). Its objects stay; the "
          "result lists those the new shape left buried, floating, sunk or over a pit.",
          lambda s, a: s.reimport_geometry(a["level"], a["path"]),
          {"level": _LEVEL, "path": {"type": "string", "description": "the new .glb"}}, ["level", "path"]),
    _tool("game_textures", "The game's own level textures, from the player's ROM: a level painted with them carries "
          "none (each player's game shows them). Filter by colour words, a game level or a size; each comes with "
          "the levels that use it and its PNG path for Blender (jfg64.game_texture_material).",
          lambda s, a: s.game_textures(a.get("words"), a.get("level"), a.get("size"), a.get("limit", 40)),
          {"words": {"type": "array", "items": {"type": "string"},
                     "description": "all of: a colour (green, brown, grey, blue, cyan, orange, yellow, red, pink, "
                                    "purple, white, black) and dark / mid / bright / vivid"},
           "level": {"type": "integer", "description": "a game level id (stock_levels) that uses it"},
           "size": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
           "limit": {"type": "integer"}}, []),
    _tool("stock_level_textures", "What a game level is made of: its textures by the share of the level they cover, "
          "each mostly floor, wall or ceiling, with a picture. For a theme (\"a forest level\"), look at the game's "
          "levels of that kind and use their textures.",
          lambda s, a: s.stock_level_textures(a["level"], a.get("limit", 24)),
          {"level": {"type": "integer", "description": "a game level id (stock_levels)"},
           "limit": {"type": "integer"}}, ["level"]),
    _tool("stock_level_layout", "How a game level is laid out: a floor map from above (brighter = higher, darker = "
          "roofed, orange = narrow passages) and its numbers (footprint, roofed share, floor heights and tiers, "
          "passages / rooms / open ground, passage width, clearance). Study the game's levels before designing.",
          lambda s, a: s.stock_level_layout(a["level"]),
          {"level": {"type": "integer", "description": "a game level id (stock_levels)"}}, ["level"]),
    _tool("level_layout", "The same floor map and numbers for a level of the project, to compare with the game's.",
          lambda s, a: s.level_layout(a["level"]), {"level": _LEVEL}, ["level"]),
    _tool("texture_sheet", "A picture of game textures side by side, each id under it.",
          lambda s, a: s.texture_sheet(a["ids"]),
          {"ids": {"type": "array", "items": {"type": "string"}, "description": "hex ids, as game_textures gives"}},
          ["ids"]),
    _tool("level_preview", "A top-down picture of a level: the geometry shaded by height and coloured by surface, with "
          "start points, exits (their radius), squads and their regions, doors (across their facing), keys and "
          "pickups drawn on (an edit of a game level: that level's own start points and exits, grey). Up is -z. "
          "Look at it after laying a level out.",
          lambda s, a: s.preview(a["level"], a.get("size", 512), a.get("out"), a.get("colours", False)),
          {"level": _LEVEL, "size": {"type": "integer", "description": "pixels across (default 512, at most 2048)"},
           "colours": {"type": "boolean", "description": "the baked vertex colours (the level's mood) instead of "
                                                         "the surface kinds"},
           "out": {"type": "string", "description": "also save the PNG here"}}, ["level"]),
    _tool("check_placement", "A level's objects that should stand on the floor but are buried, floating, sunk or over "
          "a pit, with where each would stand.", lambda s, a: s.check_placement(a["level"]),
          {"level": _LEVEL}, ["level"]),
    _tool("settle_objects", "Put objects back on the floor where check_placement found one (all of them, or uids), "
          "as one undo step.", lambda s, a: s.settle_objects(a["level"], a.get("uids")),
          {"level": _LEVEL, "uids": {"type": "array", "items": {"type": "string"}}}, ["level"]),
    _tool("geometry_info", "Bounds, triangles, surfaces and images of a geometry file or a level's track, against "
          "the game's limits.", lambda s, a: s.geometry_info(a["name"]),
          {"name": {"type": "string", "description": "a level or a geometry name"}}, ["name"]),
    _tool("surface_at", "For points [x, z] of a level: the floor height and kind, water depth, kill floors.",
          lambda s, a: s.surface_at(a["level"], a["points"], a.get("below")),
          {"level": _LEVEL, "points": {"type": "array", "items": _XZ},
           "below": {"type": "number", "description": "only floors at or below this height (under a bridge)"}},
          ["level", "points"]),
    _tool("find_flat", "Dry, level spots near (x, z) big enough for something of `radius`, nearest first.",
          lambda s, a: s.find_flat(a["level"], a["x"], a["z"], a.get("radius", 60), a.get("search", 600),
                                   a.get("step", 50), a.get("tolerance", 8), a.get("count", 5)),
          {"level": _LEVEL, "x": {"type": "number"}, "z": {"type": "number"}, "radius": {"type": "number"},
           "search": {"type": "number"}, "step": {"type": "number"}, "tolerance": {"type": "number"},
           "count": {"type": "integer"}}, ["level", "x", "z"]),
    _tool("raycast", "Where a ray first hits a level's solid geometry.",
          lambda s, a: s.raycast(a["level"], a["origin"], a["direction"]),
          {"level": _LEVEL, "origin": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
           "direction": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
          ["level", "origin", "direction"]),
    _tool("add_level", "A new level of the pack's own on a geometry file of the project.",
          lambda s, a: s.add_level(a["name"], a["display_name"], a["geometry"], a.get("planet")),
          {"name": {"type": "string", "description": "lower case, digits, _ and -"},
           "display_name": {"type": "string", "description": "the name the game shows (31 characters)"},
           "geometry": {"type": "string", "description": "a geometry name, or stock:track/<n>"},
           "planet": {"type": "string"}}, ["name", "display_name", "geometry"]),
    _tool("set_level", "Change a level's settings: name, track, sky, music (pack:music/<name>), planet, ambient, and "
          "header fields by name in \"header\" (fogNear, fogFar, fogR/G/B, tune ...). null removes one.",
          lambda s, a: s.set_level(a["level"], a["values"]), {"level": _LEVEL, "values": {"type": "object"}},
          ["level", "values"]),
    _tool("place", "Place one object, on the floor by default.",
          lambda s, a: s.place(a["level"], a["type"], a["pos"], a.get("fields"), a.get("name"),
                               a.get("on_floor", True), a.get("lift", 0), a.get("example"), a.get("target"),
                               a.get("facing")),
          {"level": _LEVEL, "type": _TYPE, "pos": _POS, "facing": _FACING,
           "fields": {"type": "object", "description": "field: number or named value"},
           "name": {"type": "string"},
           "on_floor": {"type": "boolean", "description": "drop onto the floor (with [x, y, z]: the floor at or "
                                                          "below y). Default true"},
           "lift": {"type": "number", "description": "units above where it lands (lights, glows)"},
           "example": _EXAMPLE, "target": {"type": "string", "description": "an exit's level: pack:level/<name> or "
                                                                            "stock:level/<n>"}},
          ["level", "type", "pos"]),
    _tool("place_squad", "A squad: a squadron, its members around centre, the region they keep to and, with door, "
          "a door it keeps locked until they are all dead (\"new\" with door_type and door_pos, or a door's uid).",
          lambda s, a: s.place_squad(a["level"], a["centre"], a["member_type"], a.get("members", 3), a.get("region"),
                                     a.get("door"), a.get("door_type"), a.get("door_pos"), a.get("door_fields"),
                                     a.get("spread", 150), a.get("name"), a.get("squadron_example"),
                                     a.get("member_example")),
          {"level": _LEVEL, "centre": _XZ, "member_type": _TYPE, "members": {"type": "integer"},
           "region": {"type": "array", "items": _XZ, "description": "corners in order (default: a square 300 "
                                                                   "around the centre)"},
           "door": {"type": "string"}, "door_type": _TYPE, "door_pos": _POS, "door_fields": {"type": "object"},
           "spread": {"type": "number"}, "name": {"type": "string"}, "squadron_example": _EXAMPLE,
           "member_example": _EXAMPLE}, ["level", "centre", "member_type"]),
    _tool("kind_slots", "The texture slots a reskin (kind) of a game type paints: each slot's size and whether the "
          "base image there has transparency, and the base model's size.",
          lambda s, a: s.kind_slots(a["base"]), {"base": _TYPE}, ["base"]),
    _tool("make_kind", "A reskinned type (kind): a game type with a model, under the pack's own name, size and "
          "textures; its animations and behaviour stay the base's. textures: slot -> a PNG of exactly that slot's "
          "size, or paint them with paint_kind_texture. Making one that exists updates it. Place it as "
          "pack:kind/<name>, e.g. as place_squad's member_type.",
          lambda s, a: s.make_kind(a["name"], a["display_name"], a["base"], a.get("scale", 1.0), a.get("textures")),
          {"name": {"type": "string"}, "display_name": {"type": "string"}, "base": _TYPE,
           "scale": {"type": "number", "description": "size against the base (1 = the same, at most 8)"},
           "textures": {"type": "object", "description": "slot number -> PNG file path"}},
          ["name", "display_name", "base"]),
    _tool("paint_kind_texture", "Draw a kind's textures (all its slots, or slots) at each slot's size from colours and "
          "a pattern: solid, noise, stripes, spots or heat. The textures are the pack's own; nothing of the game's "
          "images is used.",
          lambda s, a: s.paint_kind_texture(a["name"], a["colour"], a.get("accent"), a.get("pattern", "noise"),
                                            a.get("slots"), a.get("seed", 0)),
          {"name": {"type": "string"}, "colour": _RGB, "accent": _RGB,
           "pattern": {"type": "string", "enum": ["solid", "noise", "stripes", "spots", "heat"]},
           "slots": {"type": "array", "items": {"type": "integer"}}, "seed": {"type": "integer"}},
          ["name", "colour"]),
    _tool("place_key_door", "A door that needs a coloured key, and the key (on the floor at key_pos; in key_level if "
          "given). The colour is the first one the pack's doors don't use yet, unless colour names one.",
          lambda s, a: s.place_key_door(a["level"], a["door_type"], a["door_pos"], a["key_pos"], a.get("colour"),
                                        a.get("facing"), a.get("key_level")),
          {"level": _LEVEL, "door_type": _TYPE, "door_pos": _POS, "key_pos": _POS,
           "colour": {"type": ["string", "integer"], "description": "a key's name (\"red key\") or keyLock number"},
           "facing": _FACING, "key_level": _LEVEL}, ["level", "door_type", "door_pos", "key_pos"]),
    _tool("place_switch_door", "A shootable switch that keeps a door locked until the player shoots it: door \"new\" "
          "(with door_type and door_pos) or a door's uid. Shot, it opens the door and sets one of the pack's game "
          "flags (handed out from 103), so the door stays open after a reload; saved false: the door locks again on "
          "reload.",
          lambda s, a: s.place_switch_door(a["level"], a["switch_pos"], a.get("door"), a.get("door_type"),
                                           a.get("door_pos"), a.get("saved", True), a.get("flag"),
                                           a.get("switch_facing"), a.get("door_facing"), a.get("switch_lift", 0)),
          {"level": _LEVEL, "switch_pos": _POS, "door": {"type": "string"}, "door_type": _TYPE, "door_pos": _POS,
           "saved": {"type": "boolean"}, "flag": {"type": "integer", "description": "a pack flag, 103-127"},
           "switch_facing": _FACING, "door_facing": _FACING,
           "switch_lift": {"type": "number", "description": "the switch's height over the floor"}},
          ["level", "switch_pos"]),
    _tool("place_patrol", "A patrol route for squads: nodes at points, linked in order both ways (a chain is walked "
          "there and back; loop joins the ends into a ring). With squadron (its uid) the squadron patrols it: it "
          "starts from the node nearest to itself, so keep it next to the route.",
          lambda s, a: s.place_patrol(a["level"], a["points"], a.get("loop", False), a.get("squadron"),
                                      a.get("wait")),
          {"level": _LEVEL, "points": {"type": "array", "items": _POS, "minItems": 2},
           "loop": {"type": "boolean"}, "squadron": {"type": "string"},
           "wait": {"type": "integer", "description": "wait at each node (stock 15)"}}, ["level", "points"]),
    _tool("add_cover", "Make the obstacles inside a squad's region (blocks, crates, pillars) holes of it. Smart "
          "squads move only to cover (the corners of holes and inward corners of their region): in a plain "
          "rectangle over open floor they stand and fire (validate: squad-cover). place_squad does this itself.",
          lambda s, a: s.add_cover(a["level"], a["squadron"]),
          {"level": _LEVEL, "squadron": {"type": "string", "description": "the squadron's uid"}},
          ["level", "squadron"]),
    _tool("fix_squad", "Mend a squadron: point it (and its members) at a fitting stock squad of its own enemy, keeping "
          "its fields; make it spawn at once; wind its region counter-clockwise. For squads that stand still, never "
          "turn or never appear (validate: squad-stock, squad-trigger, region-winding).",
          lambda s, a: s.fix_squad(a["level"], a["uid"]), {"level": _LEVEL, "uid": {"type": "string"}},
          ["level", "uid"]),
    _tool("connect", "An exit in one level and the start point it brings the player to in another: a new one at "
          "arrive_pos (numbered clear of the level's own and, for an edit of a game level, the game level's), or an "
          "existing one with arrive_start_point.",
          lambda s, a: s.connect(a["from_level"], a["exit_pos"], a["to_level"], a.get("arrive_pos"),
                                 a.get("arrive_yaw", 0), a.get("radius", 70), a.get("name"),
                                 a.get("arrive_start_point")),
          {"from_level": _LEVEL, "exit_pos": _POS, "to_level": _LEVEL, "arrive_pos": _POS,
           "arrive_yaw": {"type": "number", "description": "facing in degrees, 0 = towards -z"},
           "radius": {"type": "integer"}, "name": {"type": "string"},
           "arrive_start_point": {"type": "integer", "description": "arrive at this existing start point instead"}},
          ["from_level", "exit_pos", "to_level"]),
    _tool("update_object", "Change an object's fields (merged), position, name or exit target.",
          lambda s, a: s.update_object(a["level"], a["uid"], a.get("fields"), a.get("pos"), a.get("on_floor"),
                                       a.get("name"), a.get("target"), a.get("facing")),
          {"level": _LEVEL, "uid": {"type": "string"}, "fields": {"type": "object"}, "pos": _POS,
           "on_floor": {"type": "boolean"}, "name": {"type": "string"}, "target": {"type": "string"},
           "facing": _FACING},
          ["level", "uid"]),
    _tool("delete_objects", "Delete objects by uid.", lambda s, a: s.delete_objects(a["level"], a["uids"]),
          {"level": _LEVEL, "uids": {"type": "array", "items": {"type": "string"}}}, ["level", "uids"]),
    _tool("undo", "Undo a level's last change.", lambda s, a: s.undo(a["level"]), {"level": _LEVEL}, ["level"]),
    _tool("redo", "Redo a level's last undone change.", lambda s, a: s.redo(a["level"]), {"level": _LEVEL},
          ["level"]),
    _tool("set_planet", "Write a planet (planets/<name>.json). On a galaxy of the pack's own, leave unlock out (or "
          "\"start\") so it shows from the start: the player reaches the galaxy with L / R on the planet map. "
          "The guide's \"Planets and galaxies\" explains disc, routes and unlock.",
          lambda s, a: s.set_planet(a["name"], a["planet"]),
          {"name": {"type": "string"}, "planet": _doc_schema("planet")}, ["name", "planet"]),
    _tool("set_galaxy", "Write a galaxy of the pack's own (galaxies/<name>.json).",
          lambda s, a: s.set_galaxy(a["name"], a["galaxy"]),
          {"name": {"type": "string"}, "galaxy": _doc_schema("galaxy")}, ["name", "galaxy"]),
    _tool("edit_stock_level", "A project level that adds objects to one of the game's own levels (e.g. a portal exit "
          "into the pack): what it holds joins that level when the pack loads.",
          lambda s, a: s.edit_stock_level(a["name"], a["level_id"]),
          {"name": {"type": "string", "description": "the project's name for it"},
           "level_id": {"type": "integer", "description": "the stock level (stock_levels)"}}, ["name", "level_id"]),
    _tool("stock_levels", "The game's own levels (id, name, world, track), for exits and entry levels "
          "(stock:level/<id>); search filters by name. Marks where a new game starts.",
          lambda s, a: s.stock_levels(a.get("search"), a.get("world")),
          {"search": {"type": "string"}, "world": {"type": "integer"}}),
    _tool("get_triggers", "The project's triggers (\"when ... then ...\" rules).", lambda s, a: s.get_triggers()),
    _tool("set_triggers", "Replace the project's triggers; returns script problems.",
          lambda s, a: s.set_triggers(a["triggers"]), {"triggers": {"type": "array", "items": {"type": "object"}}},
          ["triggers"]),
    _tool("write_script", "Write scripts/<name>.lua (the port's Lua API).",
          lambda s, a: s.write_script(a["name"], a["text"]),
          {"name": {"type": "string"}, "text": {"type": "string"}}, ["name", "text"]),
    _tool("import_music", "Copy an .ogg into the project's music.", lambda s, a: s.import_music(a["path"],
                                                                                                a.get("name")),
          {"path": {"type": "string"}, "name": {"type": "string"}}, ["path"]),
    _tool("validate", "Check the project against the game's rules, and each level's objects against its geometry; "
          "each problem names its file and object.",
          lambda s, a: s.validate(a.get("notes", False), a.get("geometry", True)),
          {"notes": {"type": "boolean"},
           "geometry": {"type": "boolean", "description": "also check placement against the geometry (default "
                                                          "true)"}}),
    _tool("build", "Build the .nrm pack.", lambda s, a: s.build(a.get("out")), {"out": {"type": "string"}}),
    _tool("play", "Build, install into the port and start the game in a level, for the player to play.",
          lambda s, a: s.play(a.get("level"), a.get("start_point", 0), a.get("character", "juno"),
                              a.get("launch", True)),
          {"level": _LEVEL, "start_point": {"type": "integer"},
           "character": {"type": "string", "enum": ["vela", "juno", "lupus"]},
           "launch": {"type": "boolean", "description": "false: build and install only"}}),
]
_BY_NAME = {t["name"]: t for t in TOOLS}


def tool_list() -> list[dict]:
    return [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]


def call(session: agent.Session, name: str, args: dict) -> dict:
    """A tools/call result: the tool's value as JSON text, or the error as text with isError."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            value = tool["run"](session, args or {})
    except agent.AgentError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except KeyError as e:
        return {"content": [{"type": "text", "text": f"missing argument {e}"}], "isError": True}
    except Exception as e:  # noqa: BLE001 (the agent gets the error; the server keeps running)
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": f"internal error: {type(e).__name__}: {e}"}], "isError": True}
    if isinstance(value, dict) and isinstance(value.get("png"), bytes):  # a picture, with the rest as text
        rest = {k: v for k, v in value.items() if k != "png"}
        return {"content": [{"type": "image", "data": base64.b64encode(value["png"]).decode("ascii"),
                             "mimeType": "image/png"}, {"type": "text", "text": agent.dumps(rest)}], "isError": False}
    text = value if isinstance(value, str) else agent.dumps(value)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle(session: agent.Session, msg: dict) -> dict | None:
    """One JSON-RPC message in, its response out (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notifications/initialized, cancellations: nothing to answer
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "jfg-studio", "version": __version__}, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        result = call(session, params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(project: str | None = None, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    session = agent.Session()
    if project:
        try:
            session.open(project)
        except agent.AgentError as e:
            print(f"jfgstudio mcp: {e}", file=sys.stderr)
    for raw in stdin:
        line = raw.decode("utf-8").strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(session, msg) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if reply is not None:
            stdout.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()
    return 0
