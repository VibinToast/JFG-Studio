"""Validator v0: the rules the decomp learned from failed ROMs (MSR mod #6, "Pitfalls"), plus the project's own
consistency. Every problem names the file and, where there is one, the object (uid), so the editor can select it.

Rules that need the stock catalog (stock levels' start points, stock map routes) or the geometry (batch and buffer
limits) are not checked yet; `jfgstudio build` runs them once the catalog and jfgformats are in.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from jfgstudio import objects as obj
from jfgstudio import refs
from jfgstudio.project import Project

ERROR, WARNING, INFO = "error", "warning", "info"
CUTSCENE_DATA_COUNT = 406  # asset 0x41 entries; 3 = the empty list
FREE_WORLD = 15
REGION_BLOCKS = 32  # per-level saved-flag blocks
# Header fields a new level must set (jfgformats levels.NEW_LEVEL_TEMPLATE "mustSet"); Studio itself fills track,
# track2 (sky) and the two object lists.
MUST_SET = ("world", "region", "regionIndex", "fogNear", "fogFar", "fog3", "fogR", "fogG", "fogB", "tune")
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}


@dataclass(frozen=True)
class Problem:
    severity: str
    rule: str
    file: str
    message: str
    uid: str | None = None

    def __str__(self) -> str:
        where = f"{self.file}" + (f" [{self.uid}]" if self.uid else "")
        return f"{self.severity}: {where}: {self.message} ({self.rule})"


def validate(project: Project, schemas: dict | None = None) -> list[Problem]:
    schemas = schemas if schemas is not None else obj.load_schemas()
    out: list[Problem] = []
    for level in project.levels.values():
        out += _check_level(project, level, schemas)
    for name, planet in project.planets.items():
        out += _check_planet(project, name, planet)
    out += _check_planets_together(project)
    return out


def _ref(text, category, file, uid, out) -> refs.Ref | None:
    try:
        return refs.parse(text, category)
    except refs.RefError as e:
        out.append(Problem(ERROR, "reference", file, str(e), uid))
        return None


def _check_level(project: Project, level, schemas) -> list[Problem]:
    out: list[Problem] = []
    lf = f"levels/{level.name}/level.json"
    of = f"levels/{level.name}/objects.json"
    header = level.level["header"]

    cut = header.get("cutsceneData")
    if cut is None:
        out.append(Problem(ERROR, "cutscene-data", lf,
                           "header.cutsceneData is missing; use 3 (the empty cutscene list)"))
    elif cut == -1:
        out.append(Problem(ERROR, "cutscene-data", lf, "cutsceneData -1 keeps the previous level's cutscenes loaded "
                                                       "and the level never starts (black screen); use 3"))
    elif not 0 <= cut < CUTSCENE_DATA_COUNT:
        out.append(Problem(ERROR, "cutscene-data", lf, f"cutsceneData {cut} is past the table (0-405); use 3"))

    world = header.get("world")
    if world is None:
        out.append(Problem(ERROR, "world", lf, "header.world is missing"))
    elif world > FREE_WORLD:
        out.append(Problem(ERROR, "world", lf, f"world {world}: worlds past 15 need the port's planet registry"))

    missing = [f for f in MUST_SET if f not in header]
    if missing:
        out.append(Problem(ERROR, "header", lf, f"header needs {', '.join(missing)} (a new level's header has no "
                                                f"stock level to take them from)"))
    for f in ("region", "regionIndex"):
        if header.get(f, 0) >= REGION_BLOCKS:
            out.append(Problem(ERROR, "header", lf, f"{f} {header[f]}: the game has {REGION_BLOCKS} (0-31)"))

    track = _ref(level.level["track"], "track", lf, None, out)
    if track and track.is_pack and not project.geometry_path(track.name).is_file():
        out.append(Problem(ERROR, "track", lf, f"{track}: geometry/{track.name}.glb does not exist"))
    if level.level.get("music"):
        try:
            music = refs.parse(level.level["music"], "music")
        except refs.RefError as e:
            out.append(Problem(ERROR, "music", lf, str(e)))
        else:
            if music.is_pack and not project.music_path(music.name).is_file():
                out.append(Problem(ERROR, "music", lf, f"{music}: music/{music.name}.ogg does not exist"))
            elif music.is_pack and project.music_path(music.name).read_bytes()[:4] != b"OggS":
                out.append(Problem(ERROR, "music", lf, f"music/{music.name}.ogg is not an OGG file"))

    items = level.objects["objects"]
    for uid, n in Counter(o["uid"] for o in items).items():
        if n > 1:
            out.append(Problem(ERROR, "uid", of, f"uid {uid} is used {n} times", uid))

    total = obj.LIST_HEADER_SIZE
    squadrons, members, doors, region_corners, starts = [], [], [], defaultdict(set), []
    for o in items:
        uid = o["uid"]
        t = _ref(o["type"], None, of, uid, out)
        if t and t.category not in ("objtype", "kind"):
            out.append(Problem(ERROR, "type", of, f"{o['type']}: an object's type is an objtype or a kind", uid))
        schema = obj.schema_for(o, schemas)
        if o.get("schema") and schema is None:
            out.append(Problem(ERROR, "schema", of, f"unknown schema {o['schema']!r}", uid))
        size = o.get("size")
        if size is None and "stock" not in o:
            out.append(Problem(ERROR, "size", of, "entry size unknown: give \"size\" or a \"stock\" base entry", uid))
        if size is not None:
            total += size
            if schema and size < schema.min_size:
                out.append(Problem(ERROR, "size", of, f"size 0x{size:X} is smaller than {schema.name}'s code reads "
                                                      f"(0x{schema.min_size:X})", uid))
            if size % 2:
                out.append(Problem(WARNING, "size", of, f"odd size 0x{size:X}: every stock entry is even", uid))
        for name, value in o.get("fields", {}).items():
            f = schema.field(name) if schema else None
            if f is None:
                out.append(Problem(ERROR, "field", of, f"field {name!r} is not in "
                                                       f"{schema.name if schema else 'any schema for this type'}", uid))
            else:
                lo, hi = obj._RANGES[f.type]
                if not lo <= value <= hi:
                    out.append(Problem(ERROR, "field", of, f"{name} = {value} is outside {lo}..{hi}", uid))
        fields = o.get("fields", {})
        key = schema.key if schema else None
        if key == "squadron":
            squadrons.append(o)
        elif key == "squadmember":
            members.append(o)
        elif key == "door":
            doors.append(o)
        elif key == "regionpoint" and "region" in fields:
            region_corners[fields["region"]].add(fields.get("corner"))
        elif key == "startpoint":
            starts.append(o)
        elif key == "exit":
            out += _check_exit(project, o, of, schemas)
        if any(not -32768 <= v <= 32767 for v in o["pos"]):
            out.append(Problem(ERROR, "position", of, "position does not fit s16 (-32768..32767)", uid))

    if total > obj.LIST_BUFFER_SIZE:
        out.append(Problem(ERROR, "list-size", of, f"the object list is 0x{total:X} bytes; the game's buffer is "
                                                   f"0x{obj.LIST_BUFFER_SIZE:X} (header included)"))
    out += _check_squads(squadrons, members, doors, region_corners, of)
    if not any(s.get("fields", {}).get("player", 0) == 0 and s.get("fields", {}).get("startPoint", 0) == 0
               for s in starts):
        out.append(Problem(WARNING, "start-point", of, "no start point 0 for player 0: the planet map and most exits "
                                                       "enter a level at start point 0"))
    return out


def _check_squads(squadrons, members, doors, region_corners, of) -> list[Problem]:
    out = []
    ids = Counter(s.get("fields", {}).get("squadronId") for s in squadrons)
    for sid, n in ids.items():
        if sid is not None and n > 1:
            out.append(Problem(ERROR, "squadron-id", of, f"squadronId {sid} is used by {n} squadrons"))
    for s in squadrons:
        f = s.get("fields", {})
        if "squadronId" not in f:
            out.append(Problem(ERROR, "squadron-id", of, "the squadron has no squadronId", s["uid"]))
        region = f.get("region")
        if region is None and "stock" in s:
            # An unchanged stock squadron: jfgformats' build checks it against the regions of its stock list.
            out.append(Problem(INFO, "squad-region", of, "the squadron keeps its stock region: the build checks the "
                                                         "list has that stock list's regions", s["uid"]))
        elif region is None:
            out.append(Problem(ERROR, "squad-region", of, "the squadron has no region (+0x3F)", s["uid"]))
        elif region > 127:
            out.append(Problem(ERROR, "squad-region", of, f"region {region}: the game reads it signed (0-127)",
                               s["uid"]))
        elif len(region_corners.get(region, ())) < 3:
            out.append(Problem(ERROR, "squad-region", of, f"region {region} has no RegionPoint polygon in this list: "
                                                          "the game crashes when the level starts (region 0 "
                                                          "included). Add a region of at least 3 corners", s["uid"]))
        elif len(region_corners[region]) != 4:
            out.append(Problem(INFO, "squad-region", of, f"region {region} has {len(region_corners[region])} "
                                                         "corners; only 4-corner regions have been tested", s["uid"]))
        door = f.get("doorId", 0)
        if door and not any(d.get("fields", {}).get("doorId") == door for d in doors):
            out.append(Problem(WARNING, "door-link", of, f"the squadron locks door id {door}, but no door has it",
                               s["uid"]))
        if not any(m.get("fields", {}).get("squadron") == f.get("squadronId") for m in members):
            out.append(Problem(INFO, "squad-members", of, "the squadron has no members here (most stock "
                                                          "squadrons have none either)", s["uid"]))
    locked = {s.get("fields", {}).get("doorId") for s in squadrons} - {None, 0}
    for door_id, n in Counter(d.get("fields", {}).get("doorId") for d in doors).items():
        if door_id in locked and n > 1:
            out.append(Problem(ERROR, "door-link", of, f"{n} doors have door id {door_id}: a squadron locks only the "
                                                       "first of them, and its death opens them all; give each its "
                                                       "own id"))
    for m in members:
        if m.get("fields", {}).get("squadron") not in ids:
            out.append(Problem(ERROR, "squad-members", of, "the member names a squadron this list does not have "
                                                           "(members without a squadron are never spawned)", m["uid"]))
    for d in doors:
        door = d.get("fields", {}).get("doorId", 0)
        if door and not any(s.get("fields", {}).get("doorId") == door for s in squadrons):
            out.append(Problem(INFO, "door-link", of, f"door id {door} is not locked by any squadron in this list",
                               d["uid"]))
    for region, corners in region_corners.items():
        if corners != set(range(len(corners))):
            out.append(Problem(ERROR, "region-corners", of, f"region {region}: corner indices must be 0..n-1 "
                                                            f"(have {sorted(c for c in corners if c is not None)})"))
    return out


def _check_exit(project: Project, o: dict, of: str, schemas) -> list[Problem]:
    out: list[Problem] = []
    target = o.get("target")
    if not target:
        if not {"levelLow", "levelHigh"} <= set(o.get("fields", {})):
            out.append(Problem(ERROR, "exit-target", of, "the exit has no target level", o["uid"]))
        return out
    t = _ref(target, "level", of, o["uid"], out)
    if not t:
        return out
    point = o.get("fields", {}).get("startPoint", 0)
    if t.is_pack:
        level = project.levels.get(t.name)
        if level is None:
            out.append(Problem(ERROR, "exit-target", of, f"{target}: no such level in this project", o["uid"]))
        elif not any(x.get("fields", {}).get("startPoint", 0) == point and x.get("fields", {}).get("player", 0) == 0
                     for x in level.objects["objects"] if _key(x, schemas) == "startpoint"):
            out.append(Problem(ERROR, "exit-target", of, f"{target} has no start point {point} for player 0",
                               o["uid"]))
    elif not t.is_stock:
        out.append(Problem(ERROR, "exit-target", of, f"{target}: exits lead to stock or pack levels", o["uid"]))
    return out


def _key(o: dict, schemas) -> str | None:
    schema = obj.schema_for(o, schemas)
    return schema.key if schema else None


def _check_planet(project: Project, name: str, p: dict) -> list[Problem]:
    out: list[Problem] = []
    pf = f"planets/{name}.json"
    if "replaces" in p:
        _ref(p["replaces"], "mappoint", pf, None, out)
    elif "disc" not in p or not p.get("routes"):
        out.append(Problem(ERROR, "planet", pf, "a planet needs \"replaces\" (a stock map point to take over) or a "
                                                "disc and at least one route"))
    disc = p.get("disc", {"index": -1})
    if disc["index"] in (8, 9, 10) and disc.get("orbitParent", 0) != -1:
        out.append(Problem(ERROR, "moon-disc", pf, f"disc {disc['index']} is a moon in the stock map: set "
                                                   "orbitParent -1 to make it a planet, or it circles its parent and "
                                                   "can't be selected"))
    for hero, ref in p["entryLevel"].items():
        r = _ref(ref, "level", pf, None, out)
        if r and r.is_pack and r.name not in project.levels:
            out.append(Problem(ERROR, "entry-level", pf, f"{hero}: {ref} is not a level of this project"))
    for route in p.get("routes", []):
        r = _ref(route["from"], None, pf, None, out)
        if r and r.category not in ("mappoint", "world"):
            out.append(Problem(ERROR, "route", pf, f"{route['from']}: routes start at a map point or a planet"))
        if route["back"] != OPPOSITE[route["dir"]]:
            out.append(Problem(WARNING, "route", pf, f"route from {route['from']}: back direction {route['back']} "
                                                     f"is not the opposite of {route['dir']}"))
    if "fixedWorld" not in p:
        out.append(Problem(INFO, "world", pf, "no fixedWorld: needs the port's planet registry (phase 2); phase 0 "
                                              "packs use world 15"))
    return out


def _check_planets_together(project: Project) -> list[Problem]:
    out: list[Problem] = []
    used = Counter((r["from"], r["dir"]) for p in project.planets.values() for r in p.get("routes", []))
    for (src, d), n in used.items():
        if n > 1:
            out.append(Problem(ERROR, "route", "planets", f"{n} routes leave {src} to the {d}: each stick direction "
                                                          "from a map point can lead to one place"))
    worlds = Counter(p.get("fixedWorld") for p in project.planets.values() if "fixedWorld" in p)
    for w, n in worlds.items():
        if n > 1:
            out.append(Problem(ERROR, "world", "planets", f"fixedWorld {w} is used by {n} planets"))
    discs = Counter(p["disc"]["index"] for p in project.planets.values() if "disc" in p)
    taken = Counter(p["replaces"] for p in project.planets.values() if "replaces" in p)
    for point, n in taken.items():
        if n > 1:
            out.append(Problem(ERROR, "planet", "planets", f"{n} planets replace {point}"))
    for d, n in discs.items():
        if n > 1:
            out.append(Problem(ERROR, "disc", "planets", f"disc {d} is used by {n} planets"))
    return out
