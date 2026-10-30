"""Objects against a level's geometry, for after it was reshaped (a new export from Blender): what is buried, floating,
sunk into the floor or over a pit, and where each would stand instead.

Only things that stand on the floor are checked (players, enemies, pickups, doors, switches, exits, navigation
points); zones, lights, sounds and triggers may be anywhere. Heights follow objects.on_ground, as the editor's "drop
to floor" places them.
"""

import math

from jfgstudio import objects as obj
from jfgstudio.gui.viewport import floor_of, heights_at, raycast

STANDS = {"startpoint", "squadron", "squadmember", "health", "ammo", "key", "chest", "collectable", "token", "door",
          "doorswitch", "onoffswitch", "cellswitch", "damagecrate", "refuel", "exit", "patrolpoint", "staticprop"}
# Not region corners: a region is 2D and the game ignores their height (decomp MSR mod #6), so they may sit in a hill.
RISE = 40  # a floor up to this far above an object is still its own: the ground it stood on rose
FLOAT = 24  # more than this above where it would stand: floating
UP = (0.0, 1.0, 0.0)
SIDES = ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
# Things with a flat base that must sit on level ground (half their width): on a slope one side hovers (a refuel
# pad on noise terrain, play-tested). The game's own sit on flat floor.
FLAT_BASE = {"refuel": 45, "damagecrate": 30, "chest": 45, "door": 60, "startpoint": 25, "exit": 40, "key": 15}
TILT = 10  # the floor under such a base may differ by this much
# Pickups and things you walk up to, and how far round them must be clear: closer to a rock or wall they clip into it
# (a health gem half inside a boulder, play-tested).
CLEAR = {"health": 35, "ammo": 35, "key": 30, "chest": 50, "collectable": 30, "token": 30, "refuel": 40,
         "damagecrate": 40, "startpoint": 40, "exit": 40}
CRATE_SIZE = 39  # the game's usual crate size, which CLEAR's damagecrate radius is for
CLIP = 25  # a floor this far above an object's own, within its CLEAR radius, is something it clips into
RING = ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7))
ENCLOSED = 200  # solid this close above and on all four sides: inside something (a block raised around it)
ROOF_MATCH = 40  # a floor round an object this close in height to a surface over it is that roof, not a rock beside it
CLIP_REACH = 160  # floors round an object up to this far over its own are what it can clip into; higher ones (a roof
#                   over it: a key in a ship's hold, items in a cave) are not (the wall rays catch taller rocks)


def _enclosed(mesh, x: float, y: float, z: float) -> float | None:
    """How far up the solid over an object is, when it is also walled in close on every side; else None. Under a
    deck or in a wide room the side rays run free, and in a small roofed room (a Tribal's cabin) the surface over it
    is a ceiling, facing down: inside a block it is the back of the block's top."""
    at = (x, y + 1, z)
    up = raycast(mesh.collision, at, UP)
    if up is None or up > ENCLOSED:
        return None
    if (abs(heights_at(mesh.collision, x, z, facing=-1) - (y + 1 + up)) < 1).any():
        return None  # a ceiling: a room
    for d in SIDES:
        t = raycast(mesh.collision, at, d)
        if t is None or t > ENCLOSED:
            return None
    return up


def stands(o: dict, schemas: dict, behaviours: dict | None = None) -> obj.Schema | None | bool:
    """The schema of an object that stands on the floor (None for a type without one, often a stock enemy), or False
    for one that may be anywhere."""
    s = obj.schema_for(o, schemas, behaviours)
    if s is not None and s.key not in STANDS:
        return False
    return s


def check(mesh, objects: list[dict], schemas: dict, behaviours: dict | None = None) -> list[dict]:
    """Findings: {uid, problem, pos, by?, standOn?} with problem buried, over a pit, no floor, sunk or floating."""
    kill = mesh.kind_triangles("kill")
    out = []
    for o in objects:
        s = stands(o, schemas, behaviours)
        if s is False:
            continue
        if s and s.key == "squadron" and o.get("fields", {}).get("offsetY", 0) < 0:
            continue  # it launches flyers down to a spot below it: high on purpose (decomp MSR mod #6)
        x, y, z = (float(v) for v in o["pos"])
        f = mesh.floor_height(x, z, y + RISE)
        finding = {"uid": o["uid"], "pos": list(o["pos"])}
        if o.get("name"):
            finding["name"] = o["name"]
        if f is None:
            if raycast(mesh.collision, (x, y, z), UP) is not None:
                finding["problem"] = "buried"  # solid above it and nothing under: inside the ground
            elif floor_of(kill, x, z, y + RISE) is not None:
                finding["problem"] = "over a pit"
            else:
                finding["problem"] = "no floor"
            higher = mesh.floor_height(x, z)
            if higher is not None:
                finding["standOn"] = [round(x), obj.on_ground(higher, s), round(z)]
            out.append(finding)
            continue
        up = _enclosed(mesh, x, y, z)
        if up is not None:  # inside a block that rose around it (the old floor may still be under it)
            top = mesh.floor_height(x, z, y + up + 1)
            finding["problem"] = "buried"
            if top is not None:
                finding["standOn"] = [round(x), obj.on_ground(top, s), round(z)]
            out.append(finding)
            continue
        target = obj.on_ground(f, s)
        clear = CLEAR.get(s.key) if s else None
        if clear is not None and s.key == "damagecrate":  # a bigger crate needs more room (39: the game's usual)
            clear *= max(1.0, o.get("fields", {}).get("size", CRATE_SIZE) / CRATE_SIZE)
        if clear is not None:
            near = [mesh.floor_height(x + dx * clear, z + dz * clear, f + CLIP_REACH) for dx, dz in RING]
            # and a wall with nothing behind it (a level's outer wall has no floor past it to rise)
            walls = [raycast(mesh.collision, (x, f + CLIP, z), (dx, 0.0, dz)) for dx, dz in RING]
            # a floor round it that is also over it is a roof over both (a ship's deck over its hold, a cove's
            # overhang), not a rock beside it
            over = heights_at(mesh.collision, x, z)
            over = over[over > f + CLIP]
            hit_floor = any(h is not None and h > f + CLIP and not (abs(over - h) < ROOF_MATCH).any() for h in near)
            hit_wall = any(d is not None and d < clear * math.hypot(dx, dz)
                           for d, (dx, dz) in zip(walls, RING, strict=True))
            if hit_floor or hit_wall:
                finding.update({"problem": "clipping into geometry"})
                out.append(finding)  # move it clear of the rock or wall beside it
                continue
        half = FLAT_BASE.get(s.key) if s else None
        if half is not None:
            if s.key == "refuel":
                half = max(half, o.get("fields", {}).get("radius", 0))
            points = RING
            yaw_f = s.role("yaw")
            if s.key == "door" and yaw_f is not None:  # a door spans its doorway: level along its width only (a
                a = obj.angle_of(yaw_f, o.get("fields", {}).get(yaw_f.name, 0))  # stair starts just behind a reveal)
                wx, wz = math.cos(a), -math.sin(a)
                points = [(wx * k, wz * k) for k in (1, 0.5, -0.5, -1)]
            ring = [mesh.floor_height(x + dx * half, z + dz * half, y + RISE)
                    for dx, dz in points]
            ring = [h for h in ring if h is not None]
            if ring and max(ring) - min(ring) > TILT:
                finding.update({"problem": "on a slope", "by": round(max(ring) - min(ring), 1)})
                out.append(finding)  # find_flat finds level ground nearby
                continue
        if y < f - 1:  # under the floor's surface (resting on it, or the editor's small lift over it, is fine)
            finding.update({"problem": "sunk", "by": round(f - y, 1)})
        elif y - target > FLOAT:
            finding.update({"problem": "floating", "by": round(y - target, 1)})
        else:
            continue
        finding["standOn"] = [round(x), target, round(z)]
        out.append(finding)
    return out
