"""Level tools (task S17): the Level window (music, sky, fog, ambient loops, camera and effects), the Region tool
(click a polygon on the floor), the Water tool (drag a rectangle: a water quad in the pack's .glb) and the exit
destination picker in the Inspector.

Every change goes through the level's undo history; settings edits that drag (sliders, colours) merge into one step.
"""

import math

from imgui_bundle import imgui

from jfgstudio import edit, geometry, refs, squads
from jfgstudio import prefabs as pre
from jfgstudio.gui import theme
from jfgstudio.gui import viewport as vp

FOG_DEFAULT = {"fogNear": 983, "fogFar": 1016, "fog3": 983, "fogR": 160, "fogG": 170, "fogB": 200}
FOG_KEYS = ("fogNear", "fogFar", "fog3", "fogR", "fogG", "fogB")
CLOSE_PX = 12  # a click this near the first corner closes a region


def _app():
    from jfgstudio.gui import app
    return app


# ---------------------------------------------------------------- the Level window

def _stock_choices(key) -> list[tuple[object, str]]:
    """Values of a header setting stock levels use, each with the first level using it: [(value, label)]."""
    S = _app().S
    if S.catalog() is None:
        return []
    seen = {}
    for lv in S.cat.levels():
        v = key(lv)
        if v is not None and v not in seen:
            seen[v] = lv["name"]
    return [(v, f"{v}: {name}") for v, name in sorted(seen.items(), key=lambda kv: str(kv[0]))]


def _combo(label: str, current, choices: list, none_label: str | None = None):
    """A combo over (value, label) choices; returns the picked value, or the sentinel `_UNCHANGED`."""
    names = dict(choices)
    shown = none_label if current in (None, -1) and none_label else names.get(current, str(current))
    picked = _UNCHANGED
    if imgui.begin_combo(label, shown, imgui.ComboFlags_.height_large):
        if none_label and imgui.selectable(none_label, current in (None, -1))[0]:
            picked = -1
        for value, text in choices:
            if imgui.selectable(text, value == current)[0]:
                picked = value
        imgui.end_combo()
    return picked


_UNCHANGED = object()


def level_window() -> None:
    A = _app()
    S = A.S
    h = S.history()
    if h is None:
        A._muted("Open a project and pick a level.")
        return
    lv = h.level.level
    hd = lv["header"]

    def set_header(name, value, merge=False):
        h.set_value(edit.LEVEL, ("header", name), value, f"level {name}", merge=merge)
        S.validate()

    A._title(lv["name"])
    A._muted(f"track {lv['track']}")
    changed, name = imgui.input_text("Name", lv["name"], imgui.InputTextFlags_.enter_returns_true)
    if changed and name.strip():
        h.set_value(edit.LEVEL, ("name",), name.strip()[:31], "level name")
    world = hd.get("world", 15)
    changed, world = imgui.input_int("World", world)
    if changed:
        set_header("world", int(min(max(world, 0), 15)))
    if imgui.is_item_hovered():
        imgui.set_tooltip("The planet this level belongs to (0-15); a new planet is 15")
    if h.level.base is None:
        planets = [(n, p["displayName"]["en"]) for n, p in sorted(S.project.planets.items())]
        picked = _combo("Planet", lv.get("planet"), planets, "none (no region)")
        if picked is not _UNCHANGED:
            h.set_value(edit.LEVEL, ("planet",), None if picked == -1 else picked, "level planet")
            S.validate()
        if imgui.is_item_hovered():
            imgui.set_tooltip("The planet's region: leaving the planet by an exit shows the game's stats screen, and "
                              "entering it shows its name and the continues. None: no stats, no continues")
    tracks = [(f"pack:track/{f.stem}", f"{f.stem}  (geometry/{f.name})")
              for f in sorted((S.project.path / "geometry").glob("*.glb"))]
    if lv["track"] not in dict(tracks):
        tracks.append((lv["track"], lv["track"]))
    picked = _combo("Track", lv["track"], tracks)
    if picked is not _UNCHANGED and picked != lv["track"]:
        h.set_value(edit.LEVEL, ("track",), picked, "level track")
        S.validate()
    if imgui.is_item_hovered():
        imgui.set_tooltip("The level's geometry: a .glb in the project (File > Import geometry adds one)")

    if imgui.collapsing_header("Music and sound", imgui.TreeNodeFlags_.default_open):
        tunes = _stock_choices(lambda lv: lv["header"].get("tune"))
        packs = [(f"pack:music/{f.stem}", f"{f.stem}  (music/{f.name})")
                 for f in sorted((S.project.path / "music").glob("*.ogg"))]
        current = lv.get("music") if (lv.get("music") or "").startswith("pack:") else hd.get("tune")
        picked = _combo("Music", current, packs + tunes)
        if picked is not _UNCHANGED:
            pack = picked if isinstance(picked, str) else None
            changes = [edit.Change(edit.LEVEL, ("music",), lv.get("music"), pack)]
            if not isinstance(picked, str):
                # A tune plays as its stock levels set it: the channel mask and voice limit come with it.
                src = next(x for x in S.cat.levels() if x["header"].get("tune") == picked)["header"]
                changes += [edit.Change(edit.LEVEL, ("header", k), hd.get(k), src[k])
                            for k in ("tune", "tuneChannelMask", "voiceLimit") if hd.get(k) != src[k]]
            h.do(edit.Command("level music", [c for c in changes if c.old != c.new]))
            S.validate()
        if imgui.is_item_hovered():
            imgui.set_tooltip("A stock tune, or one of the pack's OGG files (music/), which the port streams in place "
                              "of the game's tune")
        if imgui.button("Add OGG music..."):
            add_music()
        ids = sorted({a for x in (S.cat.levels() if S.cat else []) for a in x["header"].get("ambientSounds", [])
                      if a != -1})
        names = {}
        for x in S.cat.levels() if S.cat else []:
            for a in x["header"].get("ambientSounds", []):
                names.setdefault(a, x["name"])
        loops = [(a, f"{a}: {names[a]}") for a in ids]
        ambient = (list(lv.get("ambient", [])) + [-1, -1, -1])[:3]
        for k in range(3):
            picked = _combo(f"Ambient loop {k + 1}", ambient[k], loops, "none")
            if picked is not _UNCHANGED:
                new = list(ambient)
                new[k] = picked
                h.set_value(edit.LEVEL, ("ambient",), new if any(v != -1 for v in new) else None, "ambient loop")

    if imgui.collapsing_header("Sky and fog", imgui.TreeNodeFlags_.default_open):
        skies = _stock_choices(lambda lv: lv["sky"] if lv["sky"] != -1 else None)
        picked = _combo("Sky", lv.get("sky", -1), skies, "none (clear colour)")
        if picked is not _UNCHANGED:
            h.set_value(edit.LEVEL, ("sky",), picked, "level sky")
        clear = [hd.get(k, 0) / 255 for k in ("clearR", "clearG", "clearB")]
        changed, clear = imgui.color_edit3("Clear colour", clear)
        if changed:
            for k, v in zip(("clearR", "clearG", "clearB"), clear, strict=True):
                set_header(k, round(v * 255), merge=True)
        fog_on = any(hd.get(k, 0) for k in FOG_KEYS)
        changed, fog_on = imgui.checkbox("Fog", fog_on)
        if changed:
            new = FOG_DEFAULT if fog_on else dict.fromkeys(FOG_KEYS, 0)
            h.do(edit.Command("level fog", [edit.Change(edit.LEVEL, ("header", k), hd.get(k), v)
                                            for k, v in new.items() if hd.get(k) != v]))
            S.validate()
        if fog_on:
            col = [hd.get(k, 0) / 255 for k in ("fogR", "fogG", "fogB")]
            changed, col = imgui.color_edit3("Fog colour", col)
            if changed:
                for k, v in zip(("fogR", "fogG", "fogB"), col, strict=True):
                    set_header(k, round(v * 255), merge=True)
            for k, label, tip in (("fogNear", "Fog start", "where the fog begins (900-1023: the game's depth units)"),
                                  ("fogFar", "Fog end", "where the fog is full"),
                                  ("fog3", "Fog 3", "the third fog value; stock levels use the start value")):
                changed, v = imgui.slider_int(label, hd.get(k, 0), 900, 1023)
                if changed:
                    set_header(k, v, merge=True)
                if imgui.is_item_hovered():
                    imgui.set_tooltip(tip)

    if imgui.collapsing_header("Camera and effects"):
        for k, label, lo, hi, tip in (
                ("fov", "Field of view", 20, 90, "degrees"),
                ("lights", "Light slots", 0, 64, "rgbalights and object lights the level can have (stock 24)"),
                ("weather", "Weather", -1, 16, "weather kind (0 = none)"),
                ("dayLength", "Day length", 0, 255, "day/night cycle length, 0 = none"),
                ("underWaterLights", "Underwater lights", 0, 255, "lights under water"),
                ("regionIndex", "Region index", 0, 255, "saved-flag region index (0 = none)")):
            changed, v = imgui.slider_int(label, hd.get(k, 0), lo, hi)
            if changed:
                set_header(k, v, merge=True)
            if imgui.is_item_hovered():
                imgui.set_tooltip(tip)
        for k, label in (("nightVision", "Night vision"), ("refract", "Refraction"), ("blur", "Blur")):
            changed, v = imgui.checkbox(label, bool(hd.get(k, 0)))
            if changed:
                set_header(k, int(v))


def add_music() -> None:
    """Copies an OGG Vorbis file into the project's music/ folder (loop points: LOOPSTART / LOOPLENGTH comments)."""
    import shutil

    from imgui_bundle import portable_file_dialogs as pfd
    S = _app().S
    picked = (pfd.open_file("Music (OGG Vorbis)", "", ["OGG Vorbis", "*.ogg"]).result() or [None])[0]
    if not picked:
        return
    from pathlib import Path
    src = Path(picked)
    if src.read_bytes()[:4] != b"OggS":
        S.message = f"{src.name} is not an OGG file"
        return
    name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in src.stem.lower()) or "music"
    dest = S.project.music_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    S.message = f"music/{dest.name} added: pick it as a level's Music"


# ---------------------------------------------------------------- Region and Water tools in the Scene

def scene_tool(origin, w: int, h: int, hovered: bool) -> None:
    """Runs the Region or Water tool on the Scene image (called instead of picking and the gizmo)."""
    S = _app().S
    io = imgui.get_io()
    mouse = imgui.get_mouse_pos()
    mx, my = mouse.x - origin.x, mouse.y - origin.y
    draw = imgui.get_window_draw_list()
    hit = ground_hit(mx, my, (w, h)) if hovered else None
    colour = imgui.get_color_u32(theme.colour("HIGHLIGHT"))

    def screen(p):
        q = vp.project(S.camera, (w, h), p)
        return imgui.ImVec2(origin.x + q[0], origin.y + q[1]) if q else None

    if S.tool == "region":
        pts = S.region_draft
        note = (f"{'Hole' if S.region_hole else 'Region'}: {len(pts)} corners. Click to add, Enter or click the "
                "first corner to finish, Backspace removes the last, Esc cancels")
        if S.region_hole:
            colour = imgui.get_color_u32(imgui.ImVec4(*vp.HOLE_COLOUR))
        if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and hit is not None and not io.key_alt:
            first = screen(pts[0]) if pts else None
            if len(pts) >= 3 and first and math.hypot(first.x - mouse.x, first.y - mouse.y) < CLOSE_PX:
                finish_region()
            else:
                pts.append([float(v) for v in hit])
        if imgui.is_key_pressed(imgui.Key.enter, False) and len(pts) >= 3:
            finish_region()
        if imgui.is_key_pressed(imgui.Key.backspace, False) and pts:
            pts.pop()
        if imgui.is_key_pressed(imgui.Key.escape, False):
            pts.clear()
        chain = [screen(p) for p in pts] + ([imgui.ImVec2(mouse.x, mouse.y)] if hovered and pts else [])
        for a, b in zip(chain, chain[1:], strict=False):
            if a and b:
                draw.add_line(a, b, colour, 2.0)
        for p in chain[:len(pts)]:
            if p:
                draw.add_circle_filled(p, 4.0, colour)
    elif S.tool == "patrol":
        note = ("Patrol: click the floor to add a point linked to the last one, click a point to link to it; Esc "
                "starts a new chain")
        objs = S.objects()
        points = [o for o in objs if (sc := S.schema(o)) and sc.key == "patrolpoint"]
        near = None
        for o in points:
            q = screen(o["pos"])
            if q and math.hypot(q.x - mouse.x, q.y - mouse.y) < CLOSE_PX:
                near = o
        last = next((o for o in points if o["uid"] == S.patrol_last), None)
        if last is not None and hovered:
            a = screen(last["pos"])
            if a:
                draw.add_line(a, imgui.ImVec2(mouse.x, mouse.y), colour, 2.0)
        if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and not io.key_alt:
            if near is not None:
                if last is not None and near["uid"] != last["uid"]:
                    patrol_link(last, near)
                S.patrol_last = near["uid"]
            elif hit is not None:
                patrol_point(hit, last)
        if imgui.is_key_pressed(imgui.Key.escape, False):
            S.patrol_last = None
    else:
        note = "Water: drag a rectangle on the floor; the surface goes at the height in the toolbar"
        if not _pack_glb():
            note = "Water: this level uses a stock track; water planes go into the pack's own geometry"
        elif hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and hit is not None and not io.key_alt:
            S.water_draft = [float(v) for v in hit]
            if S.water_auto:
                S.water_height = round(hit[1] + 15)
        if S.water_draft is not None:
            a = S.water_draft
            b = hit if hit is not None else a
            y = S.water_height
            corners = [(a[0], y, a[2]), (b[0], y, a[2]), (b[0], y, b[2]), (a[0], y, b[2])]
            sc = [screen(c) for c in corners]
            for p, q in zip(sc, sc[1:] + sc[:1], strict=True):
                if p and q:
                    draw.add_line(p, q, imgui.get_color_u32(theme.colour("ACCENT")), 2.0)
            if not imgui.is_mouse_down(imgui.MouseButton_.left):
                S.water_draft = None
                add_water_plane(a[0], a[2], b[0], b[2], y)
    draw.add_text(imgui.ImVec2(origin.x + 10, origin.y + 8), colour, note)


def ground_hit(mx: float, my: float, size):
    """The point on the level under a pixel of the Scene, or None."""
    S = _app().S
    mesh = S.view.mesh
    if mesh is None:
        return None
    ray_o, ray_d = vp.screen_ray(S.camera, size, mx, my)
    t = vp.raycast(mesh.collision, ray_o, ray_d)
    return None if t is None else ray_o + ray_d * t


def _patrol_examples():
    S = _app().S
    if S.catalog() is None:
        return None, None
    p = (S.objtypes.get(pre.PATROL_POINT_ID, {}).get("examples") or [None])[0]
    link = (S.objtypes.get(pre.PATROL_PATH_ID, {}).get("examples") or [None])[0]
    return p, link


def patrol_point(pos, last) -> None:
    """A patrol point on the floor with the next node number, linked both ways to the last one placed."""
    S = _app().S
    h = S.history()
    point_ex, link_ex = _patrol_examples()
    if h is None or point_ex is None or link_ex is None:
        S.message = "Patrol: the catalog has no stock patrol point to start from"
        return
    placer = pre.Placer(h.level, S.schemas, S.behaviours)
    o = placer.patrol_point(pos, point_ex)
    if last is not None:
        placer.patrol_link(last, o, link_ex)
    h.add(placer.made, "patrol point")
    S.patrol_last = o["uid"]
    S.selection = {o["uid"]}
    S.validate()


def patrol_link(a, b) -> None:
    S = _app().S
    h = S.history()
    _, link_ex = _patrol_examples()
    if h is None or link_ex is None:
        return
    placer = pre.Placer(h.level, S.schemas, S.behaviours)
    if placer.patrol_link(a, b, link_ex) is not None:
        h.add(placer.made, "patrol link")
        S.validate()


def finish_region() -> None:
    A = _app()
    S = A.S
    h = S.history()
    pts = S.region_draft
    if h is None or len(pts) < 3:
        return
    placer = pre.Placer(h.level, S.schemas, S.behaviours)
    if S.region_hole:
        cx, cz = sum(p[0] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts)
        area = placer.area_at(cx, cz)
        if area is None:
            S.message = "Hole: draw it inside a navigation region (the area it is cut from)"
            return
        n = placer.hole(pts, area)
        message = f"Hole {n} in region {area}: {len(pts)} corners (squads take cover at its corners)"
    else:
        n = placer.polygon(pts)
        message = f"Region {n}: {len(pts)} corners (give its id to a squadron)"
    h.add(placer.made, f"{'hole' if S.region_hole else 'region'} {n}")
    S.selection = {o["uid"] for o in placer.made}
    S.region_draft = []
    S.tool = "move"
    S.message = message
    S.validate()


def add_cover(squadron: dict) -> None:
    """The Inspector's Add cover: the obstacles inside a squadron's region made holes of it (one undo step)."""
    S = _app().S
    h = S.history()
    if h is None or S.view.mesh is None:
        return
    placer = pre.Placer(h.level, S.schemas, S.behaviours)
    try:
        got = placer.cover(S.view.mesh, squadron)
    except ValueError as e:
        S.message = f"Add cover: {e}"
        return
    if placer.made:
        h.add(placer.made, "cover")
        S.selection = {o["uid"] for o in placer.made}
        S.message = f"{got['holes']} hole(s) added to region {got['region']} for cover"
    elif got["old"]:
        S.message = f"Region {got['region']} has its holes already ({got['old']})"
    else:
        S.message = (f"Region {got['region']}: no obstacle inside it"
                     + (f" ({got['edge']} touch its edge: cut the outline round them)" if got["edge"] else "")
                     + ". Put a block or crate in it, draw a hole with the Region tool's Hole mode, or give the "
                     "region an inward notch")
    S.validate()


def make_patrol(squadron: dict) -> None:
    """The Inspector's Patrol: the squadron put in patrol mode as the agent's place_patrol does (one undo step)."""
    S = _app().S
    h = S.history()
    if h is None:
        return
    if S.squads is None:
        S.squads = squads.index(S.cat) if S.catalog() is not None else {}

    def key(o):
        sc = S.schema(o)
        return sc.key if sc else None

    h.do(edit.Command("patrol", squads.patrol(h.level, squadron, S.squads, key, S.cat)))
    S.message = "Squadron set to patrol: it walks the patrol points nearest to it"
    if not squads.near_patrol(h.level, squadron, key):
        S.message += (f". No patrol point within {squads.PATROL_NEAR} of it: it never finds the route. Place one "
                      "next to it with the Patrol tool (P)")
    S.validate()


def _pack_glb():
    S = _app().S
    level = S.project.levels[S.level]
    r = refs.parse(level.level["track"], "track")
    return S.project.geometry_path(r.name) if r.is_pack else None


def add_water_plane(x0, z0, x1, z1, y) -> None:
    S = _app().S
    path = _pack_glb()
    h = S.history()
    if path is None or h is None or not path.exists():
        return
    if abs(x1 - x0) < 10 or abs(z1 - z0) < 10:
        S.message = "Water: drag a bigger rectangle"
        return
    try:
        data = geometry.add_water(path.read_bytes(), x0, z0, x1, z1, y)
    except geometry.GeometryError as e:
        S.message = f"Water: {e}"
        return
    h.write_file(path, data, "water plane")
    S.message = f"Water plane {abs(x1 - x0):.0f} x {abs(z1 - z0):.0f} at height {y:.0f} (Ctrl+Z removes it)"


# ---------------------------------------------------------------- the exit destination picker

def level_choices() -> list[tuple[str, str]]:
    """Every level an exit can lead to: the pack's, then the stock ones from the catalog."""
    S = _app().S
    out = [(f"pack:level/{name}", f"{lv.level['name']}  (pack: {name})") for name, lv in S.project.levels.items()]
    if S.catalog() is not None:
        out += [(f"stock:level/{x['id']}", f"{x['id']}: {x['name']}") for x in S.cat.levels()]
    return out


def start_points_of(target: str) -> list[int]:
    S = _app().S
    try:
        r = refs.parse(target, "level")
    except ValueError:
        return []
    if r.is_pack:
        lv = S.project.levels.get(r.name)
        if lv is None:
            return []
        return sorted({o.get("fields", {}).get("startPoint", 0) for o in lv.objects["objects"]
                       if (sc := S.schema(o)) and sc.key == "startpoint"})
    return S.cat.start_points(r.number()) if S.catalog() is not None else []


def exit_picker(o: dict) -> None:
    A = _app()
    S = A.S
    h = S.history()
    uid = o["uid"]
    choices = level_choices()
    names = dict(choices)
    current = o.get("target", "")
    if imgui.begin_combo("Leads to", names.get(current, current or "(pick a level)"), imgui.ComboFlags_.height_large):
        if imgui.is_window_appearing():
            imgui.set_keyboard_focus_here()
            S.exit_filter = ""
        _, S.exit_filter = imgui.input_text_with_hint("##filter", "Search levels", S.exit_filter)
        for value, text in choices:
            if S.exit_filter and S.exit_filter.lower() not in text.lower():
                continue
            if imgui.selectable(text, value == current)[0]:
                h.set_value(uid, ("target",), value, "exit target")
                S.validate()
        imgui.end_combo()
    if not current:
        return
    points = start_points_of(current)
    sp = S.fields(o, S.schema(o)).get("startPoint", 0)
    if points:
        if imgui.begin_combo("Start point", f"start point {sp}" + ("" if sp in points else " (not in that level)")):
            for n in points:
                if imgui.selectable(f"start point {n}", n == sp)[0]:
                    h.set_value(uid, ("fields", "startPoint"), n, "exit start point")
                    S.validate()
            imgui.end_combo()
    else:
        A._muted("That level has no start points yet.")
    r = refs.parse(current, "level")
    if r.is_pack and r.name != S.level and imgui.button("Create return exit"):
        create_return_exit(o, r.name)
    if r.is_pack and imgui.is_item_hovered():
        imgui.set_tooltip("Adds an exit in the target level that leads back here, and a start point next to this exit "
                          "to arrive at")


def create_return_exit(o: dict, target_name: str) -> None:
    """An exit in the target level (beside the start point this exit uses) that leads back to a new start point in
    this level, placed just in front of this exit."""
    S = _app().S
    here = S.history()
    there = S.history_for(target_name)
    sp = S.fields(o, S.schema(o)).get("startPoint", 0)
    arrive = next((t for t in there.level.objects["objects"] if (sc := S.schema(t)) and sc.key == "startpoint"
                   and t.get("fields", {}).get("startPoint", 0) == sp), None)
    if arrive is None:
        S.message = f"{target_name} has no start point {sp} to put the return exit beside"
        return
    placer = pre.Placer(here.level, S.schemas, S.behaviours)
    number = placer.free("startpoint", "startPoint", start=0)
    x, y, z = o["pos"]
    placer.single(pre.START_ID, (x, y, z - 150), name=f"start{number}",
                  fields={"startPoint": number, "player": 0, "yaw": 32})
    here.add(placer.made, "return start point")
    back = pre.Placer(there.level, S.schemas, S.behaviours)
    ax, ay, az = arrive["pos"]
    ex = back.single(pre.EXIT_ID, (ax, ay, az - 200), name=f"exit to {S.project.levels[S.level].level['name']}",
                     fields={"radius": 60, "startPoint": number})
    ex["target"] = f"pack:level/{S.level}"
    there.add(back.made, "return exit")
    S.message = f"Return exit added in {target_name}, arriving at start point {number} here"
    S.validate()

