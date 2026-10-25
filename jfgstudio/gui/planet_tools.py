"""The Planet window (tasks S18, S25): a pack planet on the planet-select map, drawn over the map from the user's
catalog: a map point of its own (the port's planet registry, worldmap.py: a disc, and a route from a stock point in
a free stick direction), or a stock map point it takes over (`replaces`). Its entry level per hero and its name.
"""

import math

from imgui_bundle import hello_imgui, imgui

from jfgstudio import refs, worldmap
from jfgstudio.gui import theme

HEROES = ("juno", "vela", "lupus")
MAP_W, MAP_H = 420.0, 800.0  # the map's own coordinates (discs and route corners)


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
        S.planet = n
        S.planets_dirty = True
        S.validate()
    if names:
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(12))
        if imgui.begin_combo("##planet", S.planet or ""):
            for n in names:
                if imgui.selectable(n, n == S.planet)[0]:
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
    if doc is None:
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


def _draw_map(doc: dict, p: dict) -> None:
    S = _app().S
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    avail = imgui.get_content_region_avail()
    scale = min(avail.x / MAP_W, avail.y / MAP_H)
    ox = origin.x + (avail.x - MAP_W * scale) / 2

    def at(x, y):
        return imgui.ImVec2(ox + x * scale, origin.y + y * scale)

    pos = point_positions(doc)
    line = imgui.get_color_u32(theme.colour("BORDER"))
    for r in doc["routes"]:
        chain = ([pos[r["from"]]] if r["from"] in pos else []) + [tuple(c) for c in r.get("corners") or []] + (
            [pos[r["to"]]] if r["to"] in pos else [])
        for a, b in zip(chain, chain[1:], strict=False):
            draw.add_line(at(*a), at(*b), line, 2.0)
    own = worldmap.is_registry(p)
    for other in _app().S.project.planets.values():  # the project's own points
        if not worldmap.is_registry(other) or "disc" not in other:
            continue
        d = other["disc"]
        mine = other is p
        colour = imgui.get_color_u32(theme.colour("HIGHLIGHT" if mine else "MUTED"))
        for route in other.get("routes", []):
            try:
                point = refs.parse(route["from"], "mappoint").number()
                corners = route.get("corners") or worldmap.auto_corners(doc, point, route["dir"], d)
            except (refs.RefError, KeyError, IndexError, ValueError):
                continue
            for a, b in zip(corners, corners[1:], strict=False):
                draw.add_line(at(*a), at(*b), colour, 2.0)
        draw.add_circle_filled(at(d["x"], d["y"]), max(d["radius"] * scale, 5.0), colour)
        draw.add_text(imgui.ImVec2(at(d["x"], d["y"]).x + d["radius"] * scale + 3, at(d["x"], d["y"]).y - 8),
                      colour, other["displayName"].get("en", ""))
    taken = p.get("replaces")
    mouse = imgui.get_mouse_pos()
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
    if own and imgui.is_item_hovered():
        mx, my = (mouse.x - ox) / scale, (mouse.y - origin.y) / scale
        if hovered_point is not None:
            imgui.set_tooltip(f"map point {hovered_point}: "
                              f"{_world_label(doc, doc['points'][hovered_point]['world'])}\nclick: the route leaves "
                              f"from here (free: {', '.join(_free_dirs(doc, hovered_point)) or 'none'})")
        else:
            imgui.set_tooltip(f"({mx:.0f}, {my:.0f})\nclick: put the planet's disc here")
        if imgui.is_item_clicked():
            if hovered_point is not None:
                free = _free_dirs(doc, hovered_point)
                if free:
                    p["routes"] = [{"from": f"stock:mappoint/{hovered_point}", "dir": free[0],
                                    "back": worldmap.OPPOSITE[free[0]]}]
                else:
                    S.message = f"map point {hovered_point} has no free direction"
            else:
                p.setdefault("disc", {"index": 2, "radius": 14}).update({"x": round(mx), "y": round(my)})
            S.planets_dirty = True
            S.validate()
        return
    if imgui.is_item_hovered() and hovered_point is not None:
        imgui.set_tooltip(f"map point {hovered_point}: {_world_label(doc, doc['points'][hovered_point]['world'])}"
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
        imgui.set_tooltip("Shown on the map once the port's planet registry exists; the stock name shows until then")
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
        p.pop("routes", None)
        p.pop("disc", None)
        dirty()
    if own:
        _own_point_fields(p, doc, dirty)
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
            imgui.set_tooltip("The world number the planet's levels use (15 is free in the stock game)")
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
    if own:
        A._muted("The planet shows on the map once the stock point its route leaves from is unlocked.")
    else:
        A._muted("The planet unlocks the way the world it takes over does.")


def _free_dirs(doc: dict, point: int) -> list[str]:
    """Stick directions at a stock point that no stock route and no other project planet takes."""
    S = _app().S
    used = worldmap.taken_directions(doc, point)
    for other in S.project.planets.values():
        for r in other.get("routes", []):
            if r.get("from") == f"stock:mappoint/{point}" and other is not S.project.planets.get(S.planet):
                used.add(r["dir"])
    return [d for d in worldmap.DIRS if d not in used]


def _own_point_fields(p: dict, doc: dict | None, dirty) -> None:
    A = _app()
    A._muted("Click empty map space to place the disc, then a stock point for the route that reaches it.")
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
        imgui.set_tooltip("The stock disc whose textures it wears")
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
        A._muted("No route yet: click the stock map point it leaves from.")
        return
    route = routes[0]
    try:
        point = refs.parse(route["from"], "mappoint").number()
    except refs.RefError:
        A._muted(f"{route['from']}: not a stock map point")
        return
    label = _world_label(doc, doc["points"][point]["world"]) if doc else ""
    imgui.text(f"From map point {point} {label}")
    free = _free_dirs(doc, point) if doc else list(worldmap.DIRS)
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
