"""The planet registry (P4 / S25): a pack's own planets on the planet-select map, for the port's jfg_worldmap.cpp.

A planet without "replaces" gets a point of its own: its disc (`disc`: x, y, radius in the map's coordinates, x right
and y down; `index` names the stock disc whose textures it wears) and the routes that reach it from stock map points
(a stick direction there, the way back, the line's corners). The port keeps the game's map and adds these around it,
so a route needs a direction that no stock route takes at that point.

    jfg_world.json   {"format": 1, "planets": [{"id", "name", "levels": [vela, juno, lupus], "disc": {"looks", "x",
                      "y", "radius", "colour"?}, "routes": [{"from", "dir", "back", "corners"}]}]}
Levels are named as jfg_music.json names them: "stock:N", or "level_<name>" for a level the pack appends.
"""

from jfgstudio import refs

DIRS = ("left", "right", "up", "down")  # the map's direction numbers 0-3
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}
HEROES = ("vela", "juno", "lupus")  # the map's hero order
GAP = 13  # a route line starts and ends this far from a disc's rim (as the stock routes do)
STOCK_LEVELS = 412


def is_registry(planet: dict) -> bool:
    return "replaces" not in planet


def point_positions(doc: dict) -> dict[int, tuple[float, float]]:
    """Stock map point -> (x, y): discs from their icons, the other points (ships, stations) from the route corners
    that reach them."""
    pos = {}
    for k, p in enumerate(doc["points"]):
        if p["kind"] == "disc" and p["index"] < len(doc["icons"]):
            icon = doc["icons"][p["index"]]
            pos[k] = (float(icon["x"]), float(icon["y"]))
    for _ in range(3):  # routes chain from known points
        for r in doc["routes"]:
            corners = r.get("corners") or []
            if not corners:
                continue
            if r["to"] not in pos and r["from"] in pos:
                pos[r["to"]] = tuple(float(v) for v in corners[-1])
            if r["from"] not in pos and r["to"] in pos:
                pos[r["from"]] = tuple(float(v) for v in corners[0])
    return pos


def point_radius(doc: dict, k: int) -> int:
    p = doc["points"][k]
    return doc["icons"][p["index"]]["radius"] if p["kind"] == "disc" else 6


def taken_directions(doc: dict, point: int) -> set[str]:
    """The stick directions stock routes already use at a map point (map.json's route flags: [dirTo, dirBack])."""
    out = set()
    for r in doc["routes"]:
        flags = r.get("flags") or []
        if r["from"] == point and flags:
            out.add(DIRS[flags[0]])
        if r["to"] == point and len(flags) > 1:
            out.add(DIRS[flags[1]])
    return out


def auto_corners(doc: dict, point: int, direction: str, disc: dict) -> list[list[int]]:
    """A route line from a stock point to a disc, as the stock map draws them: out along the stick direction, then
    turning once towards the disc if it is not straight ahead."""
    sx, sy = point_positions(doc)[point]
    sr, dr = point_radius(doc, point) + GAP, disc["radius"] + GAP
    dx, dy = disc["x"], disc["y"]
    if direction in ("left", "right"):
        s = -1 if direction == "left" else 1
        start = [round(sx + s * sr), round(sy)]
        if round(dy) == round(sy):
            return [start, [round(dx - s * dr), round(sy)]]
        v = 1 if dy > sy else -1
        return [start, [round(dx), round(sy)], [round(dx), round(dy - v * dr)]]
    s = -1 if direction == "up" else 1
    start = [round(sx), round(sy + s * sr)]
    if round(dx) == round(sx):
        return [start, [round(sx), round(dy - s * dr)]]
    h = 1 if dx > sx else -1
    return [start, [round(sx), round(dy)], [round(dx - h * dr), round(dy)]]


def level_name(project, ref: str) -> str:
    """A planet's entry level as the port names it."""
    r = refs.parse(ref, "level")
    if r.is_stock:
        return f"stock:{r.number()}"
    level = project.levels[r.name]
    fixed = level.base if level.base is not None else level.level.get("fixedId")
    return f"stock:{fixed}" if fixed is not None and fixed < STOCK_LEVELS else f"level_{r.name}"


def manifest(project, doc: dict | None) -> tuple[dict | None, list[str]]:
    """jfg_world.json for the project's registry planets, and the problems that stop it (they need the catalog's
    map to check the stock routes and draw missing route lines)."""
    planets, problems = [], []
    for name, p in sorted(project.planets.items()):
        if not is_registry(p):
            continue
        where = f"planets/{name}"
        disc = p.get("disc")
        if disc is None or not p.get("routes"):
            problems.append(f"{where}: a new map point needs a disc and a route from a stock map point")
            continue
        if doc is None:
            problems.append(f"{where}: building a new map point needs the catalog (the stock map)")
            continue
        out_disc = {"looks": disc["index"], "x": disc["x"], "y": disc["y"], "radius": disc["radius"]}
        if "colour" in disc:
            out_disc["colour"] = list(disc["colour"])
        routes = []
        for route in p["routes"]:
            r = refs.parse(route["from"])
            if not r.is_stock or r.category != "mappoint":
                problems.append(f"{where}: routes from {route['from']}: the port's map reaches a new planet from a "
                                "stock map point only (routes between pack planets come later)")
                continue
            point = r.number()
            if not 0 <= point < len(doc["points"]):
                problems.append(f"{where}: there is no map point {point}")
                continue
            taken = taken_directions(doc, point)
            if route["dir"] in taken:
                free = ", ".join(d for d in DIRS if d not in taken) or "none"
                problems.append(f"{where}: map point {point} already has a stock route to the {route['dir']}; "
                                f"free directions there: {free}")
                continue
            corners = route.get("corners") or auto_corners(doc, point, route["dir"], disc)
            routes.append({"from": point, "dir": DIRS.index(route["dir"]), "back": DIRS.index(route["back"]),
                           "corners": [list(c) for c in corners]})
        planets.append({"id": name, "name": p["displayName"]["en"],
                        "levels": [level_name(project, p["entryLevel"][h]) for h in HEROES],
                        "disc": out_disc, "routes": routes})
    return ({"format": 1, "planets": planets} if planets else None), problems
