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
from importlib import resources
from pathlib import Path

import jsonschema
import numpy as np

from jfgstudio import edit, formats, importer, placement, prefabs, preview, refs, rom, scripts
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val

SQUADRON_ID = prefabs.SQUADRON_ID
DOOR_LOCKED = 1  # a door's keyLock: locked until its squadrons are dead (or a switch opens it)
LEVEL_KEYS = ("name", "track", "sky", "music", "planet", "ambient", "header")
SURFACES = ("solid", "lava", "hidden", "water", "kill", "nocollide")  # TrackMesh's kinds; the first three are floor
FLOOR_KINDS = ("solid", "lava", "hidden")
LIST_LIMIT = 150  # catalog types listed at once
NEW_GAME_LEVEL = 21  # where a new game starts (Juno); the editor's default way back to the game
SIZED = 12  # describe_type of a shared schema (doors) measures this many of its most used types' models
KEY_IDS = tuple(range(0x258, 0x262))  # the coloured keys: keyLock 2 (yellow) is 0x258 ... 11 (blue) 0x261
PATROL_MODE = 6  # a squadron's kind (low 4 bits) that walks the patrol network (decomp: "Squad patrol network")
LAVA_FLOOR_DEPTH = 100  # lava needs a solid floor at most this far under it, or Juno falls through
DOOR_CLEARANCE = 120  # a door placed by [x, z] stands on the lowest floor with this much room above it
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
        return level

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
        """The game's own levels (from the catalog) as "id: name (world w)"; `search` filters by name, `world` by
        world. Exits and entry levels name them stock:level/<id>."""
        if self.catalog() is None:
            raise AgentError(f"the catalog is not available ({self._catalog_error})")
        out = [f"{lv['id']}: {lv['name']} (world {lv['world']})" for lv in self._catalog.levels()
               if (not search or search.lower() in lv["name"].lower()) and (world is None or lv["world"] == world)]
        return {"levels": out[:LIST_LIMIT * 3], "newGame": NEW_GAME_LEVEL,
                "note": f"stock:level/{NEW_GAME_LEVEL} is where a new game starts (Juno): the usual way back to the "
                        "game, and a good place for a portal into the pack"}

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
                under = floor_of(kinds["solid"], x, z, at["floor"] - 1)
                hidden = floor_of(kinds["hidden"], x, z, at["floor"] - 1)
                under = max((y for y in (under, hidden) if y is not None), default=None)
                at["underLava"] = round(under, 1) if under is not None else None
            out.append(at)
        return out

    def lava_holes(self, level: str) -> list[list[int]]:
        """Points of lava with no solid floor under it (LAVA_FLOOR_DEPTH at most): Juno, whose collision passes
        through lava, falls through there. One point per lava triangle (its middle)."""
        from jfgstudio.gui.viewport import floor_of
        mesh = self._mesh(level)
        lava = mesh.kind_triangles("lava")
        ground = np.concatenate([mesh.kind_triangles("solid"), mesh.kind_triangles("hidden")])
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
        lo, hi = mesh.bounds
        far = float(max(abs(float(v)) for v in (*lo, *hi)))
        counts = {k: len(mesh.kind_triangles(k)) for k in SURFACES}
        out = {"bounds": [[round(float(v)) for v in lo], [round(float(v)) for v in hi]],
               "triangles": sum(counts.values()), "surfaces": {k: n for k, n in counts.items() if n},
               "images": len(mesh.images), "farthest": round(far),
               "limits": {"farthest": 32767, "newImages": 3}}
        if far > 32767:
            out["problem"] = "a vertex is farther than 32767 units from the origin: the game can't hold it"
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
        """The level's objects that should stand on the floor but are buried, floating, sunk or over a pit."""
        lv = self._level(level)
        return placement.check(self._mesh(level), lv.objects["objects"], self.schemas, self._behaviours())

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
        if below is None and schema is not None and schema.key == "door":
            y = self._door_floor(mesh, x, z)
        else:
            y = self._floor(mesh, x, z, below)
        if y is None:
            where = f" at or below y {below:g}" if below is not None else ""
            raise AgentError(f"no floor under ({x:g}, {z:g}){where}: check surface_at, or give on_floor false")
        return [round(x), obj.on_ground(y, schema) + round(lift), round(z)]

    @staticmethod
    def _door_floor(mesh, x: float, z: float) -> float | None:
        """Where a door at (x, z) stands: the lowest floor with at least DOOR_CLEARANCE free above it (up to the next
        surface: a lintel's underside, or the top of the wall when the opening's top isn't modelled). A door is the
        one thing usually placed under something; give [x, y, z] for a door on an upper floor."""
        from jfgstudio.gui.viewport import heights_at
        floors = sorted(set(heights_at(mesh.collision, x, z, facing=1).tolist()))
        surfaces = sorted(set(heights_at(mesh.collision, x, z).tolist()))
        for f in floors:
            above = next((s for s in surfaces if s > f + 1), math.inf)
            if above - f >= DOOR_CLEARANCE:
                return f
        return floors[-1] if floors else None

    def _add(self, level: proj.Level, made: list[dict], label: str) -> list[dict]:
        self._history(level).add(made, label)
        self._save()
        return [self._describe(o) for o in made]

    def place(self, level: str, type, pos, fields: dict | None = None, name: str | None = None,
              on_floor: bool = True, lift: float = 0, example=None, target: str | None = None,
              facing: float | None = None) -> list[dict]:
        """One object. A type that isn't fully decoded starts from a stock entry (from the catalog, or `example`).
        `facing`: degrees, 0 = towards -z (as start points face), for the type's yaw field in its own steps."""
        lv = self._level(level)
        oid, schema, kind = self._type(type)
        checked = self._fields(schema, fields)
        if facing is not None:
            checked.update([self._facing(schema, facing)])
        at = self._pos(lv, pos, schema, on_floor, lift)
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
    def _facing(schema, degrees) -> tuple[str, int]:
        """(yaw field, value) for a facing in degrees, in whatever angle steps the type's yaw field takes."""
        f = schema.role("yaw") if schema else None
        if f is None:
            raise AgentError(f"{schema.key if schema else 'this type'} has no facing (no yaw field)")
        if isinstance(degrees, bool) or not isinstance(degrees, int | float):
            raise AgentError("facing is in degrees")
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
                    name: str | None = None, squadron_example=None, member_example=None) -> list[dict]:
        """A squadron, its members around `centre` and the region they keep to; with `door` ("new" with door_type
        and door_pos, or an existing door's uid) a door it keeps locked until all of them are dead."""
        lv = self._level(level)
        members = _as_int(members, "members")
        if not 1 <= members <= 16:
            raise AgentError("members is 1-16")
        m_oid, m_schema, m_kind = self._type(member_type)
        placer = prefabs.Placer(lv, self.schemas, self._behaviours())
        sq_schema = self.schemas["squadron"]
        sq_ex = self._example(SQUADRON_ID, sq_schema, squadron_example)
        m_ex = self._example(m_oid, None, member_example)
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
        corners = [self._pos(lv, [c[0], c[-1]], self.schemas["regionpoint"], True) for c in region]
        region_id = placer.polygon(corners)
        # Never the catalog's name: that is the player's game data, and uids and names are written into the project.
        label = name or (m_kind.rsplit("/", 1)[1].replace("_", " ") if m_kind else "enemy")
        squadron = placer.single(SQUADRON_ID, self._pos(lv, [cx, cz], sq_schema, True), sq_ex,
                                 name=f"{label} squad",
                                 fields={"squadronId": squadron_id, "doorId": door_id, "region": region_id,
                                         "count": members, "baddyType": m_oid})
        if m_kind:
            squadron["kindFields"] = {"baddyType": m_kind}
        wet = []
        for k in range(members):
            a = 2 * math.pi * k / members
            spot = self._dry_spot(lv.name, cx + spread * math.sin(a), cz + spread * math.cos(a), cx, cz)
            if spot is None:
                spot = (cx + spread * math.sin(a), cz + spread * math.cos(a))
                wet.append(k)
            at = self._pos(lv, list(spot), self.schemas["squadmember"], True)
            m = placer.single(m_oid, at, m_ex, name=label, fields={"squadron": squadron_id})
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
        if wet:
            made.append({"warning": f"{len(wet)} member(s) found no dry floor near their place (lava, water, a pit "
                                    "or no floor there): move them (surface_at, find_flat)"})
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

    def place_patrol(self, level: str, points: list, loop: bool = False, squadron: str | None = None,
                     start_at_load: bool = False, wait: int | None = None) -> list[dict]:
        """A patrol route: nodes on the floor at `points` ([x, z] or [x, y, z]), linked in order both ways (a chain
        is walked there and back; `loop` joins the last to the first). With `squadron` (a squadron's uid) it is put in
        patrol mode; it starts from the node nearest to itself, when activated (or at level load with
        `start_at_load`). `wait`: the wait at each node (stock 15)."""
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
        if sq is not None and start_at_load:
            nearest = min(nodes, key=lambda n: math.dist(n["pos"], sq["pos"]))
            nearest["fields"]["startAtLoad"] = 1
        made = self._add(lv, placer.made, "patrol")
        if sq is not None:
            kind = sq.get("fields", {}).get("kind")
            if kind is None and sq.get("stock") and self.catalog() is not None:
                kind = self._catalog.stock_entry(sq["stock"]["list"], sq["stock"]["entry"])[0x24]
            self.update_object(lv.name, squadron, fields={"kind": ((kind or 0) & 0xF0) | PATROL_MODE})
            made.append(self._describe(sq))
            others = [o for o in lv.objects["objects"] if self._key(o) == "patrolpoint" and o not in nodes]
            mine = min(math.dist(n["pos"], sq["pos"]) for n in nodes)
            if others and min(math.dist(o["pos"], sq["pos"]) for o in others) < mine:
                made.append({"warning": "another patrol's node is nearer the squadron than this route: it starts on "
                                        "the network nearest to itself. Move the squadron next to this route"})
        return made

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

    def update_object(self, level: str, uid: str, fields: dict | None = None, pos=None, on_floor: bool = False,
                      name: str | None = None, target: str | None = None, facing: float | None = None) -> dict:
        """Changes an object's fields (merged), position, name or exit target, as one undo step."""
        lv = self._level(level)
        o = self._object(lv, uid)
        schema = obj.schema_for(o, self.schemas, self._behaviours())
        changes = []
        values = self._fields(schema, fields)
        if facing is not None:
            values.update([self._facing(schema, facing)])
        for k, v in values.items():
            changes.append(edit.Change(uid, ("fields", k), o.get("fields", {}).get(k), v))
        if pos is not None:
            changes.append(edit.Change(uid, ("pos",), list(o["pos"]), self._pos(lv, pos, schema, on_floor)))
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
        lv = self._level(level)
        for u in uids:
            self._object(lv, u)
        self._history(lv).delete(set(uids))
        self._save()
        return {"deleted": list(uids)}

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
                                         + (f"; it would stand at {f['standOn']}" if "standOn" in f else ""),
                                         f["uid"]) for f in findings]
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
        return {"installed": str(installed), "log": lines}


def guide() -> str:
    """The world-building guide for agents (agent_guide.md)."""
    return resources.files("jfgstudio").joinpath("agent_guide.md").read_text(encoding="utf-8")


def dumps(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)
