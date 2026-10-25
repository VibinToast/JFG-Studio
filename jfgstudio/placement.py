"""Objects against a level's geometry, for after it was reshaped (a new export from Blender): what is buried, floating,
sunk into the floor or over a pit, and where each would stand instead.

Only things that stand on the floor are checked (players, enemies, pickups, doors, switches, exits, navigation
points); zones, lights, sounds and triggers may be anywhere. Heights follow objects.on_ground, as the editor's "drop
to floor" places them.
"""

from jfgstudio import objects as obj
from jfgstudio.gui.viewport import floor_of, raycast

STANDS = {"startpoint", "squadron", "squadmember", "health", "ammo", "key", "chest", "collectable", "token", "door",
          "doorswitch", "onoffswitch", "cellswitch", "damagecrate", "refuel", "exit", "regionpoint", "patrolpoint",
          "staticprop"}
RISE = 40  # a floor up to this far above an object is still its own: the ground it stood on rose
FLOAT = 24  # more than this above where it would stand: floating
UP = (0.0, 1.0, 0.0)
SIDES = ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
ENCLOSED = 200  # solid this close above and on all four sides: inside something (a block raised around it)


def _enclosed(mesh, x: float, y: float, z: float) -> float | None:
    """How far up the solid over an object is, when it is also walled in close on every side; else None. Under a
    deck or in a wide room the side rays run free."""
    at = (x, y + 1, z)
    up = raycast(mesh.collision, at, UP)
    if up is None or up > ENCLOSED:
        return None
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
        x, y, z = (float(v) for v in o["pos"])
        f = floor_of(mesh.collision, x, z, y + RISE)
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
            higher = floor_of(mesh.collision, x, z)
            if higher is not None:
                finding["standOn"] = [round(x), obj.on_ground(higher, s), round(z)]
            out.append(finding)
            continue
        up = _enclosed(mesh, x, y, z)
        if up is not None:  # inside a block that rose around it (the old floor may still be under it)
            top = floor_of(mesh.collision, x, z, y + up + 1)
            finding["problem"] = "buried"
            if top is not None:
                finding["standOn"] = [round(x), obj.on_ground(top, s), round(z)]
            out.append(finding)
            continue
        target = obj.on_ground(f, s)
        if y < f - 1:  # under the floor's surface (resting on it, or the editor's small lift over it, is fine)
            finding.update({"problem": "sunk", "by": round(f - y, 1)})
        elif y - target > FLOAT:
            finding.update({"problem": "floating", "by": round(y - target, 1)})
        else:
            continue
        finding["standOn"] = [round(x), target, round(z)]
        out.append(finding)
    return out
