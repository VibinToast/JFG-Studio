"""The Planet window (task S18): which map point a pack planet takes over, its entry level per hero, its name and
world, drawn over the planet map from the user's catalog.

Phase 1 rule: in the port the map can only be edited in place, so a planet takes over an existing map point
(`replaces`). Names and new-game unlocks wait for the planet registry (P4); the window says so.
"""

import math

from imgui_bundle import hello_imgui, imgui

from jfgstudio import refs
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


def point_positions(doc: dict) -> dict[int, tuple[float, float]]:
    """Map point -> (x, y): discs from their icons, the other points (ships, stations) from the route corners
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
        planets[n] = {"format": 1, "displayName": {"en": n.title()}, "fixedWorld": 15,
                      "entryLevel": dict.fromkeys(HEROES, entry), "unlock": {"newGame": ["juno"]}}
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
    taken = p.get("replaces")
    if taken and doc:
        k = refs.parse(taken, "mappoint").number()
        A._muted(f"Takes over map point {k} ({_world_label(doc, doc['points'][k]['world'])}). Click another point "
                 "on the map to move it.")
    else:
        A._muted("Click a map point to take it over (a new point needs the planet registry).")
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
    imgui.separator_text("Unlock")
    unlock = p.setdefault("unlock", {}).setdefault("newGame", [])
    for hero in HEROES:
        changed, on = imgui.checkbox(f"{hero.title()} at a new game", hero in unlock)
        if changed:
            if on:
                unlock.append(hero)
            else:
                unlock.remove(hero)
            dirty()
    A._muted("New-game unlocks work once the port's planet registry exists; until then the planet unlocks the way "
             "the world it takes over does.")
