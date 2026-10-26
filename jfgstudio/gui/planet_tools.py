"""The Planet window (tasks S18, S25, galaxies): a pack planet on the planet-select map.

A planet has a map point of its own (the port's planet registry, worldmap.py): on the game's galaxy, drawn over the
stock map from the user's catalog, reached from a stock point in a free stick direction; or on one of the project's
galaxies (a planet map of the pack's own, which the player switches to with L / R), drawn on an empty grid, reached
from another planet of that galaxy. Or it takes over a stock map point (`replaces`). Its entry level per hero, its
name and when it opens.
"""

import math

from imgui_bundle import hello_imgui, imgui

from jfgstudio import refs, worldmap
from jfgstudio.gui import theme

HEROES = ("juno", "vela", "lupus")
MAP_W, MAP_H = 420.0, 800.0  # the map's own coordinates (discs and route corners)
UNLOCKS = ("stock", "start", "discovered", "script", "entered", "completed")
UNLOCK_LABELS = {"stock": "with the stock point its route leaves", "start": "from the start",
                 "discovered": "once its own level was entered (a portal, an exit)",
                 "script": "by a trigger or script", "entered": "once another planet was entered",
                 "completed": "once another planet was completed"}


def _app():
    from jfgstudio.gui import app
    return app


def map_doc() -> dict | None:
    S = _app().S
    if S.catalog() is None:
        return None
    if S.map_doc is None:
        import json
        with open(S.cat.dir / "map.json", encoding="utf-8") as f:
            S.map_doc = json.load(f)
    return S.map_doc


point_positions = worldmap.point_positions


def _world_label(doc: dict, world: int) -> str:
    S = _app().S
    entry = next((w["levels"][1] for w in doc["worldLevels"] if w["world"] == world), None)
    if entry is not None and S.cat:
        levels = S.cat.levels()
        if 0 <= entry < len(levels):
            return f"world {world}: {levels[entry]['name']}"
    return f"world {world}"


def _galaxy_label(name: str) -> str:
    S = _app().S
    if name == worldmap.MAIN:
        return "The game's galaxy"
    return f"{S.project.galaxies[name]['name']} ({name})" if name in S.project.galaxies else name


def planet_window() -> None:
    A = _app()
    S = A.S
    if not S.project:
        A._muted("Open a project.")
        return
    planets = S.project.planets
    names = list(planets)
    if S.planet not in planets:
        S.planet = names[0] if names else None
    if imgui.button("New planet"):
        n = next(f"planet{k}" for k in range(1, 99) if f"planet{k}" not in planets)
        first = next(iter(S.project.levels), None)
        entry = f"pack:level/{first}" if first else "stock:level/21"
        planets[n] = {"format": 1, "displayName": {"en": n.title()},
                      "disc": {"index": 2, "x": 240, "y": 40, "radius": 14},
                      "entryLevel": dict.fromkeys(HEROES, entry)}
        if S.planet in planets and planets[S.planet].get("galaxy"):  # next to the one shown: same galaxy
            planets[n]["galaxy"] = planets[S.planet]["galaxy"]
        S.planet = n
        S.planets_dirty = True
        S.validate()
    if names:
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(12))
        if imgui.begin_combo("##planet", S.planet or ""):
            for n in names:
                label = f"{n}  ({_galaxy_label(worldmap.galaxy_of(planets[n]))})"
                if imgui.selectable(label, n == S.planet)[0]:
                    S.planet = n
            imgui.end_combo()
    if S.planet is None:
        A._muted("This project has no planet yet: New planet puts one on the map.")
        return
    p = planets[S.planet]
    doc = map_doc()
    avail = imgui.get_content_region_avail()
    side = avail.x > avail.y * 1.1
    map_w = (avail.x * 0.5) if side else avail.x
    map_h = min(avail.y if side else avail.y * 0.55, map_w * MAP_H / MAP_W)
    imgui.begin_child("map", imgui.ImVec2(map_w, map_h), imgui.ChildFlags_.borders)
    if worldmap.galaxy_of(p) != worldmap.MAIN:
        _draw_map(None, p)
    elif doc is None:
        A._muted(f"No catalog: {S.cat_error}")
    else:
        _draw_map(doc, p)
    imgui.end_child()
    if side:
        imgui.same_line()
    imgui.begin_child("planet", imgui.ImVec2(0, 0))
    imgui.push_item_width(-hello_imgui.em_size(8.5))
    _planet_fields(p, doc)
    imgui.pop_item_width()
    imgui.end_child()


def _draw_map(doc: dict | None, p: dict) -> None:
    """The planet's galaxy: the stock map (doc) with the project's planets on it, or (doc None) an empty grid with
    the planets of a project galaxy. Clicks place the disc and pick where the route starts."""
    S = _app().S
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    avail = imgui.get_content_region_avail()
    scale = min(avail.x / MAP_W, avail.y / MAP_H)
    ox = origin.x + (avail.x - MAP_W * scale) / 2
    galaxy = worldmap.galaxy_of(p)

    def at(x, y):
        return imgui.ImVec2(ox + x * scale, origin.y + y * scale)

    pos = point_positions(doc) if doc else {}
    border = imgui.get_color_u32(theme.colour("BORDER"))
    if doc:
        for r in doc["routes"]:
            chain = ([pos[r["from"]]] if r["from"] in pos else []) + [tuple(c) for c in r.get("corners") or []] + (
                [pos[r["to"]]] if r["to"] in pos else [])
            for a, b in zip(chain, chain[1:], strict=False):
                draw.add_line(at(*a), at(*b), border, 2.0)
    else:
        for gx in range(0, int(MAP_W) + 1, 50):  # the map's own grid: 50-unit cells
            draw.add_line(at(gx, 0), at(gx, MAP_H), border, 1.0)
        for gy in range(0, int(MAP_H) + 1, 50):
            draw.add_line(at(0, gy), at(MAP_W, gy), border, 1.0)
    mouse = imgui.get_mouse_pos()
    hovered_planet = None
    for name, other in S.project.planets.items():  # the project's own points on this galaxy
        if not worldmap.is_registry(other) or "disc" not in other or worldmap.galaxy_of(other) != galaxy:
            continue
        d = other["disc"]
        mine = other is p
        colour = imgui.get_color_u32(theme.colour("HIGHLIGHT" if mine else "ACCENT" if doc is None else "MUTED"))
        for route in other.get("routes", []):
            try:
                corners = worldmap.route_corners(S.project, doc, route, d)
            except (refs.RefError, KeyError, IndexError, ValueError, TypeError):
                continue
            for a, b in zip(corners, corners[1:], strict=False):
                draw.add_line(at(*a), at(*b), colour, 2.0)
        centre = at(d["x"], d["y"])
        r = max(d["radius"] * scale, 5.0)
        draw.add_circle_filled(centre, r, colour)
        draw.add_text(imgui.ImVec2(centre.x + r + 3, centre.y - 8), colour, other["displayName"].get("en", ""))
        if not mine and math.hypot(mouse.x - centre.x, mouse.y - centre.y) <= max(r, 8.0):
            hovered_planet = name
    taken = p.get("replaces")
    hovered_point = None
    for k, xy in pos.items():
        point = doc["points"][k]
        radius = doc["icons"][point["index"]]["radius"] if point["kind"] == "disc" else 6
        r = max(radius * scale, 5.0)
        centre = at(*xy)
        chosen = taken == f"stock:mappoint/{k}"
        colour = theme.colour("HIGHLIGHT" if chosen else "ACCENT" if point["kind"] == "disc" else "MUTED")
        if point["kind"] == "disc":
            draw.add_circle_filled(centre, r, imgui.get_color_u32(theme.colour("FRAME")))
            draw.add_circle(centre, r, imgui.get_color_u32(colour), 0, 3.0 if chosen else 1.5)
        else:
            draw.add_rect(imgui.ImVec2(centre.x - r, centre.y - r), imgui.ImVec2(centre.x + r, centre.y + r),
                          imgui.get_color_u32(colour), 0.0, 3.0 if chosen else 1.5)
        draw.add_text(imgui.ImVec2(centre.x + r + 3, centre.y - 8), imgui.get_color_u32(colour), str(k))
        if math.hypot(mouse.x - centre.x, mouse.y - centre.y) <= max(r, 8.0):
            hovered_point = k
    imgui.invisible_button("mapcanvas", imgui.ImVec2(max(avail.x, 1), max(avail.y, 1)))
    if worldmap.is_registry(p) and imgui.is_item_hovered():
        mx, my = (mouse.x - ox) / scale, (mouse.y - origin.y) / scale
        if hovered_planet is not None:
            free = _free_dirs_from(f"pack:world/{hovered_planet}", doc)
            theme.tip(f"planet {hovered_planet}\nclick: the route leaves from here (free: "
                              f"{', '.join(free) or 'none'})")
        elif hovered_point is not None:
            theme.tip(f"map point {hovered_point}: "
                              f"{_world_label(doc, doc['points'][hovered_point]['world'])}\nclick: the route leaves "
                              f"from here (free: {', '.join(_free_dirs(doc, hovered_point)) or 'none'})")
        else:
            theme.tip(f"({mx:.0f}, {my:.0f})\nclick: put the planet's disc here")
        if imgui.is_item_clicked():
            source = (f"pack:world/{hovered_planet}" if hovered_planet is not None
                      else f"stock:mappoint/{hovered_point}" if hovered_point is not None else None)
            if source is not None:
                free = _free_dirs_from(source, doc)
                if free:
                    p["routes"] = [{"from": source, "dir": free[0], "back": worldmap.OPPOSITE[free[0]]}]
                else:
                    S.message = f"{source} has no free direction"
            else:
                p.setdefault("disc", {"index": 2, "radius": 14}).update({"x": round(mx), "y": round(my)})
            S.planets_dirty = True
            S.validate()
        return
    if doc and imgui.is_item_hovered() and hovered_point is not None:
        theme.tip(f"map point {hovered_point}: {_world_label(doc, doc['points'][hovered_point]['world'])}"
                          "\nclick: this planet takes it over")
        if imgui.is_item_clicked():
            p["replaces"] = f"stock:mappoint/{hovered_point}"
            p.pop("disc", None)
            p.pop("routes", None)
            S.planets_dirty = True
            S.validate()


def _planet_fields(p: dict, doc: dict | None) -> None:
    A = _app()
    S = A.S
    from jfgstudio.gui import level_tools

    def dirty():
        S.planets_dirty = True
        S.validate()

    A._title(p["displayName"].get("en", S.planet))
    changed, name = imgui.input_text("Name (en)", p["displayName"].get("en", ""),
                                     imgui.InputTextFlags_.enter_returns_true)
    if changed and name.strip():
        p["displayName"]["en"] = name.strip()
        dirty()
    if imgui.is_item_hovered():
        theme.tip("Shown next to the planet on the map")
    own = worldmap.is_registry(p)
    if imgui.radio_button("A point of its own", own) and not own:
        p.pop("replaces", None)
        p.pop("fixedWorld", None)
        p.setdefault("disc", {"index": 2, "x": 240, "y": 40, "radius": 14})
        dirty()
    imgui.same_line()
    if imgui.radio_button("Take over a stock point", not own) and own:
        p["replaces"] = "stock:mappoint/6"
        p["fixedWorld"] = 15
        for key in ("routes", "disc", "galaxy", "unlock"):
            p.pop(key, None)
        dirty()
    if own:
        _galaxy_fields(p, dirty)
        _own_point_fields(p, doc, dirty)
        _unlock_fields(p, dirty)
    else:
        taken = p.get("replaces")
        if taken and doc:
            k = refs.parse(taken, "mappoint").number()
            A._muted(f"Takes over map point {k} ({_world_label(doc, doc['points'][k]['world'])}). Click another "
                     "point on the map to move it.")
        world = p.get("fixedWorld", 15)
        changed, world = imgui.input_int("World", world)
        if changed:
            p["fixedWorld"] = int(min(max(world, 0), 15))
            dirty()
        if imgui.is_item_hovered():
            theme.tip("The world number the planet's levels use (15 is free in the stock game)")
    imgui.separator_text("Entry level")
    choices = level_tools.level_choices()
    labels = dict(choices)
    for hero in HEROES:
        current = p["entryLevel"].get(hero, "")
        if imgui.begin_combo(hero.title(), labels.get(current, current), imgui.ComboFlags_.height_large):
            for value, text in choices:
                if imgui.selectable(text, value == current)[0]:
                    p["entryLevel"][hero] = value
                    dirty()
            imgui.end_combo()
    if not own:
        A._muted("The planet unlocks the way the world it takes over does.")


def _galaxy_fields(p: dict, dirty) -> None:
    """The planet's galaxy, and the galaxy's own name and home."""
    A = _app()
    S = A.S
    galaxies = S.project.galaxies
    current = worldmap.galaxy_of(p)
    imgui.separator_text("Galaxy")
    if imgui.begin_combo("Galaxy", _galaxy_label(current)):
        for name in [worldmap.MAIN, *sorted(galaxies)]:
            if imgui.selectable(_galaxy_label(name), name == current)[0] and name != current:
                _move_to_galaxy(p, name)
                dirty()
        if imgui.selectable("New galaxy...", False)[0]:
            n = next(f"galaxy{k}" for k in range(1, 99) if f"galaxy{k}" not in galaxies)
            galaxies[n] = {"format": 1, "name": f"Galaxy {n[6:]}", "home": S.planet}
            _move_to_galaxy(p, n)
            dirty()
        imgui.end_combo()
    if imgui.is_item_hovered():
        theme.tip("The game's own planet map, or a planet map of this pack's own: the player switches between "
                          "galaxies with L and R on the planet map")
    if current == worldmap.MAIN or current not in galaxies:
        return
    g = galaxies[current]
    changed, gname = imgui.input_text("Galaxy name", g["name"], imgui.InputTextFlags_.enter_returns_true)
    if changed and gname.strip():
        g["name"] = gname.strip()[:40]
        dirty()
    home = g.get("home") == S.planet
    changed, home = imgui.checkbox("The galaxy's home (the cursor starts here)", home)
    if changed and home:
        g["home"] = S.planet
        dirty()
    if imgui.small_button("Delete this galaxy"):
        for other in S.project.planets.values():
            if other.get("galaxy") == current:
                _move_to_galaxy(other, worldmap.MAIN)
        del galaxies[current]
        dirty()


def _move_to_galaxy(p: dict, galaxy: str) -> None:
    """A planet onto another galaxy: its routes stay only if they still make sense there."""
    if galaxy == worldmap.MAIN:
        p.pop("galaxy", None)
        if p.get("unlock") == "start":
            p.pop("unlock")
    else:
        p["galaxy"] = galaxy
        if p.get("unlock") == "stock":
            p.pop("unlock")
    p["routes"] = []


def _free_dirs(doc: dict, point: int) -> list[str]:
    """Stick directions at a stock point that no stock route and no other project planet takes."""
    return _free_dirs_from(f"stock:mappoint/{point}", doc)


def _free_dirs_from(source: str, doc: dict | None) -> list[str]:
    """Stick directions at a stock point or a planet that no stock route, no other route from there and (for a
    planet) none of its own ways back take."""
    S = _app().S
    me = S.project.planets.get(S.planet)
    r = refs.parse(source)
    used = worldmap.taken_directions(doc, r.number()) if r.is_stock and doc else set()
    for other in S.project.planets.values():
        for route in other.get("routes", []):
            if route.get("from") == source and other is not me:
                used.add(route["dir"])
    if not r.is_stock:
        for route in S.project.planets.get(r.name, {}).get("routes", []):
            used.add(route["back"])
    return [d for d in worldmap.DIRS if d not in used]


def _own_point_fields(p: dict, doc: dict | None, dirty) -> None:
    A = _app()
    on_main = worldmap.galaxy_of(p) == worldmap.MAIN
    A._muted("Click empty map space to place the disc, then " + (
        "a stock point or another planet" if on_main else "another planet of this galaxy") +
        " for the route that reaches it." + ("" if on_main else " The home planet needs no route."))
    disc = p.setdefault("disc", {"index": 2, "x": 240, "y": 40, "radius": 14})
    c, xy = imgui.input_int2("Disc x y", [disc["x"], disc["y"]])
    if c:
        disc["x"], disc["y"] = xy
        dirty()
    c, disc["radius"] = imgui.slider_int("Radius", disc["radius"], 4, 30)
    if c:
        dirty()
    looks = [f"disc {k}" for k in range(11)]
    c, disc["index"] = imgui.combo("Looks like", disc["index"], looks)
    if c:
        dirty()
    if imgui.is_item_hovered():
        theme.tip("The stock disc whose textures it wears")
    has = "colour" in disc
    c, has = imgui.checkbox("Top colour", has)
    if c:
        if has:
            disc["colour"] = [255, 255, 255]
        else:
            disc.pop("colour")
        dirty()
    if has:
        imgui.same_line()
        c, col = imgui.color_edit3("##top", [v / 255 for v in disc["colour"]], imgui.ColorEditFlags_.no_inputs)
        if c:
            disc["colour"] = [round(v * 255) for v in col]
            dirty()
    imgui.separator_text("Route")
    routes = p.setdefault("routes", [])
    if not routes:
        A._muted("No route yet: click where it leaves from on the map.")
        return
    route = routes[0]
    try:
        r = refs.parse(route["from"])
    except refs.RefError:
        A._muted(f"{route['from']}: not a map point or a planet")
        return
    if r.is_stock:
        label = _world_label(doc, doc["points"][r.number()]["world"]) if doc else ""
        imgui.text(f"From map point {r.number()} {label}")
    else:
        imgui.text(f"From planet {r.name}")
    free = _free_dirs_from(route["from"], doc)
    if route["dir"] not in free:
        free = [route["dir"], *free]
    if imgui.begin_combo("Stick direction", route["dir"]):
        for d in free:
            if imgui.selectable(d, d == route["dir"])[0]:
                route["dir"], route["back"] = d, worldmap.OPPOSITE[d]
                route.pop("corners", None)
                dirty()
        imgui.end_combo()
    if route.get("corners"):
        imgui.text(f"Line: {route['corners']}")
        if imgui.small_button("Draw the line again"):
            route.pop("corners")
            dirty()
    else:
        A._muted("The build draws the line (out along the stick direction, one turn to the disc).")


def _unlock_fields(p: dict, dirty) -> None:
    S = _app().S
    imgui.separator_text("Unlock")
    on_main = worldmap.galaxy_of(p) == worldmap.MAIN
    unlock = worldmap.unlock_of(p)
    kind = next(iter(unlock)) if isinstance(unlock, dict) else unlock
    if unlock == {"entered": S.planet}:
        kind = "discovered"
    kinds = [k for k in UNLOCKS if on_main or k != "stock"]
    if imgui.begin_combo("Opens", UNLOCK_LABELS.get(kind, kind)):
        for k in kinds:
            if imgui.selectable(UNLOCK_LABELS[k], k == kind)[0] and k != kind:
                others = sorted(n for n in S.project.planets if n != S.planet)
                if k == "discovered":
                    p["unlock"] = {"entered": S.planet}
                else:
                    p["unlock"] = {k: others[0] if others else ""} if k in ("entered", "completed") else k
                dirty()
        imgui.end_combo()
    if imgui.is_item_hovered():
        theme.tip("A hidden planet shows on the map once it opens. 'Completed' waits for a 'complete planet' "
                          "action in the Triggers window (for example at a world's final exit)")
    if isinstance(unlock, dict) and kind != "discovered":
        current = unlock[kind]
        if imgui.begin_combo("After planet", current or "(pick)"):
            for n in sorted(S.project.planets):
                if n != S.planet and imgui.selectable(n, n == current)[0]:
                    p["unlock"] = {kind: n}
                    dirty()
            imgui.end_combo()
