"""A top-down picture of a level for agents (and anyone without the editor open): the geometry seen from above, shaded
by height and coloured by surface, with the objects drawn on it. numpy and zlib only, no window.

Up in the picture is -z (the way a start point at yaw 0 faces), right is +x.
"""

import math
import struct
import zlib

import numpy as np

from jfgstudio import objects as obj

SURFACE_RGB = {"solid": (120, 150, 105), "ledge": (235, 200, 60), "lava": (230, 110, 30), "hidden": (110, 110, 120),
               "water": (60, 120, 220),
               "kill": (90, 20, 30), "nocollide": (150, 140, 120)}
DRAW_ORDER = ("kill", "solid", "ledge", "hidden", "nocollide", "lava", "water")  # later kinds win where they are on top
BACKGROUND = (16, 16, 20)
MARKS = {  # schema key -> (colour, shape, size in pixels)
    "startpoint": ((255, 255, 255), "arrow", 6),
    "exit": ((0, 230, 230), "ring", 0),
    "squadron": ((255, 60, 60), "square", 4),
    "squadmember": ((255, 60, 60), "dot", 3),
    "door": ((255, 220, 0), "bar", 6),
    "key": ((255, 200, 0), "diamond", 5),
    "health": ((80, 255, 80), "dot", 3),
    "ammo": ((80, 255, 80), "dot", 3),
    "chest": ((80, 255, 80), "square", 3),
    "light": ((255, 255, 170), "dot", 2),
    "glow": ((255, 255, 170), "dot", 2),
    "patrolpoint": ((90, 190, 255), "dot", 3),
}
OTHER = ((200, 120, 255), "dot", 2)
REGION_RGB = (255, 110, 110)
HOLE_RGB = (255, 90, 200)  # a hole of a region: an obstacle squads take cover behind
PATROL_RGB = (90, 190, 255)  # patrol points and the links between them
BASE_RGB = (150, 150, 150)


def _png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + rgb[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class Canvas:
    def __init__(self, lo, hi, size: int):
        span = max(float(hi[0] - lo[0]), float(hi[2] - lo[2]), 1.0)
        self.scale = (size - 8) / span  # pixels per unit, with a margin
        self.x0, self.z0 = float(lo[0]), float(lo[2])
        self.w = int(math.ceil((hi[0] - lo[0]) * self.scale)) + 8
        self.h = int(math.ceil((hi[2] - lo[2]) * self.scale)) + 8
        self.rgb = np.zeros((self.h, self.w, 3), np.float64)
        self.rgb[:] = BACKGROUND

    def px(self, x: float, z: float) -> tuple[float, float]:
        return (x - self.x0) * self.scale + 4, (z - self.z0) * self.scale + 4

    def put(self, x: int, y: int, rgb) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.rgb[y, x] = rgb

    def line(self, a, b, rgb) -> None:
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
        for t in np.linspace(0, 1, n):
            self.put(int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t)), rgb)


def _layers(mesh, colours: bool):
    """(triangles (n, 3, 3), a colour per triangle (n, 3) or None, the kind's colour, shade by height) per layer, in
    drawing order. `colours`: the baked vertex colours (each triangle's average), as the game lights the level."""
    order = {k: i for i, k in enumerate(DRAW_ORDER)}
    if colours and hasattr(mesh, "parts"):
        for p in sorted(mesh.parts, key=lambda p: order.get(p[5], 0)):
            if p[5] != "hidden":
                cols = np.asarray(p[2], np.float64).reshape(-1, 3, 4)[:, :, :3].mean(axis=1) * 255
                yield np.asarray(p[0], np.float64).reshape(-1, 3, 3), cols, None, False
        return
    for kind in DRAW_ORDER:
        yield mesh.kind_triangles(kind), None, np.array(SURFACE_RGB[kind], np.float64), True


def _surfaces(c: Canvas, mesh, colours: bool = False) -> None:
    """Each layer's triangles rasterised from above: the topmost one wins, shaded by its height (or in its baked
    colour)."""
    lo, hi = mesh.bounds
    span_y = max(float(hi[1] - lo[1]), 1.0)
    top = np.full((c.h, c.w), -np.inf)
    for tris, cols, kind_rgb, shaded in _layers(mesh, colours):
        for i, t in enumerate(tris):
            base = kind_rgb if cols is None else cols[i]
            p = np.array([c.px(v[0], v[2]) for v in t])
            x0, y0 = np.floor(p.min(axis=0)).astype(int)
            x1, y1 = np.ceil(p.max(axis=0)).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, c.w - 1), min(y1, c.h - 1)
            if x0 > x1 or y0 > y1:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax, ay), (bx, by), (cx, cy) = p
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-9:
                continue  # edge-on from above (a wall)
            u = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            v = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            w = 1 - u - v
            inside = (u >= -1e-6) & (v >= -1e-6) & (w >= -1e-6)
            if not inside.any():
                continue
            y = u * t[0][1] + v * t[1][1] + w * t[2][1]
            region = top[y0:y1 + 1, x0:x1 + 1]
            win = inside & (y >= region)
            region[win] = y[win]
            if shaded:
                shade = 0.45 + 0.75 * (y[win] - float(lo[1])) / span_y
                c.rgb[y0:y1 + 1, x0:x1 + 1][win] = np.clip(base[None, :] * shade[:, None], 0, 255)
            else:
                c.rgb[y0:y1 + 1, x0:x1 + 1][win] = np.clip(base, 0, 255)


def _mark(c: Canvas, o: dict, key: str | None, schema) -> None:
    rgb, shape, size = MARKS.get(key, OTHER)
    if o.get("base"):
        rgb = BASE_RGB  # the game level's own object, under an edit
    x, y = c.px(o["pos"][0], o["pos"][2])
    xi, yi = int(round(x)), int(round(y))
    if shape == "ring":
        r = max(3.0, o.get("fields", {}).get("radius", 60) * c.scale)
        for k in range(48):
            a = 2 * math.pi * k / 48
            c.put(int(round(x + r * math.cos(a))), int(round(y + r * math.sin(a))), rgb)
        size = 2
    if shape in ("dot", "ring"):
        for dx in range(-size, size + 1):
            for dy in range(-size, size + 1):
                if dx * dx + dy * dy <= size * size:
                    c.put(xi + dx, yi + dy, rgb)
    elif shape in ("square", "diamond"):
        for dx in range(-size, size + 1):
            for dy in range(-size, size + 1):
                if shape == "square" or abs(dx) + abs(dy) <= size:
                    c.put(xi + dx, yi + dy, rgb)
    elif shape in ("arrow", "bar"):
        yaw_f = schema.role("yaw") if schema else None
        a = obj.angle_of(yaw_f, o.get("fields", {}).get(yaw_f.name, 0)) if yaw_f else 0.0
        fx, fy = -math.sin(a), -math.cos(a)  # yaw 0 faces -z: up in the picture
        if shape == "arrow":
            c.line((x, y), (x + fx * size * 2, y + fy * size * 2), rgb)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    c.put(xi + dx, yi + dy, rgb)
        else:  # a door: a bar across its facing
            c.line((x - fy * size, y + fx * size), (x + fy * size, y - fx * size), rgb)
            c.line((x - fy * size + fx, y + fx * size + fy), (x + fy * size + fx, y - fx * size + fy), rgb)


def hole_regions(objects: list[dict], schemas: dict, behaviours: dict | None = None) -> dict[int, int]:
    """Hole region -> the area it is cut from (prefabs.hole_regions)."""
    from jfgstudio import prefabs

    def key(o):
        s = obj.schema_for(o, schemas, behaviours)
        return s.key if s else None
    return prefabs.hole_regions(objects, key)


def render(mesh, objects: list[dict], schemas: dict, behaviours: dict | None = None, size: int = 512,
           colours: bool = False) -> tuple[bytes, dict]:
    """(PNG bytes, legend) of a level from above. Objects with "base" are a game level's own (drawn grey);
    `colours` draws the baked vertex colours instead of the surface kinds."""
    lo, hi = mesh.bounds
    c = Canvas(lo, hi, max(64, min(int(size), 2048)))
    _surfaces(c, mesh, colours)
    corners: dict[int, list] = {}
    keyed = []
    for o in objects:
        s = obj.schema_for(o, schemas, behaviours)
        key = s.key if s else None
        if key == "regionpoint":
            f = o.get("fields", {})
            corners.setdefault(f.get("region", 0), []).append((f.get("corner", 0), c.px(o["pos"][0], o["pos"][2])))
        else:
            keyed.append((o, key, s))
    nodes = {o.get("fields", {}).get("node"): c.px(o["pos"][0], o["pos"][2]) for o, key, _ in keyed
             if key == "patrolpoint"}
    for o, key, _ in keyed:  # patrol routes: their links, then the points
        f = o.get("fields", {})
        if key == "patrolpath" and f.get("nodeA") in nodes and f.get("nodeB") in nodes:
            (ax, ay), (bx, by) = nodes[f["nodeA"]], nodes[f["nodeB"]]
            for dx, dy in ((0, 0), (1, 0), (0, 1)):  # two pixels wide: a route stands out from region outlines
                c.line((ax + dx, ay + dy), (bx + dx, by + dy), PATROL_RGB)
    keyed = [k for k in keyed if k[1] != "patrolpath"]
    holes = hole_regions(objects, schemas, behaviours)
    for region, pts in corners.items():
        pts = [p for _, p in sorted(pts, key=lambda q: q[0])]
        rgb = HOLE_RGB if region in holes else REGION_RGB
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            c.line(a, b, rgb)
        if region in holes and len(pts) >= 4:  # crossed, as the editor draws it
            half = len(pts) // 2
            c.line(pts[0], pts[half], rgb)
            c.line(pts[half // 2], pts[half + (len(pts) - half) // 2], rgb)
    for o, key, s in sorted(keyed, key=lambda t: t[1] not in ("light", "glow")):  # lights under the rest
        _mark(c, o, key, s)
    legend = {"up": "-z", "right": "+x", "unitsPerPixel": round(1 / c.scale, 2),
              "topLeft": [round(float(lo[0])), round(float(lo[2]))],
              "surfaces": {k: f"rgb{SURFACE_RGB[k]}" for k in DRAW_ORDER}, "shading": "brighter = higher",
              "marks": {"start point": "white square with an arrow (its facing)", "exit": "cyan ring (its radius)",
                        "squad": "red square (squadron) and red dots (members), red outline (their region)",
                        "hole": "pink crossed outline: a hole of a region, where squads take cover",
                        "patrol": "blue dots (patrol points) joined by blue lines (the links squads walk)",
                        "door": "yellow bar across its facing", "key": "gold diamond",
                        "pickups": "green dots", "lights": "pale yellow dots", "other": "purple dots",
                        "grey": "the game level's own start points and exits, under an edit of it"}}
    if colours:
        legend["surfaces"] = "the baked vertex colours, as the game lights the level (colliders left out)"
        legend.pop("shading")
    return _png(c.rgb), legend
