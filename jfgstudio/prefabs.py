"""What the Prefabs window places (task S16): one object of a stock type, or a group of objects that work together
(a squad with its region and the door it locks, an exit with the start point that leads back).

A new object either starts from a stock entry (`"stock": {list, entry}`: the build copies its bytes, so fields that
are not decoded yet keep the game's values) or, for fully decoded types, from the schema's defaults alone.

The project's own prefabs (prefabs/<name>.json: capture, Placer.saved) are objects saved together and placed again
anywhere, with fresh uids and fresh numbers for what ties them together (NUMBERS).
"""

import copy
import itertools
import math
import re

from jfgstudio import objects as obj
from jfgstudio.project import Level

EXIT_ID, START_ID, REGIONPOINT_ID, SQUADRON_ID = 0x0B, 0x0D, 0x05, 0x4F
PATROL_POINT_ID, PATROL_PATH_ID = 0x61, 0x60
REGIONINFO_ID = 0x69
# The rescuable Tribals (squadsIsTribal, decomp MSR mod #6 "Rescuable Tribals in pack levels"): a squad of one of
# these types is a Tribal; its number in the room is its squadron id & 15.
TRIBAL_TYPES = (0x11C, 0x11D, 0x11E, 0x11F, 0x120, 0x66, 0x70, 0x90, 0x97, 0x157, 0xA5)
HOLE = 1  # RegionInfo flags: a hole of the area it links to (decomp MSR mod #6)
COVER_RISE = 40  # an up-facing surface this far over a squad's ground: an obstacle, made a hole of its region
COVER_STEP = 20  # the grid obstacles() looks on
COVER_MAX = 12  # holes Placer.cover makes at most per region
COVER_MIN = 40  # an obstacle narrower than this both ways is too small to hide behind: no hole (a trial gave 20s)
COVER_LINK = 10  # cells this far over the region's ground join obstacle pieces (a lumpy rock's dip); plain ground
#                  between them keeps boulders and crates apart (five boulders became one hole, play-tested)
COVER_THIN = 60  # an obstacle thinner than this one way (a net rack 180 x 20) is no cover either: members got stuck on
#                  thin racks made holes (play-tested)
# The numbers that tie a level's objects together, by their fields' "link" (schemas): the field that gives one out,
# the lowest to give, and the value meaning none. A saved prefab's numbers that it gives out itself are renumbered
# when it is placed; those it only names are kept (a squad saved without its door still locks that door's id).
NUMBERS = {"squadronId": ("squadron", "squadronId", 1, None), "doorId": ("door", "doorId", 1, 0),
           "region": ("regionpoint", "region", 1, None), "patrolNode": ("patrolpoint", "node", 1, None),
           "cutscene": ("cutcamera", "id", 1, 0)}
START_NUMBER = ("startpoint", "startPoint")  # given out too; an exit's startPoint names one in the level it leads to
SAVED_DROP = 60  # a saved object's floor is looked for this far over where it would stand (as Drop to floor does)


def winding(points) -> float:
    """Twice the signed area of a polygon of (x, z) corners in order: positive is counter-clockwise, the way the game
    wants a region (IsPointInRegion finds nothing inside a clockwise one; decomp MSR mod #6)."""
    return sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(points, points[1:] + points[:1], strict=True))


def inside(poly, x: float, z: float) -> bool:
    """(x, z) inside the polygon of (x, z) corners (even-odd rule)."""
    hit = False
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1], strict=True):
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            hit = not hit
    return hit


def convex(poly) -> bool:
    """Every corner of the polygon of (x, z) corners turns the same way (no inward corner)."""
    turns = [(b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
             for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2], strict=True)]
    return all(t >= 0 for t in turns) or all(t <= 0 for t in turns)


def light_door(light: dict, doors: list[dict]) -> tuple[dict | None, float, float]:
    """The door a door lock light shows (decomp MSR "Door switch"): it has no door id, and on its first frame takes
    the closest door (behaviour 7) by straight-line distance. (door, its distance, the next door's distance or inf)."""
    found = sorted((math.dist(light["pos"], d["pos"]), k) for k, d in enumerate(doors))
    if not found:
        return None, math.inf, math.inf
    return doors[found[0][1]], found[0][0], found[1][0] if len(found) > 1 else math.inf


def door_lockers(door_fields: dict, objects: list[dict], key, fields) -> list[tuple[str, dict | int]]:
    """What keeps a door locked: ("squad", squadron) and ("switch", target) with its door id, and ("key", item) for a
    key lock. `key(o)` is an object's schema key, `fields(o)` its field values."""
    out: list[tuple[str, dict | int]] = []
    door_id = door_fields.get("doorId", 0)
    if door_id:
        for o in objects:
            k = key(o)
            if k in ("squadron", "target") and fields(o).get("doorId") == door_id:
                out.append(("squad" if k == "squadron" else "switch", o))
    lock = door_fields.get("keyLock", 0)
    if lock >= 2:
        out.append(("key", lock - 2))
    return out


LIGHT_TIE = 1.15  # a second door less than this much farther than the nearest: the light may take either


def hole_regions(objects, key) -> dict[int, int]:
    """Hole region -> the area it is a hole of, from the RegionInfos with flags HOLE among `objects` (`key`: an
    object's schema key)."""
    return {o["fields"]["region"]: o["fields"].get("link") for o in objects if key(o) == "regioninfo"
            and o.get("fields", {}).get("flags", 0) & HOLE and "region" in o["fields"]}


def obstacles(mesh, poly, ground: float) -> tuple[list[tuple[float, float, float, float]], int, int]:
    """The obstacles inside a region of (x, z) corners (blocks, crates, pillars: up-facing solid COVER_RISE or more
    over `ground`), each as the box (x0, z0, x1, z1) around it, how many touch the region's edge (left out: cut the
    outline round them instead), and how many pieces were joined into others. Pieces join only through raised
    ground between them (over COVER_LINK: one lumpy rock), or where their boxes overlap."""
    from jfgstudio.gui.viewport import heights_at
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    nx, nz = int((max(xs) - min(xs)) // COVER_STEP) + 1, int((max(zs) - min(zs)) // COVER_STEP) + 1

    def at(i, j):
        return min(xs) + (i + 0.5) * COVER_STEP, min(zs) + (j + 0.5) * COVER_STEP

    cells = {(i, j) for i in range(nx) for j in range(nz) if inside(poly, *at(i, j))}
    tops = {c: heights_at(mesh.collision, *at(*c), facing=1) for c in cells}
    blocked = {c for c in cells if (tops[c] > ground + COVER_RISE).any()}
    raised = {c for c in cells if (tops[c] > ground + COVER_LINK).any()}
    seen, boxes, edge, joined = set(), [], 0, 0
    for c in sorted(blocked):
        if c in seen:
            continue
        group, todo, touches = [], [c], False
        seen.add(c)
        while todo:
            i, j = todo.pop()
            group.append((i, j))
            for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if n not in cells:
                    touches = True
                elif n in raised and n not in seen:
                    seen.add(n)
                    todo.append(n)
        if touches:
            edge += 1
            continue
        group = [g for g in group if g in blocked]
        x0, z0 = at(min(g[0] for g in group), min(g[1] for g in group))
        x1, z1 = at(max(g[0] for g in group), max(g[1] for g in group))
        boxes.append((x0 - COVER_STEP / 2, z0 - COVER_STEP / 2, x1 + COVER_STEP / 2, z1 + COVER_STEP / 2))
    merged = merge_boxes(boxes)
    joined += len(boxes) - len(merged)
    return merged, edge, joined


def region_ground(mesh, poly) -> float | None:
    """The ground of a region of (x, z) corners: the median, over the region, of the lowest up-facing floor at each
    point (a squadron standing on a boulder gave its region a ground 120 up, and no holes, play-tested)."""
    from jfgstudio.gui.viewport import heights_at
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    step = max(COVER_STEP, (max(xs) - min(xs) + max(zs) - min(zs)) / 60)
    lows = []
    x = min(xs) + step / 2
    while x < max(xs):
        z = min(zs) + step / 2
        while z < max(zs):
            if inside(poly, x, z):
                h = heights_at(mesh.collision, x, z, facing=1)
                if len(h):
                    lows.append(float(h.min()))
            z += step
        x += step
    if not lows:
        return None
    lows.sort()
    return lows[len(lows) // 2]


def _overlap(a, b, gap: float = 0.0) -> bool:
    return a[0] <= b[2] + gap and b[0] <= a[2] + gap and a[1] <= b[3] + gap and b[1] <= a[3] + gap


def merge_boxes(boxes, gap: float = 0.0) -> list[tuple[float, float, float, float]]:
    """Boxes (x0, z0, x1, z1) that overlap or lie within `gap` of each other made one: a lumpy rock's top breaks into
    several pieces, and one obstacle should be one hole (a trial got two or three nested holes per rock)."""
    out = [tuple(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if _overlap(out[i], out[j], gap):
                    a, b = out[i], out.pop(j)
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    merged = True
                    break
            if merged:
                break
    return out


def capture(objects: list[dict], name: str, schemas: dict, behaviours: dict | None = None, values=None, mesh=None,
            description: str = "") -> dict:
    """A prefab (prefabs/<name>.json) of objects: the level's own, or the game level's, which become copies of their
    stock entries. Each pos is from the anchor (the middle of their footprint, at the lowest one's height). `values`:
    (object, schema) -> its fields as the build writes them; the numbers tying it to others (NUMBERS, start points)
    are written into its fields from there, so placing it can renumber them even when they come from its stock
    entry. With the level's `mesh`, each object's height over the floor under it is kept as its lift."""
    if not objects:
        raise ValueError("select the objects to save as a prefab")
    xs, ys, zs = ([float(o["pos"][i]) for o in objects] for i in range(3))
    anchor = ((min(xs) + max(xs)) / 2, min(ys), (min(zs) + max(zs)) / 2)
    out = []
    for o in objects:
        c = {k: copy.deepcopy(v) for k, v in o.items() if k not in ("game", "deleted", "group")}
        stem = o.get("name") or "copy" if o["uid"].startswith("stock:") else o["uid"]
        c["uid"] = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "obj"
        s = obj.schema_for(o, schemas, behaviours)
        if s is not None and values is not None:
            have = values(o, s)
            for f in s.fields:
                if (f.link in NUMBERS or (s.key, f.name) == START_NUMBER) and f.name in have:
                    c.setdefault("fields", {}).setdefault(f.name, have[f.name])
        c["pos"] = [round(float(v) - a) for v, a in zip(o["pos"], anchor, strict=True)]
        if mesh is not None:
            floor = mesh.floor_height(float(o["pos"][0]), float(o["pos"][2]), below=float(o["pos"][1]) + SAVED_DROP)
            if floor is not None:
                c["lift"] = round(float(o["pos"][1]) - floor, 1)
        out.append(c)
    doc = {"format": 1, "name": name.strip() or "Prefab", "objects": out}
    if description.strip():
        doc["description"] = description.strip()
    return doc


TRIGGER_AT_ONCE = 3  # squadron +0x29: spawn at level start (a stock squad's 1 waits in its own level's zone)
REGION_HALF = 300  # a new squad's region: a square this far around it


class Placer:
    """Makes objects for one level, with uids and link numbers that are free in it (and in what it made so far)."""

    def __init__(self, level: Level, schemas: dict, behaviours: dict | None = None, catalog=None):
        """catalog: the user's, for a level that edits a game level: the numbers that level's own objects use
        (squadrons, regions, doors, start points ...) are then taken too, so a new squad doesn't share a region or a
        squadron id with one of the game's (two regions under one number: the squad code crashed in IsPointInRegion,
        play-tested)."""
        self.level, self.schemas, self.behaviours = level, schemas, behaviours or {}
        self.catalog = catalog
        self.made: list[dict] = []

    def _all(self) -> list[dict]:
        return self.level.objects["objects"] + self.made

    def key(self, o: dict) -> str | None:
        s = obj.schema_for(o, self.schemas, self.behaviours)
        return s.key if s else None

    def corners(self, region) -> list[dict]:
        """The RegionPoints of a region (in the level and made so far), in corner order."""
        out = [o for o in self._all() if self.key(o) == "regionpoint" and o.get("fields", {}).get("region") == region]
        return sorted(out, key=lambda o: o["fields"].get("corner", 0))

    def holes_of(self, area) -> list[int]:
        """The regions that are holes of region `area` (a RegionInfo with flags HOLE linking them to it)."""
        return [r for r, a in hole_regions(self._all(), self.key).items() if a == area]

    def area_at(self, x: float, z: float) -> int | None:
        """The region (not a hole) that (x, z) is inside, the smallest when they overlap; None when there is none."""
        holes = hole_regions(self._all(), self.key)
        best, best_size = None, None
        for region in {o["fields"].get("region") for o in self._all() if self.key(o) == "regionpoint"} - set(holes):
            poly = [(o["pos"][0], o["pos"][2]) for o in self.corners(region)]
            if len(poly) >= 3 and inside(poly, x, z) and (best_size is None or abs(winding(poly)) < best_size):
                best, best_size = region, abs(winding(poly))
        return best

    def uid(self, stem: str) -> str:
        used = {o["uid"] for o in self._all()}
        stem = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in stem.lower()).rstrip("0123456789_") or "obj"
        return next(f"{stem}{k}" for k in itertools.count(1) if f"{stem}{k}" not in used)

    def free(self, key: str, field: str, start: int = 1) -> int:
        """The lowest number from `start` that no object of schema `key` uses in `field`: the level's, those made so
        far, and (with a catalog, in a level editing a game level) the game level's own, as their entries hold it."""
        used = {o.get("fields", {}).get(field) for o in self._all()
                if (s := obj.schema_for(o, self.schemas, self.behaviours)) and s.key == key}
        used |= self._game_values(key, field)
        return next(n for n in itertools.count(start) if n not in used)

    def _game_values(self, key: str, field: str) -> set:
        """The values of `field` in the game level's own objects of schema `key` (deleted ones too: a patch keeps
        their slot), decoded from their entries, with the project's changes on top."""
        if self.catalog is None or getattr(self.level, "base", None) is None:
            return set()
        from jfgstudio import stock_edit
        out = set()
        lists: dict[int, list] = {}
        for o in stock_edit.materialize(self.level, self.catalog):
            s = obj.schema_for(o, self.schemas, self.behaviours)
            if not s or s.key != key:
                continue
            g = o["game"]
            if g["list"] not in lists:
                try:
                    lists[g["list"]] = self.catalog.objlist(g["list"])
                except (IndexError, ValueError, AttributeError):
                    lists[g["list"]] = []
            entries = lists[g["list"]]
            raw = entries[g["index"]][1] if g["index"] < len(entries) and entries[g["index"]] else None
            values = obj.decode_entry(raw, s).get("fields", {}) if raw else {}
            values.update(o.get("fields", {}))
            out.add(values.get(field))
        return out

    def single(self, oid: int, pos, example: tuple[int, int] | None = None, name: str | None = None,
               fields: dict | None = None) -> dict:
        schema = obj.schema_for_id(oid, self.schemas, self.behaviours)
        o = {"uid": self.uid(name or (schema.key if schema else f"obj{oid:x}")), "type": f"stock:objtype/0x{oid:X}",
             "pos": [round(float(v)) for v in pos]}
        if name:
            o["name"] = name
        if schema and oid not in schema.ids:
            o["schema"] = schema.key  # found by behaviour: the build has no catalog, so name it
        if example is not None:
            o["stock"] = {"list": int(example[0]), "entry": int(example[1])}
        else:
            if schema is None or schema.status != "decoded":
                raise ValueError(f"object type 0x{oid:X} is not fully decoded: start it from a stock entry")
            o["size"] = schema.min_size + (schema.min_size & 1)  # every stock size is even
            o["fields"] = schema.defaults()
        if fields:
            o.setdefault("fields", {}).update(fields)
        self.made.append(o)
        return o

    def region(self, centre, half: float = REGION_HALF, region: int | None = None) -> int:
        """A square navigation region (4 RegionPoints) around a point; returns its id."""
        n = region if region is not None else max(self.free("regionpoint", "region"), self.free("squadron", "region"))
        x, y, z = centre
        for k, (dx, dz) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            self.single(REGIONPOINT_ID, (x + dx * half, y, z + dz * half), name=f"region{n}_{k}",
                        fields={"region": n, "corner": k})
        return n

    def polygon(self, corners, region: int | None = None) -> int:
        """A navigation region from corners in order (RegionPoints), wound counter-clockwise in (x, z) whichever way
        they were given (the game finds nothing inside a clockwise region, so its squad never moves); returns its
        id."""
        n = region if region is not None else max(self.free("regionpoint", "region"), self.free("squadron", "region"))
        if winding([(c[0], c[2]) for c in corners]) < 0:
            corners = list(reversed(corners))
        for k, (x, y, z) in enumerate(corners):
            self.single(REGIONPOINT_ID, (x, y, z), name=f"region{n}_{k}", fields={"region": n, "corner": k})
        return n

    def hole(self, corners, area: int) -> int:
        """A hole in region `area` (an obstacle): a region of its own from corners in order, wound clockwise, with a
        RegionInfo linking it to the area. Smart squad members keep out of it and run to its corners for cover; an
        area with no hole and no inward corner gives them nowhere to go (decomp MSR mod #6). Returns its id."""
        n = max(self.free("regionpoint", "region"), self.free("squadron", "region"), self.free("regioninfo", "region"))
        if winding([(c[0], c[2]) for c in corners]) > 0:
            corners = list(reversed(corners))
        for k, (x, y, z) in enumerate(corners):
            self.single(REGIONPOINT_ID, (x, y, z), name=f"hole{n}_{k}", fields={"region": n, "corner": k})
        centre = [sum(c[i] for c in corners) / len(corners) for i in range(3)]
        self.single(REGIONINFO_ID, centre, name=f"hole{n}", fields={"region": n, "flags": HOLE, "link": area,
                                                                    "anchored": 0})
        return n

    def cover(self, mesh, squadron: dict) -> dict:
        """Makes the obstacles inside a squad's region holes of it (obstacles()). A smart squad's members only move
        to the corners of holes and inward corners of their region, where the hero can't see them; in a rectangle
        over open floor they stand and fire (decomp MSR mod #6). Obstacles a hole already covers are left. Returns
        {"region", "holes": made, "edge": obstacles touching the edge, "old": holes it had, "small": obstacles too
        small to hide behind (under COVER_MIN across, or thinner than COVER_THIN), "convex", "joined": obstacles
        whose boxes overlapped and became one hole}. The ground the obstacles
        stand on is the region's own (region_ground), not the floor under the squadron."""
        region = squadron.get("fields", {}).get("region")
        corners = self.corners(region)
        if len(corners) < 3:
            raise ValueError(f"the squadron's region {region} has no corners here")
        poly = [(o["pos"][0], o["pos"][2]) for o in corners]
        sx, sy, sz = squadron["pos"]
        ground = region_ground(mesh, poly)
        if ground is None:
            ground = mesh.floor_height(sx, sz, sy + COVER_RISE)
        ground = sy if ground is None else ground
        boxes, edge, joined = obstacles(mesh, poly, ground)
        holes = self.holes_of(region)
        old = []  # the box round each hole the region has (another squad sharing the region made them, or a re-run)
        for h in holes:
            pts = [(o["pos"][0], o["pos"][2]) for o in self.corners(h)]
            if pts:
                old.append((min(x for x, _ in pts), min(z for _, z in pts), max(x for x, _ in pts),
                            max(z for _, z in pts)))
        made = small = 0
        for box in boxes:
            x0, z0, x1, z1 = box
            if any(_overlap(box, o) for o in old) or made >= COVER_MAX:
                continue  # a hole is there already
            if x1 - x0 < COVER_MIN and z1 - z0 < COVER_MIN or min(x1 - x0, z1 - z0) < COVER_THIN:
                small += 1
                continue
            self.hole([(x0, ground, z0), (x0, ground, z1), (x1, ground, z1), (x1, ground, z0)], region)
            old.append(box)
            made += 1
        return {"region": region, "holes": made, "edge": edge, "old": len(holes), "small": small,
                "convex": convex(poly), "joined": joined}

    def patrol_point(self, pos, example) -> dict:
        """A patrol point with the next free node number (they run 1..N with no gaps)."""
        node = self.free("patrolpoint", "node")
        return self.single(PATROL_POINT_ID, pos, example, name=f"patrol{node}",
                           fields={"node": node, "command": 1, "startAtLoad": 0})

    def patrol_link(self, a: dict, b: dict, example) -> dict | None:
        """A two-way link between two patrol points (None when they are already linked)."""
        na, nb = a.get("fields", {}).get("node"), b.get("fields", {}).get("node")
        for o in self._all():
            f = o.get("fields", {})
            if o["type"] == f"stock:objtype/0x{PATROL_PATH_ID:X}" and {f.get("nodeA"), f.get("nodeB")} == {na, nb}:
                return None
        x, y, z = ((p + q) / 2 for p, q in zip(a["pos"], b["pos"], strict=True))
        return self.single(PATROL_PATH_ID, (x, y, z), example, name=f"link{na}_{nb}",
                           fields={"nodeA": na, "nodeB": nb, "weight": 1, "flags": 0xC0})

    def squad(self, pos, squadron_example, member_oid: int, member_example, members: int = 2,
              door_oid: int | None = None, door_example=None, mesh=None) -> list[dict]:
        """A squadron with its members, a region to keep to and, when given, a door it keeps locked; with the level's
        `mesh`, the obstacles in the region made holes for cover (cover())."""
        squadron_id = self.free("squadron", "squadronId")
        door_id = self.free("door", "doorId") if door_oid is not None else 0
        x, y, z = pos
        region = self.region(pos)
        squadron = self.single(SQUADRON_ID, pos, squadron_example, name=f"squad{squadron_id}",
                               fields={"squadronId": squadron_id, "doorId": door_id, "region": region,
                                       "count": members, "baddyType": member_oid, "trigger": TRIGGER_AT_ONCE})
        for k in range(members):
            m = self.single(member_oid, (x + 160 * (k - (members - 1) / 2), y, z), member_example,
                            fields={"squadron": squadron_id})
            m["schema"] = "squadmember"
        if door_oid is not None:
            d = self.single(door_oid, (x, y, z + REGION_HALF + 100), door_example, fields={"doorId": door_id})
            d["schema"] = "door"
        if mesh is not None:
            self.cover(mesh, squadron)
        return self.made

    def used(self, link: str) -> set:
        """The numbers of a link (a schema field's "link": squadronId, region ...) the level's objects name in any
        field of that link, and (in a stock-level edit) the game level's own."""
        out = set()
        for s in self.schemas.values():
            for f in s.fields:
                if f.link == link:
                    out |= {o.get("fields", {}).get(f.name) for o in self._all() if self.key(o) == s.key}
                    out |= self._game_values(s.key, f.name)
        return out - {None}

    def saved(self, doc: dict, pos, mesh=None) -> list[dict]:
        """A prefab of the project's own (capture) with its anchor at pos: each object with a uid of its own, and the
        numbers the prefab gives out (NUMBERS, start points) changed to ones free here, in every field naming them.
        With the level's `mesh`, an object saved with a lift stands that high over the floor under it."""
        objs = [copy.deepcopy(o) for o in doc["objects"]]
        for o in objs:
            o.setdefault("fields", {})
        for link, (giver, field, start, none) in NUMBERS.items():
            given = sorted({o["fields"][field] for o in objs if self.key(o) == giver and field in o["fields"]} - {none})
            if not given:
                continue
            used, mapping = self.used(link), {}
            for v in given:
                mapping[v] = next(n for n in itertools.count(start) if n not in used)
                used.add(mapping[v])
            names = {(s.key, f.name) for s in self.schemas.values() for f in s.fields if f.link == link}
            names.add((giver, field))
            for o in objs:
                for key, name in names:
                    if self.key(o) == key and o["fields"].get(name) in mapping:
                        o["fields"][name] = mapping[o["fields"][name]]
        taken = set()
        for o in objs:
            if self.key(o) == START_NUMBER[0] and START_NUMBER[1] in o["fields"]:
                used = {p.get("fields", {}).get(START_NUMBER[1]) for p in self._all() if self.key(p) == START_NUMBER[0]}
                used |= self._game_values(*START_NUMBER) | taken
                o["fields"][START_NUMBER[1]] = next(n for n in itertools.count(0) if n not in used)
                taken.add(o["fields"][START_NUMBER[1]])
        x, y, z = (float(v) for v in pos)
        for o in objs:
            dx, dy, dz = o["pos"]
            lift = o.pop("lift", None)
            o["pos"] = [round(x + dx), round(y + dy), round(z + dz)]
            if mesh is not None and lift is not None:
                floor = mesh.floor_height(o["pos"][0], o["pos"][2], below=o["pos"][1] - lift + SAVED_DROP)
                if floor is not None:
                    o["pos"][1] = round(floor + lift)
            if not o["fields"]:
                del o["fields"]
            o["uid"] = self.uid(o.get("uid") or o.get("name") or "obj")
            self.made.append(o)
        return self.made

    def exit_pair(self, pos) -> list[dict]:
        """An exit and, in front of it (towards -z), the start point that brings the player back here, facing away
        from the exit."""
        number = self.free("startpoint", "startPoint", start=0)
        x, y, z = pos
        self.single(EXIT_ID, pos, name="exit", fields={"radius": 60})
        self.single(START_ID, (x, y, z - 150), name=f"start{number}",
                    fields={"startPoint": number, "player": 0, "yaw": 32})  # angle10: 32 = half a turn
        return self.made
