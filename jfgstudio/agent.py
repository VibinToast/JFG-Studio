"""What an AI agent (or any script) uses to make levels: one open project, every action a call that takes and returns
plain JSON values, saved as it goes. The MCP server (`mcp_server.py`) offers these as tools.

The editor's own code does the work (edit.py's undo history, prefabs.py's Placer, the validator, the build), so an
agent's edits are the ones the editor would make, and the editor opens the result. Objects are named by uid; a type
by its schema key ("exit", "light"), its stock object id ("0x29") or a kind of the pack ("pack:kind/fire_ant").
Positions are game units, Y up, as in Blender's glTF export.

The catalog (names, stock examples and the stock tracks, from the user's own ROM) is used when it can be opened. It
stays on this machine: the project only gets the stock entry numbers a new object starts from, as the Prefabs window
gives it.
"""

import copy
import itertools
import json
import math
from collections import Counter
from importlib import resources
from pathlib import Path

import jsonschema
import numpy as np

from jfgstudio import edit, formats, importer, placement, prefabs, preview, refs, rom, scripts, squads, stock_edit
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val

SQUADRON_ID = prefabs.SQUADRON_ID
DOOR_LOCKED = 1  # a door's keyLock: locked until its squadrons are dead (or a switch opens it)
LEVEL_KEYS = ("name", "track", "sky", "music", "planet", "ambient", "header")
SURFACES = ("solid", "ledge", "lava", "hidden", "water", "kill", "nocollide")  # TrackMesh's kinds; 4 are floor
FLOOR_KINDS = ("solid", "ledge", "lava", "hidden")
LIST_LIMIT = 150  # catalog types listed at once
NEW_GAME_LEVEL = 21  # where a new game starts (Juno); the editor's default way back to the game
SIZED = 12  # describe_type of a shared schema (doors) measures this many of its most used types' models
KEY_IDS = tuple(range(0x258, 0x262))  # the coloured keys: keyLock 2 (yellow) is 0x258 ... 11 (blue) 0x261
PATROL_NEAR = squads.PATROL_NEAR  # place_patrol moves a squadron farther than this from its route to its first node
EXPLOSIVE_DRUM, DRUM_SIZE = 0x168, 24  # most of the game's explosive drums are size 24 (its crates 39)
CLIMB, CLIMB_STEP = 100, 70  # a key higher than this over every floor round it is out of reach; steps to give
REACH_RING = (100, 160, 220, 300)  # how far round a key to look for a floor to climb from
FOLD = 12  # a downward face this close to a floor: the floor folded over
WATER_BANK, WATER_HOVER = 12, 8  # past a water edge by this much, a floor lower than this under it: hovering water
DOOR_SLACK = 40  # a passage more than this much wider than its door: the player walks round it
DOOR_OVERLAP = 10  # a door covers its opening's edges by at least this much (both sides together), or gaps show
FLYER_CRUISE = 150  # flying squads cruise this far over the floor (the game's: about 150)
FLYER_ROOM = 60  # and stay this far under a roof
PATROL_BEHAVIOUR, PATROL_MODE = squads.PATROL_BEHAVIOUR, squads.PATROL_MODE
SMART, SENTRY = squads.SMART, squads.SENTRY
IMAGE_PIXELS = 2048  # a new image is stored as one 16-bit texture of at most 4 KB: bigger ones are shrunk
LAVA_FLOOR_DEPTH = 100  # lava needs a solid floor at most this far under it, or Juno falls through
DOOR_CLEARANCE = 120  # doors, exits and start points placed by [x, z]: the lowest floor with this much room above
OPEN_FLOOR_KEYS = ("door", "exit", "startpoint")
SPREAD_TRIES = 24  # place_squad looks this many places around the centre for a dry spot for each member


class AgentError(Exception):
    """A request that can't be done; the message says why and what to do instead."""


def _as_int(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int | float) or (isinstance(v, float) and not v.is_integer()):
        raise AgentError(f"{what} must be a whole number, not {v!r}")
    return int(v)


class Session:
    """One open project and its undo histories."""

    def __init__(self, path: str | Path | None = None):
        self.project: proj.Project | None = None
        self.schemas = obj.load_schemas()
        self.histories: dict[str, edit.History] = {}
        self._catalog = None
        self._catalog_error: str | None = None
        self._types: dict[int, dict] | None = None
        self._meshes: dict[str, tuple[float, object]] = {}
        self._sizes: dict[int, dict | None] = {}
        self._slots: dict[int, list] = {}
        self._lost: dict[tuple, int] = {}
        self._squads: dict | None = None  # enemy -> the best stock squad of it (squad_examples)
        self._textures = None  # game_textures.Library, made on first use
        self._notes: list[str] = []  # what placing chose where there was a choice; added to the call's result
        self._model_data = None
        if path is not None:
            self.open(path)

    # ------------------------------------------------------------------ project

    @property
    def p(self) -> proj.Project:
        if self.project is None:
            raise AgentError("no project is open: open_project (or new_project) first")
        return self.project

    def open(self, path) -> dict:
        try:
            self.project = proj.load(path)
        except proj.ProjectError as e:
            raise AgentError("the project doesn't load:\n" + "\n".join(e.problems)) from e
        self.histories.clear()
        self._meshes.clear()
        return self.summary()

    def new(self, path, mod_id: str, name: str, author: str = "") -> dict:
        try:
            self.project = proj.create(path, mod_id, name, author)
        except proj.ProjectError as e:
            raise AgentError("\n".join(e.problems)) from e
        self.histories.clear()
        return self.summary()

    def _save(self) -> None:
        self.p.save()

    def summary(self) -> dict:
        p = self.p
        geometry = sorted(f.stem for f in (p.path / "geometry").glob("*.glb")) if (p.path / "geometry").is_dir() else []
        music = sorted(f.stem for f in (p.path / "music").glob("*.ogg")) if (p.path / "music").is_dir() else []
        self.catalog()
        return {
            "path": str(p.path), "id": p.id, "name": p.meta["name"], "version": p.meta.get("version"),
            "levels": [{"name": lv.name, "displayName": lv.level.get("name"), "track": lv.level.get("track"),
                        "planet": lv.level.get("planet"), "base": lv.level.get("base"),
                        "objects": len(lv.objects["objects"])} for lv in p.levels.values()],
            "planets": {k: {"displayName": v.get("displayName"), "galaxy": v.get("galaxy"),
                            "entryLevel": v.get("entryLevel")} for k, v in p.planets.items()},
            "galaxies": {k: v.get("name") for k, v in p.galaxies.items()},
            "kinds": {k: {"name": v.get("name"), "base": v.get("base")} for k, v in p.kinds.items()},
            "geometry": geometry, "music": music,
            "triggers": len(scripts.load_triggers(p)),
            "catalog": "available" if self._catalog else f"not available: {self._catalog_error}",
        }

    def _level(self, name: str) -> proj.Level:
        level = self.p.levels.get(name)
        if level is None:
            raise AgentError(f"no level {name!r}; the project's levels: {', '.join(self.p.levels) or '(none)'}")
        if level.base is not None and self.catalog() is not None:
            stock_edit.materialize(level, self._catalog)  # the game level's own objects, with the project's changes
        return level

    def _writable(self, lv: proj.Level, uids) -> None:
        """The game level's own objects change only with the level's editStock on (edit_game_objects)."""
        game = [u for u in uids if stock_edit.is_stock(u)]
        if game and not stock_edit.editable(lv):
            raise AgentError(f"{game[0]} is one of the game level's own objects, read-only in {lv.name}: "
                             f"edit_game_objects({lv.name!r}, true) lets you change and remove them")

    def _history(self, level: proj.Level) -> edit.History:
        h = self.histories.get(level.name)
        if h is None or h.level is not level:
            h = self.histories[level.name] = edit.History(level)
        return h

    # ------------------------------------------------------------------ catalog

    def catalog(self):
        """The user's catalog, or None (the reason is kept for summary)."""
        if self._catalog is None and self._catalog_error is None:
            try:
                from jfgstudio.catalog import Catalog
                self._catalog = Catalog.open()
                self._types = self._catalog.objtypes()
            except (formats.FormatsUnavailable, rom.RomError, OSError, ValueError) as e:
                self._catalog_error = str(e)
        return self._catalog

    def _behaviours(self) -> dict[int, int]:
        self.catalog()
        return {k: v["behaviour"] for k, v in (self._types or {}).items()}

    def object_types(self, search: str | None = None, behaviour: int | None = None) -> dict:
        """The object-type schemas (short); with `search` or `behaviour`, also the game's own object types that match
        (from the catalog)."""
        schemas = [{"key": s.key, "name": s.name, "category": s.category, "status": s.status,
                    **({"ids": [f"0x{i:X}" for i in s.ids[:3]] + (["..."] if len(s.ids) > 3 else [])} if s.ids
                       else {"behaviour": s.behaviour})}
                   for s in sorted(self.schemas.values(), key=lambda s: (s.category, s.key))]
        if search is None and behaviour is None:
            return {"schemas": schemas,
                    "next": "describe_type <key> for a schema's fields and ids; object_types with search (a name) or "
                            "behaviour (a schema's behaviour number) for the game's own types"}
        out = {}  # a search: only the matches (the schemas are the call without one)
        if self.catalog() is None:
            out["catalog"] = f"not available ({self._catalog_error}): only the schemas' own ids can be named"
            return out
        found = []
        for oid, t in sorted(self._types.items()):
            if search and search.lower() not in t["name"].lower():
                continue
            if behaviour is not None and t["behaviour"] != behaviour:
                continue
            s = obj.schema_for_id(oid, self.schemas, self._behaviours())
            found.append({"id": f"0x{oid:X}", "name": t["name"], "behaviour": t["behaviour"],
                          "schema": s.key if s else None, "uses": t.get("uses", 0)})
        found.sort(key=lambda t: -t["uses"])
        out["types"] = found[:LIST_LIMIT]
        if len(found) > LIST_LIMIT:
            out["more"] = f"{len(found) - LIST_LIMIT} more: narrow it with search or behaviour"
        return out

    def describe_type(self, type) -> dict:
        if isinstance(type, str) and type in self.schemas and not self.schemas[type].ids:
            s = self.schemas[type]  # shared by every type with one behaviour (doors, squad members): no id of its own
            out = {"schema": s.key, "name": s.name, "status": s.status, "description": s.description,
                   "fields": [self._field_doc(f) for f in s.fields], "behaviour": s.behaviour,
                   "note": f"place one by the id of a game type with behaviour {s.behaviour}"}
            if self.catalog() is not None:
                ids = [oid for _, oid in sorted(((t.get("uses", 0), oid) for oid, t in self._types.items()
                                                 if t["behaviour"] == s.behaviour), reverse=True)]
                out["ids"] = [f"0x{oid:X}" for oid in ids][:LIST_LIMIT]
                sizes = {f"0x{oid:X}": self.model_size(oid) for oid in ids[:SIZED]}  # the most used ones
                out["modelSizes"] = {k: {a: v[a] for a in "xyz"} for k, v in sizes.items() if v}
            return out
        oid, schema, kind = self._type(type)
        out = {"id": f"0x{oid:X}" if oid is not None else None, "kind": kind}
        if schema is not None:
            out.update({"schema": schema.key, "name": schema.name, "status": schema.status,
                        "description": schema.description, "category": schema.category,
                        "fields": [self._field_doc(f) for f in schema.fields]})
            if schema.status != "decoded":
                out["note"] = ("not fully decoded: a new one starts from a stock entry of the game (its undecoded "
                               "bytes are copied by the build from the player's ROM)")
        else:
            out["note"] = "no schema: it can only be placed from a stock entry, with its fields left as they are"
        if self.catalog() is not None and oid in self._types:
            t = self._types[oid]
            out.update({"gameName": t["name"], "behaviour": t["behaviour"], "uses": t.get("uses", 0),
                        "examples": t.get("examples", [])})
            if t.get("borrowed"):
                out["examplesFrom"] = (f"{t['borrowed']}: no stock level places this type, so a new one copies an "
                                       "entry of that type (same behaviour) and keeps its own id")
            size = self.model_size(oid)
            if size:
                out["modelSize"] = size
        return out

    def model_size(self, oid: int) -> dict | None:
        """The size of a type's model in game units, at its definition's scale and facing 0 (x across, y up, z along
        the facing): from the player's catalog, measured once per run. None without a model or a catalog."""
        if oid in self._sizes:
            return self._sizes[oid]
        size = None
        t = (self._types or {}).get(oid)
        if self.catalog() is not None and t and t.get("kind") == "models" and t.get("assets"):
            try:
                from jfgstudio.gui.viewport import TrackMesh
                mesh = TrackMesh(str(self._catalog.model_glb(t["assets"][0])), surfaces=False)
                lo, hi = (mesh.bounds[0] * t["scale"]).tolist(), (mesh.bounds[1] * t["scale"]).tolist()
                x, y, z = (b - a for a, b in zip(lo, hi, strict=True))
                size = {"x": round(x), "y": round(y), "z": round(z),
                        "from": [round(v) for v in lo], "to": [round(v) for v in hi],
                        "note": "the model at its type's scale, around the object's position (from / to: its box "
                                "relative to it, so from y 0 means it stands on its position); an object's own "
                                "scale field (if any) multiplies it"}
            except Exception:  # noqa: BLE001 (a model the reader can't take: no size, not an error)
                size = None
        self._sizes[oid] = size
        return size

    @staticmethod
    def _field_doc(f: obj.Field) -> dict:
        d = {"name": f.name, "type": f.type, "range": list(obj._RANGES[f.type]), "default": f.default}
        for k in ("unit", "link", "role", "description"):
            if getattr(f, k):
                d[k] = getattr(f, k)
        if f.unit in obj.ANGLE_SHIFT:
            d["perTurn"] = 0x10000 >> obj.ANGLE_SHIFT[f.unit]  # steps in a full turn (facing: degrees)
        if f.enum:
            d["values"] = {str(k): v for k, v in f.enum.items()}
        return d

    def stock_levels(self, search: str | None = None, world: int | None = None) -> dict:
        """The game's own levels (from the catalog) as "id: name (world w, sky s, tune t)"; `search` filters by name,
        `world` by world. Exits and entry levels name them stock:level/<id>; set_level's `sky` takes a level's sky
        number (-1 is none: black above) and its header's "tune" a level's music."""
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error})")
        out = [f"{lv['id']}: {lv['name']} (world {lv['world']}, sky {lv.get('sky', -1)}, tune {lv.get('tune')})"
               for lv in self._catalog.levels()
               if (not search or search.lower() in lv["name"].lower()) and (world is None or lv["world"] == world)]
        return {"levels": out[:LIST_LIMIT * 3], "newGame": NEW_GAME_LEVEL,
                "note": f"stock:level/{NEW_GAME_LEVEL} is where a new game starts (Juno): the usual way back to the "
                        "game, and a good place for a portal into the pack"}

    # ------------------------------------------------------------------ the game's textures

    def _library(self):
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error}): it has the game's textures")
        if self._textures is None:
            from jfgstudio import game_textures
            self._textures = game_textures.Library(self._catalog)
        return self._textures

    def _level_names(self, ids) -> list[str]:
        levels = self._catalog.levels()
        return [f"{n}: {levels[n]['name']}" for n in ids if 0 <= n < len(levels)]

    def game_textures(self, words=None, level=None, size=None, limit: int = 40, repeats: str | None = None) -> dict:
        """The game's level textures (from the player's ROM; a level painted with them carries none of them): those
        with all of `words` (a colour: green, brown, grey, blue, cyan, orange, yellow, red, pink, purple, white,
        black; dark / mid / bright / vivid), used by game level `level`, of `size` [w, h]; `repeats` "both": only
        those that tile both ways (301 of the game's; the rest clamp, and jfg64's export cuts faces so each piece
        holds one repeat). Each: id (hex, for jfg64's game_texture_material), size, words, which way it repeats,
        alpha, the game levels that use it, and its PNG's path."""
        lib = self._library()
        want = {w.lower() for w in (words or [])}
        lv = _as_int(level, "level") if level is not None else None
        out = []
        for tex, t in sorted(lib.textures().items(), key=lambda kv: -len(kv[1]["levels"])):
            if want - set(t["words"]) or (lv is not None and lv not in t["levels"]):
                continue
            if size is not None and [t["width"], t["height"]] != [int(v) for v in size]:
                continue
            rep = t.get("repeat", [True, True])
            if repeats == "both" and not all(rep):
                continue
            out.append({"id": f"{tex:04X}", "size": [t["width"], t["height"]], "words": t["words"],
                        "repeats": "both" if all(rep) else "across" if rep[0] else "up" if rep[1] else "no",
                        "alpha": t["alpha"], "levels": self._level_names(t["levels"][:4])
                        + ([f"... {len(t['levels']) - 4} more"] if len(t["levels"]) > 4 else []),
                        "path": str(lib.path(tex))})
            if len(out) >= max(1, int(limit)):
                break
        return {"textures": out, "note": "texture_sheet shows them; in Blender "
                                         "jfg64.game_texture_material(int(id, 16)) paints with one. Most used first"}

    def stock_level_textures(self, level, limit: int = 24) -> dict:
        """What a game level is made of: its textures by how much of the level they cover, each as floor, wall or
        ceiling, with a picture of them (ids under each). For a theme ("a forest level"): find the game's levels
        of that kind (stock_levels), look at what they use, and paint with the same textures."""
        lib = self._library()
        lv = self._catalog.levels()[_as_int(level, "level")]
        usage = lib.track_usage(lv["track"])
        total = sum(u["area"] for u in usage.values()) or 1
        tex = lib.textures()
        rows = []
        for t, u in sorted(usage.items(), key=lambda kv: -kv[1]["area"])[:max(1, int(limit))]:
            use = max(("floor", "wall", "ceiling"), key=lambda k: u[k])
            rep = tex[t].get("repeat", [True, True])
            rows.append({"id": f"{t:04X}", "share": f"{100 * u['area'] / total:.1f}%", "mostly": use,
                         "repeats": "both" if all(rep) else "across" if rep[0] else "up" if rep[1] else "no",
                         "size": [tex[t]["width"], tex[t]["height"]], "words": tex[t]["words"]})
        return {"png": lib.sheet([int(r["id"], 16) for r in rows]), "level": f"{lv['id']}: {lv['name']}",
                "world": lv["world"], "textures": rows, "header": lv.get("header", {}),
                "note": "the picture shows them in this order, left to right, with each id under it. header: the "
                        "level's music (tune), fog and ambience, for set_level's header to match its mood"}

    def _layout_of(self, mesh) -> tuple[dict, bytes]:
        from jfgstudio import layout_study
        found = layout_study.study(mesh.collision)
        if found is None:
            raise AgentError("the level has too little floor to measure")
        return found

    def stock_level_layout(self, level) -> dict:
        """How a game level is laid out: a floor map from above (brighter = higher, darker = roofed, orange = narrow
        passages; up is -z) and its numbers: footprint, walkable area, roofed share, floor heights and tiers, the
        share of passages (up to 400 wide), rooms (up to 1000) and open ground, the passages' width and the
        clearance under its roofs. Study the game's levels of the kind you are making before laying yours out."""
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error})")
        lv = self._catalog.levels()[_as_int(level, "level")]
        from jfgstudio.gui.viewport import TrackMesh
        stats, png = self._layout_of(TrackMesh(str(self._catalog.track_glb(lv["track"]))))
        return {"png": png, "level": f"{lv['id']}: {lv['name']}", "world": lv["world"], **stats}

    def level_layout(self, level: str) -> dict:
        """stock_level_layout for a level of the project: compare your layout with the game's."""
        stats, png = self._layout_of(self._mesh(level))
        return {"png": png, "level": level, **stats}

    def texture_sheet(self, ids) -> dict:
        """A picture of game textures side by side, each id (hex) under it."""
        lib = self._library()
        tex = lib.textures()
        picked = []
        for i in ids or []:
            n = int(i, 16) if isinstance(i, str) else int(i)
            if n not in tex:
                raise AgentError(f"{i}: not one of the game's level textures (game_textures lists them)")
            picked.append(n)
        if not picked:
            raise AgentError("give the texture ids to show")
        return {"png": lib.sheet(picked[:64]), "shown": [f"{n:04X}" for n in picked[:64]]}

    def _type(self, type) -> tuple[int | None, obj.Schema | None, str | None]:
        """(stock object id, schema, pack kind ref) of a type named by schema key, id or kind."""
        kind = None
        if isinstance(type, str):
            t = type.strip()
            if t.startswith("pack:kind/"):
                name = t.split("/", 1)[1]
                k = self.p.kinds.get(name)
                if k is None:
                    raise AgentError(f"no kind {name!r} in the project (kinds: {', '.join(self.p.kinds) or 'none'})")
                kind, t = t, k["base"]
            if t.startswith("stock:objtype/"):
                t = t.split("/", 1)[1]
            if t in self.schemas:
                s = self.schemas[t]
                if not s.ids:
                    raise AgentError(f"{t!r} covers every object type with behaviour {s.behaviour}: name one of them "
                                     f"by id (object_types with behaviour={s.behaviour})")
                return s.ids[0], s, kind
            try:
                oid = int(t, 0)
            except ValueError:
                raise AgentError(f"unknown type {type!r}: use a schema key ({', '.join(sorted(self.schemas))}), a "
                                 "stock id like 0x29, or pack:kind/<name>") from None
        else:
            oid = _as_int(type, "type")
        return oid, obj.schema_for_id(oid, self.schemas, self._behaviours()), kind

    def _example(self, oid: int, schema, example) -> tuple[int, int] | None:
        if example is not None:
            if not isinstance(example, list | tuple) or len(example) != 2:
                raise AgentError("example is [object list, entry]")
            return _as_int(example[0], "example list"), _as_int(example[1], "example entry")
        if schema is not None and schema.status == "decoded":
            return None
        self.catalog()
        examples = (self._types or {}).get(oid, {}).get("examples")
        if examples:
            return tuple(examples[0])
        why = (f"the catalog is not available ({self._catalog_error})" if self._types is None
               else "the game's lists don't use it")
        raise AgentError(f"type 0x{oid:X} is not fully decoded, so a new one starts from a stock entry, but {why}: "
                         "give example [list, entry]")

    # ------------------------------------------------------------------ geometry

    def _mesh(self, level_or_track):
        """The TrackMesh of a level's track, or of a geometry file of the project by name."""
        if level_or_track in self.p.levels:
            track = self.p.levels[level_or_track].level.get("track", "")
        elif isinstance(level_or_track, str) and level_or_track.startswith(("pack:track/", "stock:track/")):
            track = level_or_track
        else:
            track = f"pack:track/{level_or_track}"
        ref = refs.parse(track, "track")
        if ref.is_pack:
            path = self.p.geometry_path(ref.name)
            if not path.exists():
                raise AgentError(f"{path.name} is not in the project's geometry folder (import_geometry first)")
        else:
            if self.catalog() is None:
                raise AgentError(f"{track} is a stock track and the catalog is not available ({self._catalog_error})")
            path = self._catalog.track_glb(ref.number())
        stamp = path.stat().st_mtime
        cached = self._meshes.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            from jfgstudio.gui.viewport import TrackMesh
            mesh = TrackMesh(str(path))
        except formats.FormatsUnavailable as e:
            raise AgentError(str(e)) from e
        except Exception as e:  # noqa: BLE001 (any reader failure: the agent hears what and where)
            raise AgentError(f"{path.name} doesn't read as a .glb: {type(e).__name__}: {e}") from e
        self._meshes[str(path)] = (stamp, mesh)
        return mesh

    def _floor(self, mesh, x: float, z: float, below: float | None = None) -> float | None:
        return mesh.floor_height(x, z, below)

    def surface_at(self, level: str, points: list, below: float | None = None) -> list[dict]:
        """For each [x, z]: the floor's height and kind, and water or a kill floor over or instead of it."""
        from jfgstudio.gui.viewport import floor_of
        mesh = self._mesh(level)
        kinds = {k: mesh.kind_triangles(k) for k in SURFACES}
        out = []
        for pt in points:
            if not isinstance(pt, list | tuple) or len(pt) < 2:
                raise AgentError("points are [x, z] pairs")
            x, z = float(pt[0]), float(pt[-1])
            at = {"x": x, "z": z}
            tops = {k: floor_of(t, x, z, below) for k, t in kinds.items()}
            floors = {k: y for k, y in tops.items() if k in FLOOR_KINDS and y is not None}
            if floors:
                kind = max(floors, key=floors.get)
                at.update({"floor": round(floors[kind], 1), "kind": {"hidden": "collider"}.get(kind, kind)})
            else:
                at["floor"] = None
            water = tops["water"]
            if water is not None and (at["floor"] is None or water > at["floor"]):
                at["water"] = round(water, 1)
                if at["floor"] is not None:
                    at["depth"] = round(water - at["floor"], 1)
            kill = tops["kill"]
            if kill is not None and (at["floor"] is None or kill > at["floor"]):
                at["kill"] = round(kill, 1)
            if at.get("kind") == "lava":  # Juno passes through lava: what holds him up is the solid floor under it
                under = floor_of(np.concatenate([kinds["solid"], kinds["ledge"]]), x, z, at["floor"] - 1)
                hidden = floor_of(kinds["hidden"], x, z, at["floor"] - 1)
                under = max((y for y in (under, hidden) if y is not None), default=None)
                at["underLava"] = round(under, 1) if under is not None else None
            out.append(at)
        return out

    def _lost_collision(self) -> dict[str, int]:
        """Levels whose own geometry would lose collision in the game (importer.lost_collision): needs the catalog to
        run the game's track importer; checked once per file version."""
        if self.catalog() is None:
            return {}
        from jfgformats import models, tracks
        out = {}
        for name, lv in self.p.levels.items():
            track = lv.level.get("track", "")
            if not track.startswith("pack:track/"):
                continue
            path = self.p.geometry_path(track.split("/", 1)[1])
            if not path.exists():
                continue
            key = (str(path), path.stat().st_mtime)
            if key not in self._lost:
                try:
                    if self._model_data is None:
                        self._model_data = models.ModelData(self._catalog.assets)
                    res = tracks.import_track(self._model_data, str(path), track_id=self._catalog.track_count(),
                                              log=lambda _: None)
                    self._lost[key] = importer.lost_collision(tracks.read_track(res["data"]))
                except Exception:  # noqa: BLE001 (the build reports a file the importer refuses)
                    self._lost[key] = 0
            if self._lost[key]:
                out[name] = self._lost[key]
        return out

    def lava_holes(self, level: str) -> list[list[int]]:
        """Points of lava with no solid floor under it (LAVA_FLOOR_DEPTH at most): Juno, whose collision passes
        through lava, falls through there. One point per lava triangle (its middle)."""
        from jfgstudio.gui.viewport import floor_of
        mesh = self._mesh(level)
        lava = mesh.kind_triangles("lava")
        ground = np.concatenate([mesh.kind_triangles(k) for k in ("solid", "ledge", "hidden")])
        out = []
        for t in lava:
            x, y, z = (float(v) for v in t.mean(axis=0))
            under = floor_of(ground, x, z, y - 1)
            if under is None or y - under > LAVA_FLOOR_DEPTH:
                out.append([round(x), round(y), round(z)])
        return out

    def find_flat(self, level: str, x: float, z: float, radius: float = 60, search: float = 600,
                  step: float = 50, tolerance: float = 8, count: int = 5) -> list[dict]:
        """Spots near (x, z) whose floor is level within `tolerance` over a disc of `radius`, dry, nearest first."""
        mesh = self._mesh(level)
        from jfgstudio.gui.viewport import floor_of
        water, kill = mesh.kind_triangles("water"), mesh.kind_triangles("kill")
        ring = [(0.0, 0.0)] + [(radius * math.cos(a), radius * math.sin(a))
                               for a in (k * math.pi / 4 for k in range(8))]
        n = max(1, int(search // max(step, 1)))
        spots = []
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                cx, cz = x + i * step, z + j * step
                d = math.hypot(cx - x, cz - z)
                if d > search:
                    continue
                ys = [self._floor(mesh, cx + dx, cz + dz) for dx, dz in ring]
                if any(y is None for y in ys) or max(ys) - min(ys) > tolerance:
                    continue
                w, k = floor_of(water, cx, cz), floor_of(kill, cx, cz)
                if (w is not None and w > ys[0]) or (k is not None and k > ys[0]):
                    continue
                spots.append({"x": round(cx), "z": round(cz), "floor": round(ys[0], 1), "distance": round(d)})
        spots.sort(key=lambda s: s["distance"])
        return spots[:max(1, int(count))]

    def raycast(self, level: str, origin, direction) -> dict:
        from jfgstudio.gui.viewport import raycast
        mesh = self._mesh(level)
        d = [float(v) for v in direction]
        length = math.sqrt(sum(v * v for v in d))
        if length == 0:
            raise AgentError("direction is all zero")
        d = [v / length for v in d]
        t = raycast(mesh.collision, [float(v) for v in origin], d)
        if t is None:
            return {"hit": False}
        return {"hit": True, "distance": round(t, 1), "point": [round(o + v * t, 1) for o, v in zip(origin, d,
                                                                                                    strict=True)]}

    def geometry_info(self, name: str) -> dict:
        """A geometry file's (or level's track's) size, surfaces and the game's limits it is measured against."""
        mesh = self._mesh(name)
        images = self._glb_images(name)
        lo, hi = mesh.bounds
        far = float(max(abs(float(v)) for v in (*lo, *hi)))
        counts = {k: len(mesh.kind_triangles(k)) for k in SURFACES}
        out = {"bounds": [[round(float(v)) for v in lo], [round(float(v)) for v in hi]],
               "triangles": sum(counts.values()), "surfaces": {k: n for k, n in counts.items() if n},
               "images": len(images), "farthest": round(far),
               "limits": {"farthest": 32767, "imagePixels": IMAGE_PIXELS}}
        shrunk = [n for n, (w, h) in images.items() if w * h > IMAGE_PIXELS or w & (w - 1) or h & (h - 1)]
        if shrunk:
            out["shrunk"] = shrunk  # the game stores them smaller: draw them at 64 x 32, 32 x 64 ...
        if far > 32767:
            out["problem"] = "a vertex is farther than 32767 units from the origin: the game can't hold it"
        folded = self._folded_floors(mesh)
        if folded:
            out["folded"] = {"count": len(folded), "at": folded[:3],
                             "problem": "floor folded over: a face turned down lies on the floor, and the hero is "
                                        "falling all the time there. Fix the geometry (jfg64 0.5.5's layout doesn't "
                                        "fold passages at slanted doorways)"}
        hovering = self._hovering_water(mesh)
        if hovering:
            out["water"] = {"hovering": len(hovering), "at": hovering[:3],
                            "problem": "water hovers over the floor at its edge: sink it into a hollow so its edge "
                                       "meets the bank (or lay it on the floor)"}
        return out

    def _glb_images(self, name: str) -> dict[str, tuple[int, int]]:
        """The new images the materials of a level's (or geometry file's) .glb use: name -> (width, height). The
        game's own level textures aren't counted: the pack names them by number."""
        track = self.p.levels[name].level.get("track", "") if name in self.p.levels else f"pack:track/{name}"
        if not track.startswith("pack:track/"):
            return {}
        path = self.p.geometry_path(track.split("/", 1)[1])
        try:
            from jfgformats import gltf
            doc, blob = gltf.parse_glb(path.read_bytes(), "geometry")
        except Exception:  # noqa: BLE001 (not a .glb the reader takes: counted as none)
            return {}
        used = set()
        for m in doc.get("materials", []):
            t = ((m.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}).get("index")
            if t is not None and t < len(doc.get("textures", [])) and "source" in doc["textures"][t]:
                used.add(doc["textures"][t]["source"])
        try:
            library = self._library()
        except AgentError:
            library = None
        out = {}
        for i in sorted(used):
            img = doc["images"][i]
            data = b""
            if "bufferView" in img:
                v = doc["bufferViews"][img["bufferView"]]
                data = bytes(blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]])
            if library is not None and data and library.level_texture(data) is not None:
                continue  # a game texture: named by number, at the game's own size (never shrunk, never carried)
            size = (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) \
                if data[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
            out[img.get("name") or f"image{i}"] = size
        return out

    def import_geometry(self, src: str, name: str | None = None) -> dict:
        """Copies a .glb (from Blender) into the project and reports what the game will see."""
        try:
            dest = importer.import_geometry(self.p, src, name)
        except proj.ProjectError as e:
            raise AgentError("\n".join(e.problems)) from e
        self._meshes.pop(str(dest), None)
        out = {"geometry": dest.stem, "track": f"pack:track/{dest.stem}", "info": self.geometry_info(dest.stem)}
        if self.catalog() is not None:
            report = importer.check(dest, self._catalog)
            out["check"] = {"ok": report.ok, "lines": report.lines}
        try:
            placed = importer.markers(dest)
        except Exception:  # noqa: BLE001 (markers are optional)
            placed = []
        if placed:
            out["markers"] = [{"name": m.get("name"), "type": m["type"], "pos": m["pos"], "fields": m["fields"]}
                              for m in placed]
        return out

    def reimport_geometry(self, level: str, src: str) -> dict:
        """Replaces a level's geometry with a new export of it (one undo step); its objects stay where they are, and
        the result says which of them the new shape left buried, floating, sunk or over a pit."""
        lv = self._level(level)
        try:
            ref = refs.parse(lv.level.get("track", ""), "track")
        except refs.RefError as e:
            raise AgentError(str(e)) from e
        if not ref.is_pack:
            raise AgentError(f"{level} is on a stock track ({ref}): import_geometry a .glb and set_level its track")
        src_path = Path(src)
        if src_path.suffix.lower() != ".glb":
            raise AgentError(f"{src_path.name}: export a .glb (glTF binary) from Blender")
        try:
            data = src_path.read_bytes()
        except OSError as e:
            raise AgentError(f"cannot read {src}: {e.strerror}") from e
        dest = self.p.geometry_path(ref.name)
        self._history(lv).write_file(dest, data, "re-import geometry")
        self._meshes.pop(str(dest), None)
        shared = [o.name for o in self.p.levels.values() if o is not lv and o.level.get("track") == str(ref)]
        out = {"geometry": ref.name, "info": self.geometry_info(level)}
        if self.catalog() is not None:
            report = importer.check(dest, self._catalog)
            out["check"] = {"ok": report.ok, "lines": report.lines}
        out["placement"] = {name: self.check_placement(name) for name in [level, *shared]}
        if shared:
            out["sharedWith"] = shared  # the same file: their undo doesn't bring it back, this level's does
        if any(out["placement"].values()):
            out["next"] = "settle_objects puts sunk and floating ones on the floor; move the others yourself"
        return out

    def preview(self, level: str, size: int = 512, out: str | None = None, colours: bool = False) -> dict:
        """A top-down picture of the level with its objects ("png": the bytes), and its legend; `out` also saves it.
        An edit of a game level also shows that level's own start points and exits, grey. `colours`: the baked
        vertex colours instead of the surface kinds."""
        lv = self._level(level)
        png, legend = preview.render(self._mesh(level), lv.objects["objects"] + self.base_objects(lv), self.schemas,
                                     self._behaviours(), _as_int(size, "size"), bool(colours))
        if out:
            Path(out).write_bytes(png)
            legend["saved"] = str(out)
        return {"png": png, "legend": legend}

    def check_placement(self, level: str) -> list[dict]:
        """The level's objects that should stand on the floor but are buried, floating, sunk or over a pit, and
        doors narrower than the passage they stand in."""
        lv = self._level(level)
        mesh = self._mesh(level)
        found = placement.check(mesh, lv.objects["objects"], self.schemas, self._behaviours())
        return found + self._door_gaps(lv, mesh) + self._out_of_reach(lv, mesh)

    def _out_of_reach(self, lv: proj.Level, mesh) -> list[dict]:
        """Keys on a top the player can't climb to: every floor round it (out to REACH_RING) more than CLIMB below
        it. A key on a 210-high block, with a 40-high rock beside it, was out of reach and the level unbeatable
        (play-tested)."""
        out = []
        for o in lv.objects["objects"]:
            if obj.stock_id(o) not in val.KEY_IDS:
                continue
            x, y, z = (float(v) for v in o["pos"])
            top = mesh.floor_height(x, z, y + 5)
            if top is None:
                continue
            drops, level_with = [], False
            for r in REACH_RING:
                for dx, dz in placement.RING:
                    h = mesh.floor_height(x + dx * r, z + dz * r, top + 5)
                    if h is None:
                        continue
                    if abs(h - top) <= 5:
                        level_with = level_with or r == REACH_RING[-1]
                    else:
                        drops.append(top - h)
            if not level_with and drops and min(drops) > CLIMB:
                finding = {"uid": o["uid"], "pos": list(o["pos"]), "by": round(min(drops)),
                           "problem": f"out of reach: the nearest floor round it is {min(drops):.0f} below it. Give "
                                      f"the climb steps of about {CLIMB_STEP} (a 210-high block was out of reach, "
                                      "play-tested)"}
                if o.get("name"):
                    finding["name"] = o["name"]
                out.append(finding)
        return out

    @staticmethod
    def _folded_floors(mesh) -> list[list[float]]:
        """Where a floor is folded over: a solid triangle facing down lying on (within FOLD of) a floor. The game
        takes it for the ground's underside and the hero is falling all the time there (a passage's first piece
        folded back over a room's floor, play-tested)."""
        import numpy as np

        from jfgstudio.gui.viewport import floor_of
        t = mesh.collision
        if not len(t):
            return []
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(n, axis=1)
        down = t[(length > 1e-6) & (n[:, 1] < -0.7 * np.maximum(length, 1e-9))]
        floors = t[n[:, 1] > 0]
        out = []
        for tri in down:
            centre = tri.mean(0)
            for px, py, pz in [centre, *(v + (centre - v) * 0.2 for v in tri)]:  # its middle and near each corner
                h = floor_of(floors, float(px), float(pz), float(py) + FOLD)
                if h is not None and -2 <= py - h <= FOLD:  # on it or just over it (under the floor: hidden)
                    out.append([round(float(c)) for c in centre])
                    break
        return out

    def _hovering_water(self, mesh) -> list[list[float]]:
        """Points on the edges of water that hovers: past the edge, the floor is lower than the water and no wall
        holds it (a pool must lie in a hollow, its edge on the bank; a pool sheet 22 above the floor hovered,
        play-tested)."""
        from jfgstudio.gui.viewport import raycast
        tris = mesh.kind_triangles("water")
        edges: dict = {}
        for tri in tris:
            pts = [tuple(round(float(c), 1) for c in v) for v in tri]
            for i in range(3):
                a, b = pts[i], pts[(i + 1) % 3]
                key = (min(a, b), max(a, b))
                edges[key] = None if key in edges else pts[(i + 2) % 3]
        out = []
        for (a, b), third in edges.items():
            if third is None:
                continue  # shared by two water triangles: inside the water
            mx, my, mz = ((a[k] + b[k]) / 2 for k in range(3))
            ex, ez = b[0] - a[0], b[2] - a[2]
            n = math.hypot(ex, ez) or 1.0
            nx, nz = -ez / n, ex / n
            if nx * (third[0] - mx) + nz * (third[2] - mz) > 0:
                nx, nz = -nx, -nz  # away from the water
            h = mesh.floor_height(mx + nx * WATER_BANK, mz + nz * WATER_BANK, my + 30)
            if h is not None and h >= my - WATER_HOVER:
                continue  # it meets the bank
            wall = raycast(mesh.collision, (mx - nx, my + 2, mz - nz), (nx, 0.0, nz))
            if wall is None or wall > 2 * WATER_BANK:
                out.append([round(mx), round(my), round(mz)])
        return out

    def _door_gaps(self, lv: proj.Level, mesh) -> list[dict]:
        """Doors that don't close their opening: the walls either side (along the door's own width, at waist height)
        as far apart as the door is wide or further, or the ceiling over it higher than its top. The game's doors
        overlap their frame (its own levels mostly fit its 198-wide door in openings 150-200 wide); a door as wide
        as its tunnel showed gaps round it, and one in a room's mouth was walked round (play-tested)."""
        from jfgstudio.gui.viewport import raycast
        out = []
        for o in lv.objects["objects"]:
            if self._key(o) != "door":
                continue
            schema = obj.schema_for(o, self.schemas, self._behaviours())
            f = schema.role("yaw") if schema else None
            oid = obj.stock_id(o)
            size = self.model_size(oid) if oid is not None else None
            if f is None or size is None:
                continue
            a = obj.angle_of(f, o.get("fields", {}).get(f.name, 0))
            across = (math.cos(a), 0.0, -math.sin(a))  # the model's x at facing a (0: towards -z, x across)
            x, y, z = (float(v) for v in o["pos"])
            at = (x, y + min(100.0, size["y"] / 2), z)
            sides = [raycast(mesh.collision, at, (s * across[0], 0.0, s * across[2])) for s in (1, -1)]
            gap = None if None in sides else sides[0] + sides[1]
            up = raycast(mesh.collision, (x, y + 5, z), (0.0, 1.0, 0.0))
            problem = None
            if gap is None or gap > size["x"] + DOOR_SLACK:
                problem = "the passage is wider than the door: the player walks round it"
            elif gap >= size["x"] - DOOR_OVERLAP or (up is not None and up + 5 >= size["y"] - DOOR_OVERLAP):
                problem = ("the door doesn't overlap its frame: gaps show round it. Give it a doorway narrower and "
                           "lower than the door (jfg64 layout link \"door\"; door_spots says where)")
            if problem:
                finding = {"uid": o["uid"], "pos": list(o["pos"]), "problem": problem, "door": [size["x"], size["y"]],
                           "passage": round(gap) if gap is not None else "open on a side",
                           "headroom": round(up + 5) if up is not None else "open above"}
                if o.get("name"):
                    finding["name"] = o["name"]
                out.append(finding)
        return out

    def settle_objects(self, level: str, uids: list[str] | None = None) -> dict:
        """Puts objects back on the floor where check_placement found one for them (all of its findings, or `uids`),
        as one undo step."""
        lv = self._level(level)
        findings = {f["uid"]: f for f in self.check_placement(level)}
        wanted = list(findings) if uids is None else list(uids)
        changes, moved, left = [], [], []
        for uid in wanted:
            o = self._object(lv, uid)
            f = findings.get(uid)
            if f is None:
                continue  # already where it should be
            if "standOn" not in f:
                left.append({"uid": uid, "problem": f["problem"]})
                continue
            changes.append(edit.Change(uid, ("pos",), list(o["pos"]), f["standOn"]))
            moved.append({"uid": uid, "from": list(o["pos"]), "to": f["standOn"]})
        self._history(lv).do(edit.Command("settle objects", changes))
        self._save()
        return {"moved": moved, "left": left}

    def import_music(self, src: str, name: str | None = None) -> dict:
        src_path = Path(src)
        if src_path.suffix.lower() != ".ogg":
            raise AgentError("music is OGG Vorbis (.ogg)")
        name = name or "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in src_path.stem.lower())
        if not proj.NAME_RE.match(name):
            raise AgentError(f"{name!r}: music names use lower case letters, digits, _ and -")
        dest = self.p.music_path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src_path.read_bytes())
        return {"music": f"pack:music/{name}", "use": f"set_level(level, {{\"music\": \"pack:music/{name}\"}})"}

    # ------------------------------------------------------------------ levels

    def level_info(self, name: str) -> dict:
        level = self._level(name)
        out = {"level": copy.deepcopy(level.level), "objects": [self._describe(o) for o in level.objects["objects"]]}
        base = self.base_objects(level)
        if base:
            out["baseLevel"] = {"note": "the game level's own start points and exits (read-only): this level's "
                                        "objects are added to them, so don't reuse their start point numbers",
                                "objects": [{"schema": o["schema"], "pos": o["pos"], "fields": o["fields"]}
                                            for o in base]}
        return out

    def _describe(self, o: dict) -> dict:
        d = {k: copy.deepcopy(o[k]) for k in ("uid", "name", "type", "pos", "fields", "target", "stock",
                                              "kindFields", "group") if k in o}
        s = obj.schema_for(o, self.schemas, self._behaviours())
        if s:
            d["schema"] = s.key
            named = {}
            for f in s.fields:
                v = o.get("fields", {}).get(f.name, f.default)
                if f.enum and v in f.enum:
                    named[f.name] = f.enum[v]
            if named:
                d["named"] = named
        oid = obj.stock_id(o)
        if self._types and oid in self._types:
            d["typeName"] = self._types[oid]["name"]
        return d

    def add_level(self, name: str, display_name: str, geometry: str, planet: str | None = None) -> dict:
        """A level of the pack's own on a geometry file of the project (or a stock track, stock:track/<n>)."""
        track = geometry if geometry.startswith(("pack:track/", "stock:track/")) else f"pack:track/{geometry}"
        try:
            ref = refs.parse(track, "track")
        except refs.RefError as e:
            raise AgentError(str(e)) from e
        if ref.is_pack and not self.p.geometry_path(ref.name).exists():
            raise AgentError(f"geometry/{ref.name}.glb is not in the project: import_geometry first")
        try:
            level = proj.new_level(self.p, name, display_name[:31], track)
        except proj.ProjectError as e:
            raise AgentError("\n".join(e.problems)) from e
        if planet:
            if planet not in self.p.planets:
                del self.p.levels[name]
                raise AgentError(f"no planet {planet!r} yet (planets: {', '.join(self.p.planets) or 'none'}): "
                                 "a planet needs its entry level first, so add the level without one, then "
                                 'set_planet, then set_level {"planet": ...}')
            level.level["planet"] = planet
        self._save()
        return {"level": name, "next": "place a start point (type startpoint, fields startPoint 0, player 0)"}

    def edit_stock_level(self, name: str, level_id: int) -> dict:
        """A level of the project that adds objects to one of the game's own levels (a portal into the pack, say):
        what it holds is added to that level when the pack loads. Its track is the game level's own."""
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error}): it gives the level's track")
        level_id = _as_int(level_id, "level_id")
        levels = self._catalog.levels()
        if not 0 <= level_id < len(levels):
            raise AgentError(f"there is no stock level {level_id} (0-{len(levels) - 1}; stock_levels lists them)")
        lv = levels[level_id]
        try:
            # the project's own name for it, not the catalog's (the player's game data stays out of the project)
            proj.edit_stock_level(self.p, name, level_id, name[:31], lv["track"])
        except proj.ProjectError as e:
            raise AgentError("\n".join(e.problems)) from e
        self._save()
        return {"level": name, "base": f"stock:level/{level_id}", "track": f"stock:track/{lv['track']}",
                "next": "place objects in it like any level (its own objects stay); connect an exit to the pack"}

    def set_level(self, name: str, values: dict) -> dict:
        """Level settings: name, track, sky, music, planet, ambient, and header fields (merged by name)."""
        level = self._level(name)
        if not isinstance(values, dict):
            raise AgentError("values is an object of level settings")
        unknown = set(values) - set(LEVEL_KEYS)
        if unknown:
            raise AgentError(f"unknown level settings {sorted(unknown)}; known: {', '.join(LEVEL_KEYS)} "
                             "(header fields go inside \"header\")")
        doc = copy.deepcopy(level.level)
        changes = []
        for key, value in values.items():
            if key == "header":
                if not isinstance(value, dict):
                    raise AgentError("header is an object of header fields")
                for hk, hv in value.items():
                    changes.append(edit.Change(edit.LEVEL, ("header", hk), copy.deepcopy(doc["header"].get(hk)), hv))
                    if hv is None:
                        doc["header"].pop(hk, None)
                    else:
                        doc["header"][hk] = hv
            else:
                changes.append(edit.Change(edit.LEVEL, (key,), copy.deepcopy(doc.get(key)), value))
                if value is None:
                    doc.pop(key, None)
                else:
                    doc[key] = value
        errors = list(jsonschema.Draft202012Validator(proj._SCHEMAS["level"]).iter_errors(doc))
        if errors:
            raise AgentError("not a valid level.json: " + "; ".join(
                f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}" for e in errors))
        if "planet" in values and values["planet"] and values["planet"] not in self.p.planets:
            raise AgentError(f"no planet {values['planet']!r} yet (planets: {', '.join(self.p.planets) or 'none'}): "
                             "a planet needs its entry level first, so add the level without one, then "
                             'set_planet, then set_level {"planet": ...}')
        if values.get("music"):
            try:
                music = refs.parse(values["music"], "music")
            except refs.RefError as e:
                raise AgentError(f"music: {e}") from e
            if music.is_pack and not self.p.music_path(music.name).exists():
                raise AgentError(f"music/{music.name}.ogg is not in the project: import_music first")
        self._history(level).do(edit.Command("level settings", changes))
        self._save()
        return {"level": copy.deepcopy(level.level)}

    # ------------------------------------------------------------------ objects

    def _fields(self, schema, fields: dict | None) -> dict:
        """Fields checked against the schema; a named value ("yellow key") becomes its number."""
        out = {}
        for k, v in (fields or {}).items():
            f = schema.field(k) if schema else None
            if f is None:
                names = ", ".join(x.name for x in schema.fields) if schema else "(this type has no schema)"
                raise AgentError(f"{k!r} is not a field of {schema.key if schema else 'this type'}; fields: {names}")
            if isinstance(v, str):
                match = [n for n, label in f.enum.items() if label.lower() == v.lower()]
                if not match:
                    raise AgentError(f"{k}: {v!r} is not one of its named values "
                                     f"({', '.join(f.enum.values()) or 'it has none: give a number'})")
                v = match[0]
            v = _as_int(v, k)
            lo, hi = obj._RANGES[f.type]
            if not lo <= v <= hi:
                raise AgentError(f"{k} = {v} is outside {lo}..{hi}")
            out[k] = v
        return out

    def _pos(self, level: proj.Level, pos, schema, on_floor: bool, lift: float = 0) -> list[int]:
        if not isinstance(pos, list | tuple) or len(pos) not in (2, 3):
            raise AgentError("pos is [x, y, z] (or [x, z] to put it on the floor)")
        if len(pos) == 2 and not on_floor:
            raise AgentError("pos [x, z] needs on_floor: give [x, y, z] instead")
        x, z = float(pos[0]), float(pos[-1])
        if not on_floor:
            return [round(x), round(float(pos[1]) + lift), round(z)]
        below = float(pos[1]) if len(pos) == 3 else None
        mesh = self._mesh(level.name)
        if below is None and schema is not None and schema.key in OPEN_FLOOR_KEYS:
            y = self._open_floor(mesh, x, z)
        else:
            y = self._floor(mesh, x, z, below)
        if y is None:
            where = f" at or below y {below:g}" if below is not None else ""
            raise AgentError(f"no floor under ({x:g}, {z:g}){where}: check surface_at, or give on_floor false")
        return [round(x), obj.on_ground(y, schema) + round(lift), round(z)]

    def _open_floor(self, mesh, x: float, z: float) -> float | None:
        """Where a door, exit or start point at (x, z) goes: the lowest dry floor with at least DOOR_CLEARANCE free
        above it (up to the next surface: a lintel's underside, a roof, or the top of a wall whose opening's top isn't
        modelled), so it lands inside a roofed room or doorway, not on top; floors under water, lava or a kill floor
        are skipped (the bridge, not the river bed). With several floors there, a note says which were passed over:
        give [x, y, z] for another."""
        from jfgstudio.gui.viewport import floor_of, heights_at
        floors = sorted(set(heights_at(mesh.collision, x, z, facing=1).tolist()))
        surfaces = sorted(set(heights_at(mesh.collision, x, z).tolist()))
        wet = [y for y in (floor_of(mesh.kind_triangles(k), x, z) for k in ("water", "lava", "kill")) if y is not None]
        chosen = None
        for f in floors:
            above = next((s for s in surfaces if s > f + 1), math.inf)
            if above - f >= DOOR_CLEARANCE and not any(w > f for w in wet):
                chosen = f
                break
        if chosen is None and floors:
            chosen = floors[-1]
        if len(floors) > 1:
            self._notes.append(f"({x:g}, {z:g}) has floors at {[round(f) for f in floors]}: placed on {round(chosen)}, "
                               "the lowest dry one with room above it; give [x, y, z] for another")
        return chosen

    def _add(self, level: proj.Level, made: list[dict], label: str) -> list[dict]:
        self._history(level).add(made, label)
        self._save()
        out = [self._describe(o) for o in made]
        out += [{"note": n} for n in self._notes]
        self._notes.clear()
        return out

    def place(self, level: str, type, pos, fields: dict | None = None, name: str | None = None,
              on_floor: bool = True, lift: float = 0, example=None, target: str | None = None,
              facing: float | None = None) -> list[dict]:
        """One object. A type that isn't fully decoded starts from a stock entry (from the catalog, or `example`).
        `facing`: degrees, 0 = towards -z (as start points face), for the type's yaw field in its own steps; or an
        [x, z] point to face (a chest faces the way the player comes in)."""
        lv = self._level(level)
        oid, schema, kind = self._type(type)
        checked = self._fields(schema, fields)
        if oid == EXPLOSIVE_DRUM and "size" not in checked:
            checked["size"] = DRUM_SIZE  # the game's drums are smaller than its crates (the schema's default)
        if oid == val.HEALTH_UPGRADE and "upgrade" not in checked:  # its own number, saved as a pack's game flag
            taken = self._flags_in_use()
            flag = next((f for f in val.PACK_FLAGS if f not in taken), None)
            if flag is None:
                raise AgentError("the pack has used every game flag a health upgrade can take (103-127)")
            checked["upgrade"] = flag - val.UPGRADE_FLAG
        at = self._pos(lv, pos, schema, on_floor, lift)
        if facing is not None:
            checked.update([self._facing(schema, facing, (at[0], at[2]))])
        ex = self._example(oid, schema, example)
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        try:
            o = placer.single(oid, at, ex, name=name, fields=checked or None)
        except ValueError as e:
            raise AgentError(str(e)) from e
        if kind:
            o["type"] = kind  # schema_for can't follow a kind to its base: name the schema
            if schema is not None:
                o["schema"] = schema.key
        if schema is not None and schema.key in ("door", "squadmember") and "schema" not in o:
            o["schema"] = schema.key
        if target is not None:
            self._target(target)
            if schema is None or schema.key != "exit":
                raise AgentError("only exits have a target")
            o["target"] = target
        return self._add(lv, placer.made, f"place {name or o['uid']}")

    @staticmethod
    def _facing(schema, degrees, at=None) -> tuple[str, int]:
        """(yaw field, value) for a facing in degrees (0 towards -z, 90 towards -x, 180 towards +z, 270 towards +x:
        counter-clockwise seen from above), in whatever angle steps the type's yaw field takes; or for an [x, z]
        point to face from `at` ((x, z))."""
        f = schema.role("yaw") if schema else None
        if f is None:
            raise AgentError(f"{schema.key if schema else 'this type'} has no facing (no yaw field)")
        if isinstance(degrees, list | tuple) and len(degrees) >= 2 and at is not None:
            x, z = float(degrees[0]), float(degrees[-1])
            return f.name, obj.value_of_angle(f, math.atan2(at[0] - x, at[1] - z))  # as _toward
        if isinstance(degrees, bool) or not isinstance(degrees, int | float):
            raise AgentError("facing is in degrees, or an [x, z] point to face")
        return f.name, obj.value_of_angle(f, math.radians(float(degrees)))

    def _target(self, target: str) -> None:
        try:
            t = refs.parse(target, "level")
        except refs.RefError as e:
            raise AgentError(str(e)) from e
        if t.is_pack and t.name not in self.p.levels:
            raise AgentError(f"{target}: no such level in the project")

    def place_squad(self, level: str, centre, member_type, members: int = 3, region=None, door=None,
                    door_type=None, door_pos=None, door_fields: dict | None = None, spread: float = 150,
                    name: str | None = None, squadron_example=None, member_example=None,
                    face=None) -> list[dict]:
        """A squadron, its members around `centre` and the region they keep to; with `door` ("new" with door_type
        and door_pos, or an existing door's uid) a door it keeps locked until all of them are dead. Members face
        `face` ([x, z]; default: the level's start point 0, the way the player comes) until they have a target.
        A squad whose squadron spawns its own flying members is placed where they drop in from, so they cruise
        FLYER_CRUISE over the floor (under any roof)."""
        lv = self._level(level)
        members = _as_int(members, "members")
        if not 1 <= members <= 16:
            raise AgentError("members is 1-16")
        m_oid, m_schema, m_kind = self._type(member_type)
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        sq_schema = self.schemas["squadron"]
        matched = self.squad_examples(m_oid) if squadron_example is None else None
        sq_ex = self._example(SQUADRON_ID, sq_schema, squadron_example if matched is None else list(matched[0]))
        # Flying enemies and some others have no member entries in the game's lists: their squadron spawns its own
        # members (count of them) round itself. Such a squad is the squadron alone.
        spawns_own = member_example is None and matched is not None and matched[1] is None
        m_ex = None if spawns_own else self._example(m_oid, None, member_example if member_example is not None
                                                     or matched is None or matched[1] is None else list(matched[1]))
        squadron_id = placer.free("squadron", "squadronId")
        door_id, existing = 0, None
        if door == "new":
            if door_type is None or door_pos is None:
                raise AgentError("a new door needs door_type (a door object type, behaviour 7) and door_pos")
            door_id = placer.free("door", "doorId")
        elif door is not None:
            existing = edit.find(lv, door)
            if existing is None or self._key(existing) != "door":
                raise AgentError(f"{door!r} is not a door of {level}")
            door_id = existing.get("fields", {}).get("doorId") or placer.free("door", "doorId")
        cx, cz = float(centre[0]), float(centre[-1])
        if region is None:
            h = prefabs.REGION_HALF
            region = [[cx - h, cz - h], [cx + h, cz - h], [cx + h, cz + h], [cx - h, cz + h]]
        if len(region) < 3:
            raise AgentError("region is at least three [x, z] corners, in order around it")
        corners = [self._corner(lv, c, (cx, cz)) for c in region]
        region_id = placer.polygon(corners)
        # Never the catalog's name: that is the player's game data, and uids and names are written into the project.
        label = name or (m_kind.rsplit("/", 1)[1].replace("_", " ") if m_kind else "enemy")
        target = self._face_point(lv, face)
        sq_fields = {"squadronId": squadron_id, "doorId": door_id, "region": region_id, "count": members,
                     "baddyType": m_oid, "trigger": prefabs.TRIGGER_AT_ONCE}
        if target is not None:
            sq_fields["heading"] = self._toward(sq_schema, (cx, cz), target)
        sq_pos = self._pos(lv, [cx, cz], sq_schema, True)
        flight = self._flight(lv, sq_ex, sq_pos) if spawns_own else None
        if flight is not None:
            sq_pos[1] = flight.pop("y")
            sq_fields.update(flight)
        squadron = placer.single(SQUADRON_ID, sq_pos, sq_ex, name=f"{label} squad", fields=sq_fields)
        if m_kind:
            squadron["kindFields"] = {"baddyType": m_kind}
        wet = []
        for k in range(0 if spawns_own else members):
            a = 2 * math.pi * k / members
            spot = self._dry_spot(lv.name, cx + spread * math.sin(a), cz + spread * math.cos(a), cx, cz)
            if spot is None:
                spot = (cx + spread * math.sin(a), cz + spread * math.cos(a))
                wet.append(k)
            at = self._pos(lv, list(spot), self.schemas["squadmember"], True)
            m_fields = {"squadron": squadron_id}
            if target is not None:
                m_fields["yaw"] = self._toward(self.schemas["squadmember"], (at[0], at[2]), target)
            m = placer.single(m_oid, at, m_ex, name=label, fields=m_fields)
            m["schema"] = "squadmember"
            if m_kind:
                m["type"] = m_kind
        if door == "new":
            d_oid, d_schema, _ = self._type(door_type)
            if d_schema is None or d_schema.key != "door":
                raise AgentError(f"{door_type!r} is not a door type (behaviour 7)")
            fields = {"keyLock": DOOR_LOCKED, **self._fields(d_schema, door_fields), "doorId": door_id}
            d = placer.single(d_oid, self._pos(lv, door_pos, d_schema, True), self._example(d_oid, d_schema, None),
                              name="door", fields=fields)
            d["schema"] = "door"
        made = self._add(lv, placer.made, f"squad {label}")
        if existing is not None:
            h = self._history(lv)
            h.set_value(existing["uid"], ("fields", "doorId"), door_id, "lock door")
            if not existing.get("fields", {}).get("keyLock"):
                h.set_value(existing["uid"], ("fields", "keyLock"), DOOR_LOCKED, "lock door")
            self._save()
            made.append(self._describe(existing))
        try:
            made += self.add_cover(lv.name, squadron["uid"])
        except AgentError as e:  # no geometry to look at yet
            made.append({"warning": f"no cover added ({e}): run add_cover once the level has geometry"})
        if spawns_own:
            made.append({"note": f"the game's squads of 0x{m_oid:X} have no member entries: the squadron spawns its "
                                 f"{members} member(s) itself, round it (a flying enemy rises to its own height)"})
        if squadron_example is None and matched is None:
            made.append({"warning": f"the game has no squad of 0x{m_oid:X}: the squadron's unknown bytes (behaviour, "
                                    "missions) come from another enemy's squad, so it may not move or fight right"})
        if wet:
            made.append({"warning": f"{len(wet)} member(s) found no dry floor near their place (lava, water, a pit "
                                    "or no floor there): move them (surface_at, find_flat)"})
        return made

    def _region_corners(self, lv: proj.Level, region) -> list[dict]:
        return prefabs.Placer(lv, self.schemas, self._behaviours()).corners(region)

    def _behaviour(self, sq: dict) -> int | None:
        """A squadron's normal behaviour (+0x0D): its field, else its stock entry's; None when unknown."""
        behaviour = sq.get("fields", {}).get("behaviour")
        if behaviour is None and sq.get("stock") and self.catalog() is not None:
            try:
                behaviour = self._catalog.stock_entry(sq["stock"]["list"], sq["stock"]["entry"])[0x0D]
            except Exception:  # noqa: BLE001 (the build reports a bad stock entry)
                return None
        return behaviour

    def _holes_of(self, lv: proj.Level, region) -> list[int]:
        return prefabs.Placer(lv, self.schemas, self._behaviours()).holes_of(region)

    def add_cover(self, level: str, squadron: str) -> list[dict]:
        """Makes the obstacles inside a squad's region holes of it (prefabs.Placer.cover): a smart squad's members
        only move to the corners of holes and inward corners of their region (decomp MSR mod #6). Each hole is the box
        around one obstacle; obstacles touching the region's edge are left (move the edge clear of them).
        Warns when the region still gives no cover."""
        lv = self._level(level)
        sq = self._object(lv, squadron)
        if self._key(sq) != "squadron":
            raise AgentError(f"{squadron!r} is not a squadron of {level}")
        mesh = self._mesh(level)
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        try:
            got = placer.cover(mesh, sq)
        except ValueError as e:
            raise AgentError(str(e)) from e
        made = self._add(lv, placer.made, "cover") if placer.made else []
        if got["small"]:
            made.append({"warning": f"{got['small']} obstacle(s) in region {got['region']} are under "
                                    f"{prefabs.COVER_MIN} across: too small to hide behind, so no hole. Cover the "
                                    "player can't see over is about 100 tall and 80 across (rock radius 90+)"})
        if not got["holes"] and not got["old"] and got["convex"] and self._behaviour(sq) == SMART:
            made.append({"warning": f"region {got['region']} has no obstacle inside it and no inward corner: its "
                                    "smart squad finds no cover to move to and stands and fires. Put a block or crate "
                                    "in it (then add_cover)"
                                    + (f" ({got['edge']} obstacle(s) touch its edge: move the edge clear of them)"
                                       if got["edge"] else "")})
        return made

    def _open_to_sky(self, level: str) -> bool:
        """A level some of whose floor has no roof over it (the game's fully roofed levels have no sky)."""
        from jfgstudio import layout_study
        try:
            found = layout_study.study(self._mesh(level).collision)
        except AgentError:
            return False
        return found is not None and found[0]["roofed"] < 0.9

    def _corner(self, lv: proj.Level, c, centre) -> list:
        """A region corner on the floor under it, or at the floor height of the region's centre where there is none
        (past a wall: a region is 2D, the game ignores its corners' height)."""
        try:
            return self._pos(lv, [c[0], c[-1]], self.schemas["regionpoint"], True)
        except AgentError:
            y = self._pos(lv, list(centre), self.schemas["regionpoint"], True)[1]
            return [round(float(c[0])), y, round(float(c[-1]))]

    def _face_point(self, lv: proj.Level, face) -> tuple[float, float] | None:
        """The point squads face: `face` [x, z], else the level's start point 0 (else its first), else None."""
        if face is not None:
            return float(face[0]), float(face[-1])
        starts = [o for o in lv.objects["objects"] if self._key(o) == "startpoint"]
        start = next((o for o in starts if o.get("fields", {}).get("startPoint") == 0), starts[0] if starts else None)
        return (float(start["pos"][0]), float(start["pos"][2])) if start else None

    def _toward(self, schema, at, target) -> int:
        """The yaw field value that faces from `at` to `target` ((x, z) each): the game turns a facer by
        atan2(at - target) on (x, z) (decomp MSR mod #6), 0 towards -z, as Studio's facing degrees."""
        f = schema.role("yaw")
        return obj.value_of_angle(f, math.atan2(at[0] - target[0], at[1] - target[1]))

    def _flight(self, lv: proj.Level, example, pos) -> dict | None:
        """Where a squadron that launches its own flyers goes (decomp MSR mod #6): the game's flyer squadrons sit
        high and cruise 16 x offsetY below (one at 409, offset -16: 153 over its floor). Kept: the stock launch
        (pitch, speed); set: the squadron's height and offsetY so they cruise FLYER_CRUISE over the floor, under any
        roof there. None for a squad that doesn't fly down to its spot (offsetY not below 0)."""
        if example is None or self.catalog() is None:
            return None
        e = self._catalog.stock_entry(*example)
        drop = int.from_bytes(e[0x1B:0x1C], "big", signed=True)
        if drop >= 0:
            return None
        from jfgstudio.gui.viewport import raycast
        floor = pos[1]
        cruise = floor + FLYER_CRUISE
        y = cruise - 16 * drop  # where the game's own sit: above the cruise height by the stock drop
        roof = raycast(self._mesh(lv.name).collision, (pos[0], floor + 5, pos[2]), (0.0, 1.0, 0.0))
        out = {}
        if roof is not None:
            top = floor + 5 + roof - FLYER_ROOM
            cruise = min(cruise, max(floor + 40, top - 20))
            y = max(cruise, min(y, top))
            if y - cruise < 120:
                out["launchPitch"] = 0  # no room to dive in: launch level
        out.update({"y": round(y), "offsetY": max(-128, min(127, round((cruise - y) / 16)))})
        return out

    def place_tribal(self, level: str, pos, type="0x11C", name: str | None = None) -> list[dict]:
        """A Tribal to rescue at `pos` ([x, z]): the player touches it and it's saved. One squad of one, copied from
        the game's own Tribal squads (they stand still, behaviour 9). The level must be on a planet: its region keeps
        the count apart from the game's own Tribals (the port's HUD count and stats screen, port request 14). Each
        Tribal of a level needs its own number (squadron id & 15), so 16 at most per level."""
        lv = self._level(level)
        oid, _, _ = self._type(type)
        if oid not in prefabs.TRIBAL_TYPES:
            raise AgentError(f"{type!r} is not a Tribal type ({', '.join(f'0x{t:X}' for t in prefabs.TRIBAL_TYPES)})")
        taken = {o["fields"].get("squadronId", 0) & 0xF for o in lv.objects["objects"] if self._key(o) == "squadron"
                 and o.get("fields", {}).get("baddyType") in prefabs.TRIBAL_TYPES}
        if len(taken) >= 16:
            raise AgentError("a level holds 16 Tribals at most (their numbers are squadron id & 15)")
        x, z = float(pos[0]), float(pos[-1])
        made = self.place_squad(level, [x, z], f"0x{oid:X}", members=1, spread=0, name=name or "tribal",
                                region=[[x - 120, z - 120], [x + 120, z - 120], [x + 120, z + 120], [x - 120, z + 120]])
        sq = next(o for o in made if o.get("schema") == "squadron")
        # Stand and wait, as the game's own Tribals (behaviour 9 in every set), whatever squad was copied.
        self.update_object(level, sq["uid"], fields={"behaviour": SENTRY, "alertBehaviour": SENTRY,
                                                     "hurtBehaviour": SENTRY})
        if sq["fields"]["squadronId"] & 0xF in taken:  # another Tribal has its number: the next free id that doesn't
            used = {o["fields"].get("squadronId") for o in lv.objects["objects"] if self._key(o) == "squadron"}
            new = next(i for i in range(1, 256) if i not in used and i & 0xF not in taken and i & 0xF)
            self.update_object(level, sq["uid"], fields={"squadronId": new})
        if not lv.level.get("planet"):
            made.append({"warning": "the level is on no planet: its Tribals count into no region of their own. Give "
                                    "it a planet (set_level planet) before release"})
        return [o for o in made if "no cover to move to" not in str(o.get("warning", ""))
                and "member entries" not in str(o.get("note", ""))]

    def place_glass(self, level: str, a, b, bottom: float, hit_points: int | None = None) -> list[dict]:
        """A breakable glass pane filling a square window opening in a wall: `a` and `b` ([x, z]) are the opening's
        sides along the wall, `bottom` its sill height. The pane is as tall as it is wide (the game's glass is
        square: 5.12 x size a side, decomp MSR mod #6), centred in the opening and turned along the wall. Model the
        opening square (100-520 across) and the pane fills it; nothing else holds it."""
        lv = self._level(level)
        ax, az, bx, bz = float(a[0]), float(a[-1]), float(b[0]), float(b[-1])
        width = math.hypot(bx - ax, bz - az)
        if not 60 <= width <= 1300:
            raise AgentError(f"an opening {width:.0f} wide: the game's panes are 5.12 x size (up to 255) a side")
        schema = self.schemas["glass"]
        fields = {"size": max(1, min(255, round(width / 5.12))),
                  "yaw": obj.value_of_angle(schema.field("yaw"), math.atan2(-(bz - az), bx - ax))}
        if hit_points is not None:
            fields["hitPoints"] = _as_int(hit_points, "hit_points")
        centre = ((ax + bx) / 2, float(bottom) + width / 2, (az + bz) / 2)
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        placer.single(0x12B, centre, None, name="glass", fields=fields)
        made = self._add(lv, placer.made, "glass")
        made.append({"note": f"a pane {fields['size'] * 5.12:.0f} a side; check in the game that it fills the opening "
                             "(diagonal walls are untested)"})
        return made

    def _dry(self, level: str, x: float, z: float) -> bool:
        """Solid floor at (x, z) with no lava, water or kill floor at or over it."""
        at = self.surface_at(level, [[x, z]])[0]
        return at["floor"] is not None and at.get("kind") != "lava" and "water" not in at and "kill" not in at

    def _dry_spot(self, level: str, x: float, z: float, cx: float, cz: float) -> tuple[float, float] | None:
        """(x, z) when it is dry, else the nearest dry spot found on rings around it (pulled towards the squad's
        centre (cx, cz) first); None when there is none close by."""
        if self._dry(level, x, z):
            return (x, z)
        for k in range(1, SPREAD_TRIES + 1):
            r = 40 * ((k + 7) // 8)
            a = 2 * math.pi * (k % 8) / 8
            for px, pz in ((x + (cx - x) * k / SPREAD_TRIES, z + (cz - z) * k / SPREAD_TRIES),
                           (x + r * math.cos(a), z + r * math.sin(a))):
                if self._dry(level, px, pz):
                    return (px, pz)
        return None

    # ------------------------------------------------------------------ kinds (reskinned types)

    def _base_slots(self, oid: int) -> list[tuple[int, int, str]]:
        """(width, height, transparency) of each texture slot of a stock type's model, from the player's catalog."""
        if oid in self._slots:
            return self._slots[oid]
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error}): it has the base's model")
        t = self._types.get(oid)
        if not t or t.get("kind") != "models" or not t.get("assets"):
            raise AgentError(f"0x{oid:X} has no model to reskin")
        from jfgformats import models
        if self._model_data is None:
            self._model_data = models.ModelData(self._catalog.assets)
        out = []
        for w, h, tex_id in self._model_data.model(t["assets"][0]).textures:
            try:
                alpha = self._model_data.texture_png(tex_id & 0x7FFF)[1]
            except Exception:  # noqa: BLE001 (a texture the reader can't take: its transparency is unknown)
                alpha = "unknown"
            out.append((w, h, alpha or "none"))
        self._slots[oid] = out
        return out

    def _kind_base(self, base) -> int:
        oid, _, kind = self._type(base)
        if kind:  # a kind of a kind is a kind of its base
            oid = refs.parse(self.p.kinds[kind.split("/", 1)[1]]["base"], "objtype").number()
        return oid

    def kind_slots(self, base) -> dict:
        """The texture slots a kind of `base` paints: each slot's size and whether the base's image there has
        transparency (cut-outs a solid texture would fill in), and the base model's size."""
        oid = self._kind_base(base)
        slots = [{"slot": i, "width": w, "height": h, "transparency": a}
                 for i, (w, h, a) in enumerate(self._base_slots(oid))]
        out = {"base": f"stock:objtype/0x{oid:X}", "slots": slots}
        size = self.model_size(oid)
        if size:
            out["modelSize"] = {k: size[k] for k in "xyz"}
        return out

    def make_kind(self, name: str, display_name: str, base, scale: float = 1.0,
                  textures: dict | None = None) -> dict:
        """A kind: `base` (a stock type with a model) with its own name, size (`scale`, 1 = the base's) and textures
        (`textures`: slot -> a PNG file of exactly that slot's size; or paint them with paint_kind_texture). Making
        one that exists updates it. Place it as type pack:kind/<name> (place, place_squad's member_type)."""
        if not proj.NAME_RE.match(name or ""):
            raise AgentError(f"{name!r}: kind names use lower case letters, digits, _ and -")
        oid = self._kind_base(base)
        slots = self._base_slots(oid)
        kind = copy.deepcopy(self.p.kinds.get(name) or {})
        kind.update({"format": proj.FORMAT, "name": display_name, "base": f"stock:objtype/0x{oid:X}",
                     "scale": float(scale)})
        kind.setdefault("textures", {})
        for slot, src in (textures or {}).items():
            k = _as_int(int(slot) if isinstance(slot, str) and slot.isdigit() else slot, "slot")
            if not 0 <= k < len(slots):
                raise AgentError(f"texture slot {k}: the base model has slots 0-{len(slots) - 1}")
            data = Path(src).read_bytes()
            from jfgformats import png
            try:
                w, h, _ = png.read_png(data)
            except Exception as e:  # noqa: BLE001
                raise AgentError(f"{src}: not a PNG the game's texture packer reads ({e})") from e
            if (w, h) != tuple(slots[k][:2]):
                raise AgentError(f"{src} is {w} x {h}; slot {k} takes {slots[k][0]} x {slots[k][1]}")
            dest = self.p.kind_texture(name, k)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            kind["textures"][str(k)] = dest.name
        errors = list(jsonschema.Draft202012Validator(proj._SCHEMAS["kind"]).iter_errors(kind))
        if errors:
            raise AgentError("not a valid kind: " + "; ".join(e.message for e in errors))
        self.p.kinds[name] = kind
        self._save()
        return {"kind": f"pack:kind/{name}", **kind, "slots": len(slots),
                "unpainted": [i for i in range(len(slots)) if str(i) not in kind["textures"]]}

    def paint_kind_texture(self, name: str, colour, accent=None, pattern: str = "noise", slots=None,
                           seed: int = 0) -> dict:
        """Draws new textures for a kind's slots (all, or `slots`) at each slot's size: `colour` and `accent` are
        RGB 0-255, `pattern` one of PATTERNS. Nothing of the base's images is used: the textures are the pack's own."""
        kind = self.p.kinds.get(name)
        if kind is None:
            raise AgentError(f"no kind {name!r} (make_kind first)")
        if pattern not in PATTERNS:
            raise AgentError(f"pattern is one of {', '.join(PATTERNS)}")
        main = self._rgb(colour, "colour")
        second = self._rgb(accent, "accent") if accent is not None else tuple(min(255, c + 60) for c in main)
        all_slots = self._base_slots(refs.parse(kind["base"], "objtype").number())
        chosen = range(len(all_slots)) if slots is None else [_as_int(k, "slot") for k in slots]
        from jfgformats import png
        painted = []
        for k in chosen:
            if not 0 <= k < len(all_slots):
                raise AgentError(f"texture slot {k}: the base model has slots 0-{len(all_slots) - 1}")
            w, h, _ = all_slots[k]
            dest = self.p.kind_texture(name, k)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(png.write_png("RGBA", w, h, _paint(w, h, main, second, pattern, seed + k)))
            kind.setdefault("textures", {})[str(k)] = dest.name
            painted.append({"slot": k, "width": w, "height": h})
        self._save()
        return {"kind": f"pack:kind/{name}", "painted": painted}

    @staticmethod
    def _rgb(v, what: str) -> tuple[int, int, int]:
        if not isinstance(v, list | tuple) or len(v) != 3:
            raise AgentError(f"{what} is [r, g, b], 0-255")
        return tuple(max(0, min(255, _as_int(c, what))) for c in v)

    def place_key_door(self, level: str, door_type, door_pos, key_pos, colour=None, facing: float | None = None,
                       key_level: str | None = None) -> list[dict]:
        """A door that needs a coloured key, and that key (in `key_level`, this level by default). `colour`: a key's
        name ("red key") or keyLock number; by default the first colour no door of the pack uses yet."""
        lv = self._level(level)
        door_schema = self.schemas["door"]
        names = door_schema.field("keyLock").enum
        used = {o.get("fields", {}).get("keyLock") for x in self.p.levels.values() for o in x.objects["objects"]
                if self._key(o) == "door"}
        if colour is None:
            lock = next((n for n in range(2, 2 + len(KEY_IDS)) if n not in used), None)
            if lock is None:
                raise AgentError("every key colour is used by a door of the pack already: give colour")
        elif isinstance(colour, str):
            lock = next((n for n, label in names.items() if label.lower() == colour.lower() and n >= 2), None)
            if lock is None:
                raise AgentError(f"{colour!r} is not a key; keys: {', '.join(v for k, v in names.items() if k >= 2)}")
        else:
            lock = _as_int(colour, "colour")
            if not 2 <= lock < 2 + len(KEY_IDS):
                raise AgentError(f"keyLock {lock} is not a key (2-{1 + len(KEY_IDS)})")
        self._level(key_level or level)
        door = self.place(lv.name, door_type, door_pos, fields={"keyLock": lock}, facing=facing,
                          name=names[lock] + " door")
        if door[0].get("schema") != "door":
            self.undo(lv.name)
            raise AgentError(f"{door_type!r} is not a door type (behaviour 7)")
        key = self.place(key_level or level, f"0x{KEY_IDS[lock - 2]:X}", key_pos, name=names[lock])
        return door + key

    def _flags_in_use(self) -> set[int]:
        used = {o.get("fields", {}).get("gameFlag") for lv in self.p.levels.values() for o in lv.objects["objects"]
                if self._key(o) == "target"}
        used |= {a.get("flag") for t in scripts.load_triggers(self.p) for a in t.get("then", [])
                 if a.get("do") == "flag"}
        used |= {val.UPGRADE_FLAG + o.get("fields", {}).get("upgrade", 0) for lv in self.p.levels.values()
                 for o in lv.objects["objects"] if obj.stock_id(o) == val.HEALTH_UPGRADE}
        return {f for f in used if isinstance(f, int)}

    def _move_squad(self, lv: proj.Level, sq: dict, to) -> list[dict]:
        """Moves a squadron, its members and its region's corners together (on the floor) so the squadron stands at
        `to`, as one undo step; a squadron walks the patrol route nearest to itself."""
        f = sq.get("fields", {})
        dx, dz = to[0] - sq["pos"][0], to[2] - sq["pos"][2]
        group = [o for o in lv.objects["objects"] if o is sq
                 or (self._key(o) == "squadmember" and o.get("fields", {}).get("squadron") == f.get("squadronId"))
                 or (self._key(o) == "regionpoint" and o.get("fields", {}).get("region") == f.get("region"))]
        changes = []
        for o in group:
            at = self._pos(lv, [o["pos"][0] + dx, o["pos"][2] + dz], obj.schema_for(o, self.schemas,
                                                                                    self._behaviours()), True)
            changes.append(edit.Change(o["uid"], ("pos",), list(o["pos"]), at))
        self._history(lv).do(edit.Command("move squad to its patrol", changes))
        self._save()
        return [{"note": f"moved the squad ({len(group)} objects: squadron, members, region) by ({dx:g}, {dz:g}) "
                         "to the route's first node: a squadron walks the route nearest to itself"}]

    def place_switch_door(self, level: str, switch_pos, door=None, door_type=None, door_pos=None,
                          saved: bool = True, flag: int | None = None, switch_facing: float | None = None,
                          door_facing: float | None = None, switch_lift: float = 0) -> list[dict]:
        """A shootable switch (the game's target) that keeps a door locked until it is shot: `door` "new" (with
        door_type and door_pos) or a door's uid. Shot, it opens the door and sets a game flag of the pack's (103-127,
        the first one free; `flag` to choose), so the door stays open after a reload; `saved` false: no flag, the door
        locks again when the level reloads. `switch_lift`: its height over the floor (on a wall, say)."""
        lv = self._level(level)
        target_schema = self.schemas["target"]
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        used_ids = {o.get("fields", {}).get("doorId") for o in lv.objects["objects"]
                    if self._key(o) in ("door", "squadron", "target")}
        existing = None
        if door == "new":
            if door_type is None or door_pos is None:
                raise AgentError("a new door needs door_type (a door object type, behaviour 7) and door_pos")
            door_id = next(n for n in itertools.count(1) if n not in used_ids)
        elif door is not None:
            existing = self._object(lv, door)
            if self._key(existing) != "door":
                raise AgentError(f"{door!r} is not a door of {level}")
            door_id = existing.get("fields", {}).get("doorId") or next(n for n in itertools.count(1)
                                                                       if n not in used_ids)
        else:
            raise AgentError('give door: "new" (with door_type and door_pos) or a door\'s uid')
        if not saved:
            game_flag = 0
        elif flag is not None:
            game_flag = _as_int(flag, "flag")
            problem = val._flag_problem(game_flag)
            if problem and problem[0] == val.ERROR:
                raise AgentError(problem[1])
        else:
            taken = self._flags_in_use()
            game_flag = next((f for f in [*val.PACK_FLAGS, *val.PACK_FLAGS_MORE] if f not in taken), None)
            if game_flag is None:
                raise AgentError("the pack has used every flag it can (103-127, 301-351): use saved false")
        fields = {"doorId": door_id, "gameFlag": game_flag}
        if switch_facing is not None:
            fields.update([self._facing(target_schema, switch_facing,
                                        (float(switch_pos[0]), float(switch_pos[-1])))])
        placer.single(0x48, self._pos(lv, switch_pos, target_schema, True, switch_lift), name="door switch",
                      fields=fields)
        if door == "new":
            d_oid, d_schema, _ = self._type(door_type)
            if d_schema is None or d_schema.key != "door":
                raise AgentError(f"{door_type!r} is not a door type (behaviour 7)")
            d_fields = {"doorId": door_id}  # no key value: the switch's lock does the locking
            if door_facing is not None:
                d_fields.update([self._facing(d_schema, door_facing, (float(door_pos[0]), float(door_pos[-1])))])
            d = placer.single(d_oid, self._pos(lv, door_pos, d_schema, True), self._example(d_oid, d_schema, None),
                              name="switch door", fields=d_fields)
            d["schema"] = "door"
        made = self._add(lv, placer.made, "switch door")
        if existing is not None and existing.get("fields", {}).get("doorId") != door_id:
            made.append(self.update_object(lv.name, existing["uid"], fields={"doorId": door_id}))
        made.append({"note": f"the switch locks door id {door_id} itself (the door's keyLock stays 0, 'not locked')"})
        if game_flag:
            made.append({"note": f"game flag {game_flag}: set when the switch is shot (saved with the player's game)"})
        return made

    def place_patrol(self, level: str, points: list, loop: bool = False, squadron: str | None = None,
                     wait: int | None = None) -> list[dict]:
        """A patrol route: nodes on the floor at `points` ([x, z] or [x, y, z]), linked in order both ways (a chain
        is walked there and back; `loop` joins the last to the first). With `squadron` (a squadron's uid) it is put in
        patrol mode (behaviour 2, the node patrol, sets off by itself from the node nearest to it) and fights as a
        smart squad once it sees the hero (alert behaviour 0x11; decomp MSR mod #6, not play-tested). `wait`: the
        wait at each node (stock 15)."""
        lv = self._level(level)
        if not isinstance(points, list) or len(points) < 2:
            raise AgentError("a patrol needs at least two points")
        node_schema, link_schema = self.schemas["patrolpoint"], self.schemas["patrolpath"]
        node_ex = self._example(prefabs.PATROL_POINT_ID, node_schema, None)
        link_ex = self._example(prefabs.PATROL_PATH_ID, link_schema, None)
        sq = None
        if squadron is not None:
            sq = self._object(lv, squadron)
            if self._key(sq) != "squadron":
                raise AgentError(f"{squadron!r} is not a squadron of {level}")
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        nodes = [placer.patrol_point(self._pos(lv, pt, node_schema, True), node_ex) for pt in points]
        if wait is not None:
            for n in nodes:
                n["fields"]["wait"] = self._fields(node_schema, {"wait": wait})["wait"]
        pairs = list(zip(nodes, nodes[1:], strict=False))
        if loop and len(nodes) > 2:
            pairs.append((nodes[-1], nodes[0]))
        for a, b in pairs:
            placer.patrol_link(a, b, link_ex)
        made = self._add(lv, placer.made, "patrol")
        if sq is not None and min(math.dist(n["pos"][::2], sq["pos"][::2]) for n in nodes) > PATROL_NEAR:
            made += self._move_squad(lv, sq, nodes[0]["pos"])
        if sq is not None:
            self.catalog()
            changes = squads.patrol(lv, sq, self._squad_index(), self._key, self._catalog)
            self._history(lv).do(edit.Command(f"patrol {squadron}", changes))
            self._save()
            made.append(self._describe(sq))
            others = [o for o in lv.objects["objects"] if self._key(o) == "patrolpoint" and o not in nodes]
            mine = min(math.dist(n["pos"], sq["pos"]) for n in nodes)
            if others and min(math.dist(o["pos"], sq["pos"]) for o in others) < mine:
                made.append({"warning": "another patrol's node is nearer the squadron than this route: it starts on "
                                        "the network nearest to itself. Move the squadron next to this route"})
        return made

    def _squad_index(self) -> dict:
        """Every stock squadron by its enemy (squads.index)."""
        if self._squads is None:
            self._squads = squads.index(self._catalog) if self._catalog is not None else {}
        return self._squads

    def squad_examples(self, member_oid: int, mode: int | None = None):
        """(squadron entry, member entry) of a stock squad of `member_oid`, [list, entry] each (the member None when
        its members aren't list entries). A squadron's unnamed bytes (behaviour, missions, hearing ...) must come from
        a squadron of the same enemy (decomp MSR, "Squadron entry"), and in the mode it is used in: `mode` (kind's low
        4 bits: 6 patrol), else the enemy's usual one (2 in 893 of the game's 924 squads; a squad copied from a rare
        mode-1 one ran into place and crouched without firing, play-tested). Of those, a smart one (behaviour 0x11:
        walks inside its region to the hero) before any other, and a sentry (9: never walks, faces one way) last.
        None when the game has no squad of that enemy in that mode, or there is no catalog."""
        if self.catalog() is None:
            return None
        return squads.examples(self._squad_index(), member_oid, mode)

    def fix_squad(self, level: str, uid: str) -> dict:
        """Points a squadron (and its members) at a stock squad of its own enemy, keeping every field set, makes it
        spawn at once (a stock squad's trigger zone is where it stood in its own level) and winds its region
        counter-clockwise: for squads that stand still, never turn, never appear or behave like another enemy."""
        lv = self._level(level)
        sq = self._object(lv, uid)
        if self._key(sq) != "squadron":
            raise AgentError(f"{uid!r} is not a squadron of {level}")
        self.catalog()
        try:
            changes, fixed = squads.fix(lv, sq, self._squad_index(), self._key)
        except ValueError as e:
            raise AgentError(str(e)) from e
        self._history(lv).do(edit.Command(f"fix squad {uid}", changes))
        self._save()
        return fixed

    def mismatched_squads(self) -> list[tuple[str, str, int, int]]:
        """(level, uid, the squad's enemy, the copied stock squadron's enemy) for squadrons copying another enemy's, one
        in another mode, or a sentry where the game has a walking squad of that enemy."""
        if self.catalog() is None:
            return []
        import struct
        out = []
        for lv in self.p.levels.values():
            for o in lv.objects["objects"]:
                if self._key(o) != "squadron" or not o.get("stock"):
                    continue
                try:
                    e = self._catalog.stock_entry(o["stock"]["list"], o["stock"]["entry"])
                except Exception:  # noqa: BLE001 (the build reports a bad stock entry)
                    continue
                stock_baddy = struct.unpack_from(">H", e, 0x36)[0] if len(e) >= 0x38 else None
                ours = o.get("fields", {}).get("baddyType", stock_baddy)
                kind = o.get("fields", {}).get("kind")
                found = self._squad_index().get(ours, [])
                if not found or len(e) <= 0x24:
                    continue
                # the mode it runs in: its own kind, else its enemy's usual one; the copied squad should be in it
                runs = kind & 0xF if kind is not None else Counter(f[0] for f in found).most_common(1)[0][0]
                best = self.squad_examples(ours, runs)
                fits = best is not None
                # a sentry (never walks or turns) copied where the game has a squad of that enemy that walks
                behaviour = o.get("fields", {}).get("behaviour", e[0x0D])
                stands = ours in prefabs.TRIBAL_TYPES  # Tribals stand on purpose
                sentry = fits and behaviour == SENTRY and not stands and \
                    self._catalog.stock_entry(*best[0])[0x0D] != SENTRY
                if stock_baddy is not None and (ours != stock_baddy or (fits and e[0x24] & 0xF != runs) or sentry):
                    out.append((lv.name, o["uid"], ours, stock_baddy))
        return out

    def base_objects(self, level: proj.Level) -> list[dict]:
        """The start points and exits of the game level an edit adds to (read-only, from the player's catalog; never
        written into the project). Empty for a level of the pack's own or without a catalog."""
        if level.base is None or self.catalog() is None:
            return []
        info = self._catalog.levels()[level.base]
        out = []
        for n in (info["objectList0"], info["objectList1"]):
            try:
                entries = self._catalog.objlist(n)
            except (IndexError, ValueError):
                continue
            for e in entries:
                if not e or e[0] not in (prefabs.START_ID, prefabs.EXIT_ID):
                    continue
                schema = self.schemas["startpoint" if e[0] == prefabs.START_ID else "exit"]
                if len(e[1]) < schema.min_size:
                    continue
                d = obj.decode_entry(e[1], schema)
                out.append({"type": f"stock:objtype/0x{e[0]:X}", "schema": schema.key, "pos": d["pos"],
                            "fields": d["fields"], "base": True})
        return out

    def _base_starts(self, level: proj.Level) -> set[int]:
        return {o["fields"]["startPoint"] for o in self.base_objects(level) if o["schema"] == "startpoint"}

    def connect(self, from_level: str, exit_pos, to_level: str, arrive_pos=None, arrive_yaw: float = 0,
                radius: int = 70, name: str | None = None, arrive_start_point: int | None = None) -> list[dict]:
        """An exit in `from_level` and the start point it brings the player to in `to_level`: a new one at
        `arrive_pos` (a number neither the project's level nor, for an edit of a game level, the game level uses),
        or with `arrive_start_point` one that is already there. `arrive_yaw` is the facing in degrees (0 = towards
        -z)."""
        src, dst = self._level(from_level), self._level(to_level)
        if src is dst:
            raise AgentError("an exit into its own level reloads the level (its enemies and pickups come back) and "
                             "looks like a hidden teleport: join areas of a level by passages, and use exits to leave "
                             "for another level")
        st = self.schemas["startpoint"]
        dst_placer = prefabs.Placer(dst, self.schemas, self._behaviours())
        mine = {o.get("fields", {}).get("startPoint") for o in dst.objects["objects"] if self._key(o) == "startpoint"}
        base = self._base_starts(dst)
        arrived = []
        if arrive_start_point is not None:
            number = _as_int(arrive_start_point, "arrive_start_point")
            if number not in mine | base:
                own = sorted(n for n in mine if n is not None)
                raise AgentError(f"{to_level} has no start point {number} (its own: {own}"
                                 + (f"; the game level's: {sorted(base)}" if base else "") + ")")
        else:
            if arrive_pos is None:
                raise AgentError("give arrive_pos (a new start point) or arrive_start_point (an existing one)")
            number = next(n for n in itertools.count(0) if n not in mine | base)
            yaw_f = st.role("yaw")
            yaw = obj.value_of_angle(yaw_f, math.radians(float(arrive_yaw))) if yaw_f else 0
            dst_placer.single(prefabs.START_ID, self._pos(dst, arrive_pos, st, True), name=f"from {from_level}",
                              fields={"player": 0, "startPoint": number, yaw_f.name if yaw_f else "yaw": yaw})
            arrived = self._add(dst, dst_placer.made, "arrival")
        ex = self.schemas["exit"]
        src_placer = prefabs.Placer(src, self.schemas, self._behaviours())
        o = src_placer.single(prefabs.EXIT_ID, self._pos(src, exit_pos, ex, True), name=name or f"to {to_level}",
                              fields={"radius": _as_int(radius, "radius"), "startPoint": number})
        o["target"] = f"pack:level/{to_level}"
        return self._add(src, src_placer.made, "exit") + arrived

    def _key(self, o: dict) -> str | None:
        s = obj.schema_for(o, self.schemas, self._behaviours())
        return s.key if s else None

    def _object(self, level: proj.Level, uid: str) -> dict:
        o = edit.find(level, uid)
        if o is None or uid == edit.LEVEL:
            raise AgentError(f"no object {uid!r} in {level.name}")
        return o

    def update_object(self, level: str, uid: str, fields: dict | None = None, pos=None, on_floor: bool | None = None,
                      name: str | None = None, target: str | None = None, facing: float | None = None) -> dict:
        """Changes an object's fields (merged), position, name or exit target, as one undo step."""
        lv = self._level(level)
        self._writable(lv, [uid])
        o = self._object(lv, uid)
        schema = obj.schema_for(o, self.schemas, self._behaviours())
        changes = []
        values = self._fields(schema, fields)
        if facing is not None:
            where = pos if pos is not None else o["pos"]
            values.update([self._facing(schema, facing, (float(where[0]), float(where[-1])))])
        for k, v in values.items():
            changes.append(edit.Change(uid, ("fields", k), o.get("fields", {}).get(k), v))
        if pos is not None:
            floor = on_floor if on_floor is not None else len(pos) == 2  # [x, z]: onto the floor, as place does
            changes.append(edit.Change(uid, ("pos",), list(o["pos"]), self._pos(lv, pos, schema, floor)))
        if name is not None:
            changes.append(edit.Change(uid, ("name",), o.get("name"), name or None))
        if target is not None:
            if schema is None or schema.key != "exit":
                raise AgentError("only exits have a target")
            self._target(target)
            changes.append(edit.Change(uid, ("target",), o.get("target"), target))
        self._history(lv).do(edit.Command(f"edit {uid}", changes))
        self._save()
        return self._describe(o)

    def delete_objects(self, level: str, uids: list[str]) -> dict:
        """The level's own objects are deleted; the game level's (with editStock on) are removed from it, which
        revert_game_objects undoes."""
        lv = self._level(level)
        self._writable(lv, uids)
        for u in uids:
            self._object(lv, u)
        self._history(lv).delete(set(uids))
        self._save()
        return {"deleted": list(uids)}

    def game_objects(self, level: str, type=None, include_removed: bool = False) -> dict:
        """A stock-level edit's game level objects (uids "stock:<list>:<index>"), with the project's changes on them:
        each one's type, name (from the player's game), position and whether it was changed or removed. `type`
        filters by object type."""
        lv = self._level(level)
        if lv.base is None:
            raise AgentError(f"{level} is a level of the pack's own: it has no game objects (edit_stock_level makes "
                             "a level that edits one of the game's)")
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error}): it holds the game's objects")
        want = self._type(type)[0] if type is not None else None
        out = []
        for o in stock_edit.live(lv):
            oid = o["game"]["id"]
            if (want is not None and oid != want) or (o.get("deleted") and not include_removed):
                continue
            t = (self._types or {}).get(oid) or {}
            d = {"uid": o["uid"], "type": f"0x{oid:X}", "name": o.get("name") or t.get("name", ""),
                 "pos": [round(v) for v in o["pos"]]}
            key = self._key(o)
            if key == "squadron":  # named by what it holds: its enemy, or a Tribal to rescue
                g = o["game"]
                schema = self.schemas["squadron"]
                baddy = {**obj.decode_entry(self._catalog.stock_entry(g["list"], g["index"]), schema)["fields"],
                         **o.get("fields", {})}.get("baddyType")
                if baddy:
                    name = ((self._types or {}).get(baddy) or {}).get("name", f"0x{baddy:X}")
                    d["holds"] = f"0x{baddy:X}"
                    if not o.get("name"):
                        d["name"] = f"Tribal ({name})" if baddy in prefabs.TRIBAL_TYPES else f"{name} squad"
            if key:
                d["schema"] = key
            if o.get("deleted"):
                d["removed"] = True
            elif stock_edit.changed(o):
                d["changed"] = True
            out.append(d)
        lists = sorted({o["game"]["list"] for o in stock_edit.live(lv)})
        shared = {n: stock_edit.sharing(self._catalog, lv.base, n) for n in lists}
        return {"editable": stock_edit.editable(lv), "objects": out[:LIST_LIMIT * 4],
                "more": max(0, len(out) - LIST_LIMIT * 4),
                "note": "read-only until edit_game_objects(level, true); then update_object and delete_objects take "
                        "these uids. The pack carries only the changes."
                        + ("".join(f" The game's list {n} is also level(s) {', '.join(map(str, s[:5]))}'s: its "
                                   "changes show there too." for n, s in shared.items() if s))}

    def edit_game_objects(self, level: str, editable: bool = True) -> dict:
        """Lets the game level's own objects be changed and removed (or makes them read-only again; their changes
        stay), as one undo step."""
        lv = self._level(level)
        if lv.base is None:
            raise AgentError(f"{level} is a level of the pack's own: it has no game objects")
        self._history(lv).set_value(edit.LEVEL, ("editStock",), True if editable else None,
                                    "edit game objects" if editable else "lock game objects")
        self._save()
        return {"level": level, "editable": stock_edit.editable(lv)}

    def revert_game_objects(self, level: str, uids: list[str] | None = None) -> dict:
        """Puts the game level's objects (`uids`, or all of them) back as the game has them: position, fields,
        name, and removed ones back in the level. One undo step."""
        lv = self._level(level)
        targets = [o for o in stock_edit.live(lv) if (uids is None or o["uid"] in uids) and stock_edit.changed(o)]
        changes = []
        for o in targets:
            g = o["game"]
            for path, old, new in ((("pos",), list(o["pos"]), list(g["pos"])),
                                   (("fields",), dict(o.get("fields", {})), {}),
                                   (("name",), o.get("name"), None), (("deleted",), o.get("deleted"), None)):
                if old != new and not (old in (None, {}) and new in (None, {})):
                    changes.append(edit.Change(o["uid"], path, old, new))
        self._history(lv).do(edit.Command("revert game objects", changes))
        self._save()
        return {"reverted": [o["uid"] for o in targets]}

    # ------------------------------------------------------------------ enemy tables and dependencies

    def _baddy_row(self, object) -> tuple[int, dict]:
        """(row, stock row) of a baddy named by its member object id (0xE0, a schema key) or {"row": n}."""
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error}): the tables come from the ROM")
        from jfgformats import baddies
        rows = self._catalog.baddies()["rows"]
        if isinstance(object, dict) and "row" in object:
            row = _as_int(object["row"], "row")
        else:
            oid = self._type(object)[0]
            row = baddies.row_of(oid)
            if row < 0:
                raise AgentError(f"object 0x{oid:X} is not a baddy (no row in BaddyTypeToIndex)")
        if not 0 <= row < len(rows):
            raise AgentError(f"row {row} is not a baddy row (0-{len(rows) - 1})")
        return row, rows[row]

    def baddy_anims(self, object) -> dict:
        """A baddy's animation set as the pack leaves it: each entry's fields (decode_anim's), the pack's patch on it,
        and the actions more than 16 entries answer (the game's pick list crashes past that)."""
        from jfgformats import baddies

        from jfgstudio import baddy_patches as bp
        row, stock = self._baddy_row(object)
        rows = bp.applied(self._catalog.baddies()["rows"], self.p.baddies)
        patches = {p["anim"]: p for p in self.p.baddies if p["row"] == row}
        anims = []
        for k, a in enumerate(rows[row]["anims"]):
            e = {"anim": k, **{f: a[f] for f in bp.FIELDS}}
            if k in patches:
                e["patched"] = {f: {"stock": stock["anims"][k][f], "pack": v} for f, v in patches[k]["set"].items()}
            anims.append(e)
        out = {"row": row, "objects": [f"0x{o:X}" for o in stock["objects"]], "anims": anims}
        over = baddies.overfull_actions(rows[row]["anims"])
        if over:
            out["overfull"] = {str(a): n for a, n in sorted(over.items())}
        return out

    def baddy_patch(self, object, anim, set: dict) -> dict:
        """Patches entries (`anim`: one or a list) of a baddy's animation set with `set` (action, altAction, weight,
        stance, fireSet (null = none), loops); "expect" holds the stock values. An empty `set` removes the pack's
        patch of those entries. Refused when it leaves an action over 16 entries and worse than before."""
        from jfgformats import baddies

        from jfgstudio import baddy_patches as bp
        row, stock = self._baddy_row(object)
        anims = [_as_int(a, "anim") for a in (anim if isinstance(anim, list) else [anim])]
        if not isinstance(set, dict):
            raise AgentError("set is an object of fields: {\"action\": 53}")
        try:
            bp.check_set(set)
        except bp.PatchError as e:
            raise AgentError(str(e)) from None
        for k in anims:
            if not 0 <= k < len(stock["anims"]):
                raise AgentError(f"row {row} has {len(stock['anims'])} animations (0-{len(stock['anims']) - 1}); "
                                 f"there is no anim {k}")
        rows = self._catalog.baddies()["rows"]
        before = baddies.overfull_actions(bp.applied(rows, self.p.baddies)[row]["anims"])
        patches = [dict(p) for p in self.p.baddies]
        for k in anims:
            old = next((p for p in patches if p["row"] == row and p["anim"] == k), None)
            if old is not None:
                patches.remove(old)
            values = {**(old["set"] if old else {}), **set} if set else {}
            values = {f: v for f, v in values.items() if v != stock["anims"][k][f]}  # the stock's own: no patch
            if values:
                patches.append({"row": row, "anim": k, "expect": {f: stock["anims"][k][f] for f in values},
                                "set": values})
        patches.sort(key=lambda p: (p["row"], p["anim"]))
        after = baddies.overfull_actions(bp.applied(rows, patches)[row]["anims"])
        worse = {a: n for a, n in after.items() if n > before.get(a, 0)}
        if worse:
            what = ", ".join(f"{n} animations on action {a}" for a, n in worse.items())
            raise AgentError(f"row {row}: that leaves {what}; the game's pick list holds {baddies.PICK_LIST} and "
                             "crashes past it")
        self.p.baddies[:] = patches
        self._save()
        out = {"row": row, "patches": [p for p in patches if p["row"] == row]}
        if after:
            out["warning"] = (f"row {row} still has more than {baddies.PICK_LIST} animations on action(s) "
                              f"{', '.join(map(str, sorted(after)))}: the build refuses it until fixed")
        return out

    def add_dependency(self, id: str, version: str) -> dict:
        """Another pack this one needs (mod.json "dependencies"): a library such as restored content."""
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_.\-]*$", id or ""):
            raise AgentError(f"{id!r} is not a mod id (lower case letters, digits, _ . -)")
        if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", version or ""):
            raise AgentError(f"{version!r} is not a version (1.2.3)")
        if id == self.p.id:
            raise AgentError("a pack can't depend on itself")
        deps = [d for d in self.p.meta.get("dependencies", []) if d["id"] != id] + [{"id": id, "version": version}]
        self.p.meta["dependencies"] = deps
        self._save()
        return {"dependencies": deps}

    def remove_dependency(self, id: str) -> dict:
        deps = [d for d in self.p.meta.get("dependencies", []) if d["id"] != id]
        if len(deps) == len(self.p.meta.get("dependencies", [])):
            raise AgentError(f"the pack has no dependency {id!r}")
        self.p.meta["dependencies"] = deps
        self._save()
        return {"dependencies": deps}

    def undo(self, level: str) -> dict:
        lv = self._level(level)
        label = self._history(lv).undo()
        self._meshes.clear()  # an undone re-import puts the old geometry back
        self._save()
        return {"undone": label}

    def redo(self, level: str) -> dict:
        lv = self._level(level)
        label = self._history(lv).redo()
        self._meshes.clear()
        self._save()
        return {"redone": label}

    # ------------------------------------------------------------------ planets, galaxies, scripts

    def _write_doc(self, schema: str, store: dict, name: str, doc: dict) -> dict:
        if not proj.NAME_RE.match(name or ""):
            raise AgentError(f"{name!r}: names use lower case letters, digits, _ and -")
        doc = {"format": proj.FORMAT, **doc}
        errors = list(jsonschema.Draft202012Validator(proj._SCHEMAS[schema]).iter_errors(doc))
        if errors:
            raise AgentError(f"not a valid {schema}: " + "; ".join(
                f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}" for e in errors))
        store[name] = doc
        self._save()
        return {schema: name, "problems": [self._problem(p) for p in val.validate(self.p, self.schemas)
                                           if p.severity != val.INFO and name in p.file]}

    def set_planet(self, name: str, doc: dict) -> dict:
        return self._write_doc("planet", self.p.planets, name, doc)

    def set_galaxy(self, name: str, doc: dict) -> dict:
        return self._write_doc("galaxy", self.p.galaxies, name, doc)

    def get_triggers(self) -> list[dict]:
        return scripts.load_triggers(self.p)

    def set_triggers(self, triggers: list[dict]) -> dict:
        if not isinstance(triggers, list):
            raise AgentError("triggers is a list")
        scripts.save_triggers(self.p, triggers)
        return {"triggers": len(triggers), "problems": scripts.check(self.p)}

    def write_script(self, name: str, text: str) -> dict:
        if not proj.NAME_RE.match(name or ""):
            raise AgentError(f"{name!r}: script names use lower case letters, digits, _ and -")
        path = self.p.path / "scripts" / f"{name}.lua"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return {"script": f"scripts/{name}.lua", "problems": scripts.check(self.p)}

    # ------------------------------------------------------------------ checking, building, playing

    @staticmethod
    def _problem(p: val.Problem) -> dict:
        d = {"severity": p.severity, "rule": p.rule, "file": p.file, "message": p.message}
        if p.uid:
            d["uid"] = p.uid
        return d

    def validate(self, notes: bool = False, geometry: bool = True) -> dict:
        """The validator's problems, and (with `geometry`) objects each level's geometry leaves buried, floating,
        sunk or over a pit, as warnings."""
        problems = val.validate(self.p, self.schemas)
        if geometry:
            for name in self.p.levels:
                try:
                    findings = self.check_placement(name)
                except AgentError:
                    continue  # no geometry to check against (missing file, stock track without a catalog)
                problems += [val.Problem(val.WARNING, "placement", f"levels/{name}/objects.json",
                                         f"{f['problem']}" + (f" by {f['by']:g}" if "by" in f else "")
                                         + (f"; it would stand at {f['standOn']}" if "standOn" in f else "")
                                         + ("; one side hovers: move it to level ground (find_flat, or a flat "
                                            "clearing in the geometry)" if f["problem"] == "on a slope" else ""),
                                         f["uid"]) for f in findings]
            for name, lv in self.p.levels.items():
                if not lv.level.get("track", "").startswith("pack:"):
                    continue  # the game's own track: its water and floors are the game's
                try:
                    mesh = self._mesh(name)
                    hovering, folded = self._hovering_water(mesh), self._folded_floors(mesh)
                except AgentError:
                    hovering, folded = [], []
                if folded:
                    problems.append(val.Problem(val.WARNING, "folded-floor", f"levels/{name}/level.json",
                                                f"the floor is folded over at {len(folded)} place(s) (one at "
                                                f"{folded[0]}): a face turned down lies on it, and the hero is "
                                                "falling all the time there"))
                if hovering:
                    problems.append(val.Problem(val.WARNING, "water", f"levels/{name}/level.json",
                                                f"water hovers over the floor at {len(hovering)} edge(s) (one at "
                                                f"{hovering[0]}): sink it into a hollow so its edge meets the bank"))
            for name, lv in self.p.levels.items():  # nothing above: black where the sky should be
                if lv.base is None and lv.level.get("sky", -1) == -1 and self._open_to_sky(name):
                    problems.append(val.Problem(val.WARNING, "sky", f"levels/{name}/level.json",
                                                "the level has no sky (sky -1) but is open above: the player sees "
                                                "black there. Set one (set_level sky): a game level's of the same "
                                                "theme, as stock_levels lists them (fully roofed levels need none)"))
            for name, lost in self._lost_collision().items():
                problems.append(val.Problem(val.ERROR, "collision", f"levels/{name}/level.json",
                                            f"{lost} colliding triangles of its geometry come after non-colliding "
                                            "(nocollide) ones: the game gives them no collision and the player falls "
                                            "through. Make the decoration solid, or the see-through surfaces after "
                                            "it colliding"))
            for name in self.p.levels:  # lava over nothing: a hole for Juno
                try:
                    holes = self.lava_holes(name)
                except AgentError:
                    continue
                if holes:
                    problems.append(val.Problem(val.WARNING, "lava-floor", f"levels/{name}/level.json",
                                                f"lava with no solid floor under it at {holes[0]}"
                                                + (f" and {len(holes) - 1} more places" if len(holes) > 1 else "")
                                                + ": Juno isn't held up by lava (he passes through it, unhurt) and "
                                                f"falls through. Put a solid floor at most {LAVA_FLOOR_DEPTH} under "
                                                "every lava surface"))
        for lv in self.p.levels.values():  # smart squads with nowhere to move to
            for o in lv.objects["objects"]:
                if self._key(o) != "squadron":
                    continue
                region = o.get("fields", {}).get("region")
                corners = self._region_corners(lv, region)
                if self._behaviour(o) != SMART or len(corners) < 3 or self._holes_of(lv, region):
                    continue
                if prefabs.convex([(c["pos"][0], c["pos"][2]) for c in corners]):
                    problems.append(val.Problem(val.WARNING, "squad-cover", f"levels/{lv.name}/objects.json",
                                                f"region {region} is convex with no holes: the smart squad finds no "
                                                "cover to move to and stands and fires. add_cover makes the "
                                                "obstacles in it holes", o["uid"]))
        for name, uid, ours, theirs in self.mismatched_squads():
            problems.append(val.Problem(val.WARNING, "squad-stock", f"levels/{name}/objects.json",
                                        f"the squadron copies a stock squad (of 0x{theirs:X}) that doesn't fit its "
                                        f"0x{ours:X} members or its mode: they may stand still or not fight. "
                                        "fix_squad points it at a fitting squad of their own enemy", uid))
        for lv in self.p.levels.values():  # stock trigger zones: placed for the stock level, not this one
            for o in lv.objects["objects"]:
                if self._key(o) != "squadron":
                    continue
                trigger = o.get("fields", {}).get("trigger")
                if trigger is None and o.get("stock") and self.catalog() is not None:
                    try:
                        trigger = self._catalog.stock_entry(o["stock"]["list"], o["stock"]["entry"])[0x29]
                    except Exception:  # noqa: BLE001 (the build reports a bad stock entry)
                        continue
                if trigger is not None and trigger & 0xF == 1 and lv.base is None:
                    problems.append(val.Problem(val.WARNING, "squad-trigger", f"levels/{lv.name}/objects.json",
                                                "the squadron spawns when a hero enters a zone copied from a stock "
                                                "level (+0x1F-+0x23 from where that squad stood): it may never appear "
                                                "here. trigger 3 (fix_squad) spawns it at once", o["uid"]))
        for lv in self.p.levels.values():  # an edit's start points against the game level's own
            base = self._base_starts(lv)
            for o in lv.objects["objects"]:
                n = o.get("fields", {}).get("startPoint")
                if self._key(o) == "startpoint" and n in base:
                    problems.append(val.Problem(val.ERROR, "start-clash", f"levels/{lv.name}/objects.json",
                                                f"start point {n} is one the game level already has: arrivals and "
                                                f"the game's own uses of it would clash (free numbers start at "
                                                f"{next(k for k in itertools.count(0) if k not in base)})", o["uid"]))
        shown = [self._problem(p) for p in problems if notes or p.severity != val.INFO]
        return {"errors": sum(p.severity == val.ERROR for p in problems),
                "warnings": sum(p.severity == val.WARNING for p in problems), "problems": shown}

    def build(self, out: str | None = None) -> dict:
        from jfgstudio import build
        lines: list[str] = []
        path = Path(out) if out else self.p.path / "build" / f"{self.p.id}.nrm"
        try:
            result = build.build(self.p, path, log=lines.append)
        except build.BuildError as e:
            raise AgentError("the build failed:\n" + "\n".join(e.problems)) from e
        except (formats.FormatsUnavailable, rom.RomError) as e:
            raise AgentError(str(e)) from e
        return {"pack": str(result.path), "log": lines}

    def play(self, level: str | None = None, start_point: int = 0, character: str = "juno",
             launch: bool = True) -> dict:
        """Builds, installs into the port's mods folder and starts the game in a level. The player plays it."""
        from jfgstudio import build, testrun
        lines: list[str] = []
        try:
            installed = testrun.play(self.p, level, _as_int(start_point, "start_point"), character, launch=launch,
                                     log=lines.append)
        except build.BuildError as e:
            raise AgentError("the build failed:\n" + "\n".join(e.problems)) from e
        except (testrun.TestRunError, formats.FormatsUnavailable, rom.RomError, ValueError) as e:
            raise AgentError(str(e)) from e
        except PermissionError as e:
            raise AgentError(f"{e.filename} is in use: the game is probably running with the pack loaded. Ask the "
                             "player to close it, then try again") from e
        command = next((ln[len(testrun.COMMAND):] for ln in lines if ln.startswith(testrun.COMMAND)), None)
        return {"installed": str(installed), "command": command, "log": lines}


PATTERNS = ("solid", "noise", "stripes", "spots", "heat")


def _paint(w: int, h: int, colour, accent, pattern: str, seed: int) -> bytes:
    """A w x h RGBA texture: `colour` with `accent` worked in by `pattern` (seeded, so the same call draws the same)."""
    import random
    rng = random.Random(seed)
    spots = [(rng.random() * w, rng.random() * h, 1 + rng.random() * max(1, min(w, h)) / 5) for _ in range(5)]
    px = bytearray()
    for y in range(h):
        for x in range(w):
            if pattern == "solid":
                t = 0.0
            elif pattern == "noise":
                t = rng.random() * 0.6
            elif pattern == "stripes":
                t = 1.0 if (y // 2) % 2 else 0.0
            elif pattern == "spots":
                t = 1.0 if any((x - sx) ** 2 + (y - sy) ** 2 < r * r for sx, sy, r in spots) else rng.random() * 0.2
            else:  # heat: waves of the accent through the colour
                t = max(0.0, min(1.0, (math.sin(x * 0.9 + seed) + math.sin(y * 0.7 - seed * 2) + rng.random() * 1.5)
                                 / 3.5))
            px += bytes(round(c + (a - c) * t) for c, a in zip(colour, accent, strict=True)) + bytes((255,))
    return bytes(px)


def design_guide() -> str:
    """Level design for agents (level_design.md): outdoor and indoor levels as the game builds them, with its
    numbers, room-graph templates and the process."""
    return resources.files("jfgstudio").joinpath("level_design.md").read_text(encoding="utf-8")


def guide() -> str:
    """The world-building guide for agents (agent_guide.md)."""
    return resources.files("jfgstudio").joinpath("agent_guide.md").read_text(encoding="utf-8")


def dumps(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)
