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
MOD_REGIONS = 9  # the port numbers mods' regions 31-39 (the save keeps 40)
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
    out += _check_galaxies(project)
    out += _check_scripts(project)
    out += _check_arrivals(project, schemas)
    out += _check_keys(project, schemas)
    out += _check_reachable(project, schemas)
    out += _check_fog(project, schemas)
    out += _check_flags(project, schemas)
    return out


# Game flags (decomp MSR mod #6, "Game flags a mod pack can use"): every flag is saved progress of the save file.
PACK_FLAGS = range(103, 128)  # unused by the game: packs hand them out from 103
PACK_FLAGS_MORE = range(301, 352)  # the second choice, unless the pack adds ammo capacity upgrades (they use 0x80 + n)
FLAG_BYTES = 352  # flags from here on are the regions' tribal rescued / killed bits, cleared at region changes
HEALTH_UPGRADE, UPGRADE_FLAG = 0xE9, 0x2F  # a health upgrade's number n is saved as game flag 0x2F + n
GLOBAL_FLAGS = {*range(0, 20), *range(66, 71), *range(73, 77), *range(82, 88), 93, 95}  # copied to the shared file


def _flag_problem(flag: int) -> tuple[str, str] | None:
    if flag in PACK_FLAGS or flag == 0:
        return None
    if flag in PACK_FLAGS_MORE:
        return INFO, f"game flag {flag} is free unless the pack adds ammo capacity upgrades (they use 0x80 + n)"
    if flag < 0 or flag >= FLAG_BYTES:
        return ERROR, (f"game flag {flag} is not a pack's to use: flags from {FLAG_BYTES} on are the regions' tribal "
                       "rescued / killed bits (use 103-127)")
    if flag in GLOBAL_FLAGS:
        return ERROR, (f"game flag {flag} is copied to the file all save files share: packs must not set it "
                       "(use 103-127)")
    if flag == 80:
        return WARNING, "game flag 80 is free but meant for a cell switch's message: prefer 103-127"
    return ERROR, f"game flag {flag} is the game's own (saved progress): use 103-127"


def _check_flags(project: Project, schemas) -> list[Problem]:
    """Switches' game flags (target) and the flags triggers set: in the packs' range, each switch its own; and every
    switch's door id names a door of its level."""
    out, seen = [], {}
    for level in project.levels.values():
        of = f"levels/{level.name}/objects.json"
        door_ids = {o.get("fields", {}).get("doorId") for o in level.objects["objects"] if _key(o, schemas) == "door"}
        for o in level.objects["objects"]:
            if _key(o, schemas) == "health" and obj.stock_id(o) == HEALTH_UPGRADE:
                n = o.get("fields", {}).get("upgrade", 0)
                problem = _flag_problem(UPGRADE_FLAG + n)
                if problem:
                    out.append(Problem(problem[0], "game-flag", of, f"the health upgrade's number {n} is saved as "
                                       f"{problem[1]} (game flag 0x2F + n: give it {PACK_FLAGS[0] - UPGRADE_FLAG}-"
                                       f"{PACK_FLAGS[-1] - UPGRADE_FLAG})", o["uid"]))
                elif UPGRADE_FLAG + n in seen:
                    out.append(Problem(WARNING, "game-flag", of, f"the health upgrade's game flag {UPGRADE_FLAG + n} is "
                                       f"also {seen[UPGRADE_FLAG + n]}'s: taking one counts as taking both", o["uid"]))
                seen.setdefault(UPGRADE_FLAG + n, o["uid"])
                continue
            if _key(o, schemas) != "target":
                continue
            f = o.get("fields", {})
            flag, door = f.get("gameFlag", 0), f.get("doorId", 0)
            problem = _flag_problem(flag)
            if problem:
                out.append(Problem(problem[0], "game-flag", of, problem[1], o["uid"]))
            if flag:
                if flag in seen:
                    out.append(Problem(WARNING, "game-flag", of, f"game flag {flag} is also {seen[flag]}'s: shooting "
                                                                 "one counts as shooting both on a reload", o["uid"]))
                seen.setdefault(flag, o["uid"])
            if not door or door not in door_ids:
                out.append(Problem(ERROR, "switch-door", of, f"the switch opens door id {door}, and no door of this "
                                                             "level has it", o["uid"]))
    from jfgstudio import scripts
    for t in scripts.load_triggers(project):
        for a in t.get("then", []):
            if a.get("do") == "flag" and isinstance(a.get("flag"), int):
                problem = _flag_problem(a["flag"])
                if problem:
                    out.append(Problem(problem[0], "game-flag", "triggers.json",
                                       f"trigger {t.get('name', '')!r}: {problem[1]}"))
    return out


FOG_NEAR_MIN = 900  # none of the game's own levels starts its fog closer (checked 2026-10-25: 0 of 412)


def _check_fog(project: Project, schemas) -> list[Problem]:
    """Fog that starts closer than any of the game's levels: the view turns to fog (a trial level's zone at 150 went
    white)."""
    out = []
    for level in project.levels.values():
        h = level.level.get("header", {})
        if h.get("fogFar") and h.get("fogNear", FOG_NEAR_MIN) < FOG_NEAR_MIN:
            out.append(Problem(WARNING, "fog", f"levels/{level.name}/level.json",
                               f"fogNear {h['fogNear']} starts the fog closer than any of the game's levels "
                               f"(all {FOG_NEAR_MIN} or more; a light haze is about 985-990): the view will be "
                               "mostly fog"))
        for o in level.objects["objects"]:
            f = o.get("fields", {})
            if _key(o, schemas) == "fogzone" and f.get("near", FOG_NEAR_MIN) < FOG_NEAR_MIN:
                out.append(Problem(WARNING, "fog", f"levels/{level.name}/objects.json",
                                   f"the fog zone's near {f['near']} is closer than any of the game's level fog "
                                   f"({FOG_NEAR_MIN} or more): inside it the view turns to fog", o["uid"]))
    return out


KEY_IDS = range(0x258, 0x262)  # the coloured keys: yellow 0x258 ... blue 0x261; a door needs id - 0x258 + 2
ARRIVAL_MARGIN = 10  # a start point this close to an exit's radius: the player may be sent straight on


def _check_arrivals(project: Project, schemas) -> list[Problem]:
    """A start point inside an exit's radius in its level: arriving there takes the exit again at once."""
    out = []
    for level in project.levels.values():
        objs = level.objects["objects"]
        exits = [o for o in objs if _key(o, schemas) == "exit" and o.get("fields", {}).get("radius", 0) >= 5]
        for e in exits:
            if e.get("target") == f"pack:level/{level.name}":
                out.append(Problem(WARNING, "exit-self", f"levels/{level.name}/objects.json",
                                   "the exit leads back into its own level: taking it reloads the level (its enemies "
                                   "and pickups come back), a hidden teleport to the player (play-tested). Join a "
                                   "level's areas by passages; exits leave for another level", e["uid"]))
        for s in (o for o in objs if _key(o, schemas) == "startpoint"):
            for e in exits:
                d = ((s["pos"][0] - e["pos"][0]) ** 2 + (s["pos"][2] - e["pos"][2]) ** 2) ** 0.5
                if d <= e["fields"]["radius"] + ARRIVAL_MARGIN:
                    out.append(Problem(WARNING, "arrival", f"levels/{level.name}/objects.json",
                                       f"start point {s.get('fields', {}).get('startPoint', 0)} is {d:.0f} from exit "
                                       f"{e['uid']} (radius {e['fields']['radius']}): arriving may take that exit "
                                       "again", s["uid"]))
    return out


def _check_keys(project: Project, schemas) -> list[Problem]:
    """A door that needs a coloured key no level of the pack holds."""
    held = {obj.stock_id(o) - KEY_IDS.start + 2 for lv in project.levels.values() for o in lv.objects["objects"]
            if obj.stock_id(o) in KEY_IDS}
    out = []
    for level in project.levels.values():
        for o in level.objects["objects"]:
            lock = o.get("fields", {}).get("keyLock", 0)
            if _key(o, schemas) == "door" and lock >= 2 and lock not in held:
                name = schemas["door"].field("keyLock").enum.get(lock, f"item {lock - 2}")
                out.append(Problem(WARNING, "key", f"levels/{level.name}/objects.json",
                                   f"the door needs the {name} and no level of the pack holds one (unless the player "
                                   "brings it from the game)", o["uid"]))
    return out


def _check_reachable(project: Project, schemas) -> list[Problem]:
    """Planets of the pack the player can't get to: not shown on any map and no exit leads into them."""
    planets = {n: p for n, p in project.planets.items() if "replaces" not in p}
    if not planets:
        return []
    planet_of = {lv.name: lv.level["planet"] for lv in project.levels.values() if lv.level.get("planet")}
    for n, p in planets.items():
        for ref in p.get("entryLevel", {}).values():
            if ref.startswith("pack:level/"):
                planet_of.setdefault(ref.split("/", 1)[1], n)
    exits = [(lv.name, refs.parse(o["target"]).name) for lv in project.levels.values()
             for o in lv.objects["objects"] if o.get("target", "").startswith("pack:level/")]
    reached: set[str] = set()
    while True:
        before = len(reached)
        for n, p in planets.items():
            unlock = p.get("unlock") or ("stock" if not p.get("galaxy") else "start")
            other = unlock.get("entered") or unlock.get("completed") if isinstance(unlock, dict) else None
            shown = unlock in ("start", "stock", "script") or (isinstance(unlock, dict) and "newGame" in unlock) or (
                other is not None and other != n and other in reached)
            # an exit from a game level (an edit of one, or a level on no planet) or a reached planet's level
            portal = any(planet_of.get(dst) == n and planet_of.get(src) != n
                         and (planet_of.get(src) is None or planet_of.get(src) in reached) for src, dst in exits)
            if shown or portal:
                reached.add(n)
        if len(reached) == before:
            break
    return [Problem(WARNING, "reachable", f"planets/{n}.json",
                    "the player can't get here: the planet only shows once it is entered or another planet is, and no "
                    "exit from the game's levels leads in. Give it unlock \"start\" (shown from the beginning; L / R "
                    "on the planet map to its galaxy), or an exit into it from an edit of a game level")
            for n in planets if n not in reached]


def _ref(text, category, file, uid, out) -> refs.Ref | None:
    try:
        return refs.parse(text, category)
    except refs.RefError as e:
        out.append(Problem(ERROR, "reference", file, str(e), uid))
        return None


def _check_scripts(project: Project) -> list[Problem]:
    from jfgstudio import scripts as scr
    return [Problem(ERROR, "triggers", "triggers.json", p) for p in scr.check(project)]


def _check_level(project: Project, level, schemas) -> list[Problem]:
    out: list[Problem] = []
    lf = f"levels/{level.name}/level.json"
    of = f"levels/{level.name}/objects.json"
    header = level.level["header"]
    if level.level.get("base"):
        _ref(level.level["base"], "level", lf, None, out)
        if level.level.get("planet"):
            out.append(Problem(ERROR, "planet", lf, "a stock level's edit keeps the stock level's region: no planet"))
        return out + _check_objects(project, level, schemas, of, stock_base=True)

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
    planet = level.level.get("planet")
    if planet is not None:
        if planet not in project.planets:
            out.append(Problem(ERROR, "planet", lf, f"planet {planet!r}: the project has no planets/{planet}.json"))
        elif sum(lv.level.get("planet") == planet for lv in project.levels.values()) > REGION_BLOCKS:
            out.append(Problem(ERROR, "planet", lf, f"planet {planet} has more than {REGION_BLOCKS} levels (the game's "
                                                    "saved-flag blocks of a region)"))
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

    return out + _check_objects(project, level, schemas, of)


def _check_objects(project: Project, level, schemas, of: str, stock_base: bool = False) -> list[Problem]:
    """The level's objects. A stock-level edit (`stock_base`) adds to the stock list: its squads, regions and start
    points are checked against what the stock level has by the build, not here."""
    out: list[Problem] = []
    items = level.objects["objects"]
    for uid, n in Counter(o["uid"] for o in items).items():
        if n > 1:
            out.append(Problem(ERROR, "uid", of, f"uid {uid} is used {n} times", uid))

    total = obj.LIST_HEADER_SIZE
    squadrons, members, doors, region_corners, starts, switches = [], [], [], defaultdict(set), [], []
    region_points, holes = defaultdict(list), set()
    patrol_nodes, patrol_links, counts = [], [], Counter()
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
        elif key == "target":
            switches.append(o)
        elif key == "regionpoint" and "region" in fields:
            region_corners[fields["region"]].add(fields.get("corner"))
            region_points[fields["region"]].append((fields.get("corner", 0), o["pos"][0], o["pos"][2]))
        elif key == "regioninfo" and fields.get("link") not in (None, 0, fields.get("region")):
            holes.add(fields.get("region"))  # a hole of another region: wound clockwise on purpose
        elif key == "startpoint":
            starts.append(o)
        elif key == "exit":
            out += _check_exit(project, o, of, schemas)
        elif key == "patrolpoint":
            patrol_nodes.append((fields.get("node"), uid))
            if fields.get("startAtLoad"):
                out.append(Problem(WARNING, "patrol-start", of, "the patrol point has a start value: the squadron "
                                                                "nearest it rides a lift from level load, stops at its "
                                                                "first node and never fights. Set startAtLoad to 0",
                                   uid))
        elif key == "patrolpath":
            patrol_links.append((fields.get("nodeA"), fields.get("nodeB"), uid))
        elif key == "fogzone" and fields.get("fadeTime", 1) == 0:
            out.append(Problem(ERROR, "fog-zone", of, "fadeTime 0: the game divides by it", uid))
        counts[key] += 1
        if any(not -32768 <= v <= 32767 for v in o["pos"]):
            out.append(Problem(ERROR, "position", of, "position does not fit s16 (-32768..32767)", uid))

    if total > obj.LIST_BUFFER_SIZE:
        out.append(Problem(ERROR, "list-size", of, f"the object list is 0x{total:X} bytes; the game's buffer is "
                                                   f"0x{obj.LIST_BUFFER_SIZE:X} (header included)"))
    out += _check_winding({r: p for r, p in region_points.items() if r not in holes}, of)  # an edit's own regions too
    if stock_base:
        return out
    out += _check_squads(squadrons, members, doors, region_corners, of, switches)
    out += _check_patrols(patrol_nodes, patrol_links, of)
    for key, limit, what in (("camerazone", 24, "camera zones (the rest are ignored)"),
                             ("texscroll", 16, "texture scrolls (the rest are dropped)"),
                             ("audio", 40, "sound emitters (the audio map holds 40 points)"),
                             ("glow", 50, "glows (the game's camera lights)")):
        if counts[key] > limit:
            out.append(Problem(WARNING, "limit", of, f"{counts[key]} {what}; the game takes {limit}"))
    lights = counts["light"]
    slots = level.level["header"].get("lights", 24)
    if lights > slots:
        out.append(Problem(WARNING, "limit", of, f"{lights} lights but {slots} light slots (Level window: Light "
                                                 "slots); lights past the last slot are dropped"))
    if not any(s.get("fields", {}).get("player", 0) == 0 and s.get("fields", {}).get("startPoint", 0) == 0
               for s in starts):
        out.append(Problem(WARNING, "start-point", of, "no start point 0 for player 0: the planet map and most exits "
                                                       "enter a level at start point 0"))
    return out


def _check_patrols(nodes, links, of) -> list[Problem]:
    """Patrol points are numbered 1..N with no gaps (the game puts them in an array by number); links name existing
    nodes, at most 6 from each (MSR "Squad patrol network")."""
    out = []
    numbers = Counter(n for n, _ in nodes)
    for n, count in numbers.items():
        if count > 1:
            out.append(Problem(ERROR, "patrol", of, f"patrol node number {n} is used {count} times"))
    if nodes and sorted(numbers) != list(range(1, len(numbers) + 1)):
        out.append(Problem(ERROR, "patrol", of, f"patrol node numbers must run 1..{len(nodes)} with no gaps "
                                                f"(found {sorted(numbers)})"))
    outgoing = Counter()
    for a, b, uid in links:
        for n in (a, b):
            if n not in numbers:
                out.append(Problem(ERROR, "patrol", of, f"the link names patrol node {n}, which isn't there", uid))
        outgoing[a] += 1
        outgoing[b] += 1
    for n, k in outgoing.items():
        if k > 6:
            out.append(Problem(WARNING, "patrol", of, f"patrol node {n} has {k} links; the game keeps 6"))
    return out


def _check_winding(region_points, of) -> list[Problem]:
    """A region's corners must go counter-clockwise in (x, z): the game finds no point inside a clockwise one, so its
    squad never moves or turns (decomp MSR mod #6; the game's clockwise regions are holes linked to another)."""
    from jfgstudio.prefabs import winding
    out = []
    for region, pts in region_points.items():
        pts = [(x, z) for _, x, z in sorted(pts, key=lambda p: p[0] if p[0] is not None else -1)]
        if len(pts) >= 3 and winding(pts) < 0:
            out.append(Problem(ERROR, "region-winding", of, f"region {region}: its corners go clockwise in (x, z): "
                                                            "the game finds nothing inside it, so its squad stands "
                                                            "still and never turns. fix_squad (or reversing the "
                                                            "corner numbers) mends it"))
    return out


def _check_squads(squadrons, members, doors, region_corners, of, switches=()) -> list[Problem]:
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
        if door and not any(s.get("fields", {}).get("doorId") == door for s in [*squadrons, *switches]):
            out.append(Problem(INFO, "door-link", of, f"door id {door} is not locked by any squadron or switch in "
                                                      "this list",
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
    elif "disc" not in p or (not p.get("routes") and not p.get("galaxy")):
        out.append(Problem(ERROR, "planet", pf, "a planet needs \"replaces\" (a stock map point to take over) or a "
                                                "disc and a route (a planet on a project galaxy may have none)"))
    disc = p.get("disc", {"index": -1})
    if "replaces" not in p:  # a map point of its own (the planet registry): the disc index is only its look
        for route in p.get("routes", []):
            r = _ref(route["from"], None, pf, None, out)
            on_main = not p.get("galaxy")
            if r and r.is_stock and r.category == "mappoint" and not on_main:
                out.append(Problem(ERROR, "route", pf, f"{route['from']}: a stock map point is on the game's galaxy, "
                                                       "this planet is not"))
            elif r and not (r.is_stock and r.category == "mappoint") and not (r.is_pack and r.category == "world"):
                out.append(Problem(ERROR, "route", pf, f"{route['from']}: routes start at a stock map point or a "
                                                       "planet (pack:world/<name>)"))
            elif r and r.category == "world" and r.name not in project.planets:
                out.append(Problem(ERROR, "route", pf, f"{route['from']}: the project has no planet {r.name}"))
            if route.get("corners") and len(route["corners"]) < 2:
                out.append(Problem(ERROR, "route", pf, "a route line needs at least 2 corners (or none: the build "
                                                       "draws it)"))
    elif disc["index"] in (8, 9, 10) and disc.get("orbitParent", 0) != -1:
        out.append(Problem(ERROR, "moon-disc", pf, f"disc {disc['index']} is a moon in the stock map: set "
                                                   "orbitParent -1 to make it a planet, or it circles its parent and "
                                                   "can't be selected"))
    for hero, ref in p["entryLevel"].items():
        r = _ref(ref, "level", pf, None, out)
        if r and r.is_pack and r.name not in project.levels:
            out.append(Problem(ERROR, "entry-level", pf, f"{hero}: {ref} is not a level of this project"))
    for route in p.get("routes", []):
        r = _ref(route["from"], None, pf, None, out)
        if r and "replaces" in p and r.category not in ("mappoint", "world"):
            out.append(Problem(ERROR, "route", pf, f"{route['from']}: routes start at a map point or a planet"))
        if route["back"] != OPPOSITE[route["dir"]]:
            out.append(Problem(WARNING, "route", pf, f"route from {route['from']}: back direction {route['back']} "
                                                     f"is not the opposite of {route['dir']}"))
    if "fixedWorld" not in p and "replaces" in p:
        out.append(Problem(INFO, "world", pf, "no fixedWorld: a planet that takes over a map point uses world 15"))
    return out


def _check_galaxies(project: Project) -> list[Problem]:
    """A galaxy has a home among its planets, open from the start; its planets are reachable from home; unlock rules
    name planets of the project."""
    from jfgstudio import worldmap
    out: list[Problem] = []
    for name, g in project.galaxies.items():
        gf = f"galaxies/{name}.json"
        members = sorted(n for n, p in project.planets.items() if worldmap.galaxy_of(p) == name)
        if not members:
            out.append(Problem(WARNING, "galaxy", gf, "no planet is on this galaxy yet"))
            continue
        home = g.get("home")
        if not home or home not in members:
            out.append(Problem(ERROR, "galaxy", gf, f"the home planet must be one of: {', '.join(members)}"))
            continue
        if worldmap.unlock_of(project.planets[home]) not in ("start", {"entered": home}):
            out.append(Problem(WARNING, "galaxy", gf, f"home planet {home} is not open from the start: the galaxy "
                                                      "shows only once one of its planets opens"))
        lost = [n for n in members if n not in worldmap.reachable(project, name)]
        if lost:
            out.append(Problem(ERROR, "route", gf, f"{', '.join(lost)}: no route leads there from home planet {home}"))
    for name, p in project.planets.items():
        pf = f"planets/{name}.json"
        galaxy = worldmap.galaxy_of(p)
        if galaxy != worldmap.MAIN and galaxy not in project.galaxies:
            out.append(Problem(ERROR, "galaxy", pf, f"the project has no galaxy {galaxy}"))
        if galaxy != worldmap.MAIN and "replaces" in p:
            out.append(Problem(ERROR, "galaxy", pf, "a planet taking over a stock map point is on the game's galaxy"))
        unlock = worldmap.unlock_of(p)
        if isinstance(unlock, dict):
            other = next(iter(unlock.values()))
            if other not in project.planets:
                out.append(Problem(ERROR, "unlock", pf, f"it opens after planet {other}: no such planet"))
            elif other == name and "completed" in unlock:
                out.append(Problem(ERROR, "unlock", pf, "it opens after completing itself (\"entered\" itself = "
                                                        "discovered: hidden until its level was first entered)"))
        elif unlock == "stock" and galaxy != worldmap.MAIN:
            out.append(Problem(ERROR, "unlock", pf, "\"stock\" (open with the stock point its route leaves) is for the "
                                                    "game's galaxy"))
    return out


def _check_planets_together(project: Project) -> list[Problem]:
    out: list[Problem] = []
    regions = {lv.level["planet"] for lv in project.levels.values() if lv.level.get("planet")}
    if len(regions) > MOD_REGIONS:
        out.append(Problem(WARNING, "planet", "levels", f"{len(regions)} planets have levels: the port has room for "
                                                         f"{MOD_REGIONS} regions of mods' planets, all mods together"))
    used = Counter((r["from"], r["dir"]) for p in project.planets.values() for r in p.get("routes", []))
    for (src, d), n in used.items():
        if n > 1:
            out.append(Problem(ERROR, "route", "planets", f"{n} routes leave {src} to the {d}: each stick direction "
                                                          "from a map point can lead to one place"))
    worlds = Counter(p.get("fixedWorld") for p in project.planets.values() if "fixedWorld" in p)
    for w, n in worlds.items():
        if n > 1:
            out.append(Problem(ERROR, "world", "planets", f"fixedWorld {w} is used by {n} planets"))
    discs = Counter(p["disc"]["index"] for p in project.planets.values() if "disc" in p and "replaces" in p)
    taken = Counter(p["replaces"] for p in project.planets.values() if "replaces" in p)
    for point, n in taken.items():
        if n > 1:
            out.append(Problem(ERROR, "planet", "planets", f"{n} planets replace {point}"))
    for d, n in discs.items():
        if n > 1:
            out.append(Problem(ERROR, "disc", "planets", f"disc {d} is used by {n} planets"))
    return out
