"""The planet registry and galaxies (P4 / S25, Docs/GALAXIES_PLAN.md): a pack's own planets on the planet-select map,
for the port's jfg_worldmap.cpp.

A planet without "replaces" gets a point of its own: its disc (`disc`: x, y, radius in the map's coordinates, x right
and y down; `index` names the stock disc whose textures it wears) and the routes that reach it: from a stock map point
(the game's galaxy) or from another planet of the same galaxy, with a stick direction there, the way back and the
line's corners. A planet is on the game's galaxy, or on a galaxy of the project (`galaxy`: galaxies/<name>.json), a
planet map of its own that the player switches to with L / R. `unlock` says when it shows.

    jfg_world.json   {"format": 2, "galaxies": [{"id", "name", "home"?}],
                      "planets": [{"id", "name", "galaxy", "levels": [vela, juno, lupus], "unlock",
                                   "disc": {"looks", "x", "y", "radius", "colour"?},
                                   "routes": [{"from": <stock point> | "planet:<id>", "dir", "back", "corners"}]}]}
Levels are named as jfg_music.json names them: "stock:N", or "level_<name>" for a level the pack appends.
"""

from collections import deque

from jfgstudio import refs

DIRS = ("left", "right", "up", "down")  # the map's direction numbers 0-3
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}
HEROES = ("vela", "juno", "lupus")  # the map's hero order
GAP = 13  # a route line starts and ends this far from a disc's rim (as the stock routes do)
STOCK_LEVELS = 412
MAIN = "main"


def is_registry(planet: dict) -> bool:
    return "replaces" not in planet


def galaxy_of(planet: dict) -> str:
    """The planet's galaxy: a project galaxy's name, or "main" (the game's)."""
    return planet.get("galaxy") or MAIN


def unlock_of(planet: dict) -> str | dict:
    """The planet's unlock rule, with the default filled in: "stock" on the game's galaxy, "start" on the project's."""
    unlock = planet.get("unlock")
    if not unlock or (isinstance(unlock, dict) and "newGame" in unlock):  # the phase 1 form: not a registry rule
        return "stock" if galaxy_of(planet) == MAIN else "start"
    return unlock


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


def line(start: tuple[float, float], start_radius: float, direction: str, disc: dict) -> list[list[int]]:
    """A route line from a point to a disc, as the stock map draws them: out along the stick direction, then turning
    once towards the disc if it is not straight ahead."""
    sx, sy = start
    sr, dr = start_radius + GAP, disc["radius"] + GAP
    dx, dy = disc["x"], disc["y"]
    if direction in ("left", "right"):
        s = -1 if direction == "left" else 1
        first = [round(sx + s * sr), round(sy)]
        if round(dy) == round(sy):
            return [first, [round(dx - s * dr), round(sy)]]
        v = 1 if dy > sy else -1
        return [first, [round(dx), round(sy)], [round(dx), round(dy - v * dr)]]
    s = -1 if direction == "up" else 1
    first = [round(sx), round(sy + s * sr)]
    if round(dx) == round(sx):
        return [first, [round(sx), round(dy - s * dr)]]
    h = 1 if dx > sx else -1
    return [first, [round(sx), round(dy)], [round(dx - h * dr), round(dy)]]


def auto_corners(doc: dict, point: int, direction: str, disc: dict) -> list[list[int]]:
    """The line from a stock map point to a disc."""
    return line(point_positions(doc)[point], point_radius(doc, point), direction, disc)


def route_corners(project, doc: dict | None, route: dict, disc: dict) -> list[list[int]]:
    """A route's corners: its own, or drawn from where it leaves (a stock point or a planet's disc)."""
    if route.get("corners"):
        return [list(c) for c in route["corners"]]
    r = refs.parse(route["from"])
    if r.is_stock:
        return auto_corners(doc, r.number(), route["dir"], disc)
    other = project.planets[r.name]["disc"]
    return line((other["x"], other["y"]), other["radius"], route["dir"], disc)


def level_name(project, ref: str) -> str:
    """A planet's entry level as the port names it."""
    r = refs.parse(ref, "level")
    if r.is_stock:
        return f"stock:{r.number()}"
    level = project.levels[r.name]
    fixed = level.base if level.base is not None else level.level.get("fixedId")
    return f"stock:{fixed}" if fixed is not None and fixed < STOCK_LEVELS else f"level_{r.name}"


def reachable(project, galaxy: str) -> set[str]:
    """The planets of a project galaxy its home reaches along routes (either way)."""
    home = project.galaxies.get(galaxy, {}).get("home")
    if not home:
        return set()
    links: dict[str, set[str]] = {}
    for name, p in project.planets.items():
        for route in p.get("routes", []):
            r = refs.parse(route["from"])
            if r.category == "world":
                links.setdefault(name, set()).add(r.name)
                links.setdefault(r.name, set()).add(name)
    seen, todo = {home}, deque([home])
    while todo:
        for nxt in links.get(todo.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def manifest(project, doc: dict | None) -> tuple[dict | None, list[str]]:
    """jfg_world.json for the project's registry planets and galaxies, and the problems that stop it (a route from a
    stock point needs the catalog's map, to check the stock routes and draw a missing line)."""
    planets, problems = [], []
    for name, p in sorted(project.planets.items()):
        if not is_registry(p):
            continue
        where = f"planets/{name}"
        disc = p.get("disc")
        galaxy = galaxy_of(p)
        if galaxy != MAIN and galaxy not in project.galaxies:
            problems.append(f"{where}: the project has no galaxy {galaxy}")
            continue
        if disc is None:
            problems.append(f"{where}: a map point of its own needs a disc")
            continue
        out_disc = {"looks": disc["index"], "x": disc["x"], "y": disc["y"], "radius": disc["radius"]}
        if "colour" in disc:
            out_disc["colour"] = list(disc["colour"])
        routes = []
        for route in p.get("routes", []):
            r = refs.parse(route["from"])
            if r.is_stock and r.category == "mappoint":
                if galaxy != MAIN:
                    problems.append(f"{where}: a route from a stock map point, but the planet is on galaxy {galaxy}")
                    continue
                if doc is None:
                    problems.append(f"{where}: a route from a stock map point needs the catalog (the stock map)")
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
                source = point
            elif r.is_pack and r.category == "world":
                other = project.planets.get(r.name)
                if other is None or not is_registry(other) or galaxy_of(other) != galaxy:
                    problems.append(f"{where}: a route from {route['from']}, which is not a planet of its galaxy")
                    continue
                source = f"planet:{r.name}"
            else:
                problems.append(f"{where}: routes start at a stock map point or a planet ({route['from']})")
                continue
            routes.append({"from": source, "dir": DIRS.index(route["dir"]), "back": DIRS.index(route["back"]),
                           "corners": route_corners(project, doc, route, disc)})
        if galaxy == MAIN and not routes:
            problems.append(f"{where}: on the game's galaxy a planet needs a route (from a stock point or a planet)")
            continue
        out = {"id": name, "name": p["displayName"]["en"], "galaxy": galaxy,
               "levels": [level_name(project, p["entryLevel"][h]) for h in HEROES],
               "unlock": unlock_of(p), "disc": out_disc, "routes": routes}
        planets.append(out)
    galaxies = [{"id": name, "name": g["name"], **({"home": g["home"]} if g.get("home") else {})}
                for name, g in sorted(project.galaxies.items())]
    if not planets and not galaxies:
        return None, problems
    return {"format": 2, "galaxies": galaxies, "planets": planets}, problems
