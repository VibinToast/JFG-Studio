"""What the Prefabs window places (task S16): one object of a stock type, or a group of objects that work together
(a squad with its region and the door it locks, an exit with the start point that leads back).

A new object either starts from a stock entry (`"stock": {list, entry}`: the build copies its bytes, so fields that
are not decoded yet keep the game's values) or, for fully decoded types, from the schema's defaults alone.
"""

import itertools

from jfgstudio import objects as obj
from jfgstudio.project import Level

EXIT_ID, START_ID, REGIONPOINT_ID, SQUADRON_ID = 0x0B, 0x0D, 0x05, 0x4F
PATROL_POINT_ID, PATROL_PATH_ID = 0x61, 0x60
def winding(points) -> float:
    """Twice the signed area of a polygon of (x, z) corners in order: positive is counter-clockwise, the way the game
    wants a region (IsPointInRegion finds nothing inside a clockwise one; decomp MSR mod #6)."""
    return sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(points, points[1:] + points[:1], strict=True))


TRIGGER_AT_ONCE = 3  # squadron +0x29: spawn at level start (a stock squad's 1 waits in its own level's zone)
REGION_HALF = 300  # a new squad's region: a square this far around it


class Placer:
    """Makes objects for one level, with uids and link numbers that are free in it (and in what it made so far)."""

    def __init__(self, level: Level, schemas: dict, behaviours: dict | None = None):
        self.level, self.schemas, self.behaviours = level, schemas, behaviours or {}
        self.made: list[dict] = []

    def _all(self) -> list[dict]:
        return self.level.objects["objects"] + self.made

    def uid(self, stem: str) -> str:
        used = {o["uid"] for o in self._all()}
        stem = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in stem.lower()).rstrip("0123456789_") or "obj"
        return next(f"{stem}{k}" for k in itertools.count(1) if f"{stem}{k}" not in used)

    def free(self, key: str, field: str, start: int = 1) -> int:
        """The lowest number from `start` that no object of schema `key` uses in `field`."""
        used = {o.get("fields", {}).get(field) for o in self._all()
                if (s := obj.schema_for(o, self.schemas, self.behaviours)) and s.key == key}
        return next(n for n in itertools.count(start) if n not in used)

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
              door_oid: int | None = None, door_example=None) -> list[dict]:
        """A squadron with its members, a region to keep to and, when given, a door it keeps locked."""
        squadron_id = self.free("squadron", "squadronId")
        door_id = self.free("door", "doorId") if door_oid is not None else 0
        x, y, z = pos
        region = self.region(pos)
        self.single(SQUADRON_ID, pos, squadron_example, name=f"squad{squadron_id}",
                    fields={"squadronId": squadron_id, "doorId": door_id, "region": region, "count": members,
                            "baddyType": member_oid, "trigger": TRIGGER_AT_ONCE})
        for k in range(members):
            m = self.single(member_oid, (x + 160 * (k - (members - 1) / 2), y, z), member_example,
                            fields={"squadron": squadron_id})
            m["schema"] = "squadmember"
        if door_oid is not None:
            d = self.single(door_oid, (x, y, z + REGION_HALF + 100), door_example, fields={"doorId": door_id})
            d["schema"] = "door"
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
