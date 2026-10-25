"""The editor window: a docked, Unity-style layout (design doc section 6) in the JFG theme.

Hierarchy, Scene (3D: gui/viewport.py), Inspector, Project, Prefabs, Console and Level Graph windows, the toolbar and
the menus. Edits go through jfgstudio.edit's per-level undo history.
"""

import math
import struct
import zlib
from pathlib import Path

import numpy as np
from imgui_bundle import hello_imgui, imgui, imguizmo
from imgui_bundle import portable_file_dialogs as pfd

from jfgstudio import __version__, edit, refs
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val
from jfgstudio.gui import theme
from jfgstudio.gui import viewport as vp

gz = imguizmo.im_guizmo

TOOLS = (("hand", "Q", "Hand: pan and orbit the view"), ("move", "W", "Move"), ("rotate", "E", "Rotate"),
         ("scale", "R", "Scale"))


class State:
    def __init__(self):
        self.project: proj.Project | None = None
        self.level: str | None = None
        self.selection: set[str] = set()
        self.problems: list[val.Problem] = []
        self.load_errors: list[str] = []
        self.schemas = obj.load_schemas()
        self.tool = "move"
        self.local = False
        self.snap = True
        self.grid = 10.0
        self.new_project = {"open": False, "folder": "", "id": "", "name": ""}
        self.message = ""
        self.histories: dict[str, edit.History] = {}
        self.view = vp.Viewport()
        self.camera = vp.Camera()
        self.framed_level = None
        self.snap_floor = True
        self.drag = None  # uid -> start position, while the gizmo moves the selection
        self.scene_rect = (0.0, 0.0, 1.0, 1.0)
        self.pos_edit = None  # the Inspector's position before a drag of its fields
        self.settings_open = False
        self.settings_edit = {}

    # project actions
    def open(self, path: str) -> None:
        try:
            self.project = proj.load(path)
        except proj.ProjectError as e:
            self.load_errors = e.problems
            self.message = f"Could not open {path}: {len(e.problems)} problems (see Console)"
            return
        self.load_errors = []
        self.level = next(iter(self.project.levels), None)
        self.selection.clear()
        self.validate()
        self.message = f"Opened {self.project.meta['name']}"

    def validate(self) -> None:
        self.problems = val.validate(self.project, self.schemas) if self.project else []

    def save(self) -> None:
        if self.project:
            self.project.save()
            for h in self.histories.values():
                h.dirty = False
            self.message = "Saved"

    def history(self) -> edit.History | None:
        if not self.project or self.level not in self.project.levels:
            return None
        if self.level not in self.histories:
            self.histories[self.level] = edit.History(self.project.levels[self.level])
        return self.histories[self.level]

    def undo(self) -> None:
        h = self.history()
        label = h.undo() if h else None
        self.message = f"Undo {label}" if label else "Nothing to undo"
        self.validate()

    def redo(self) -> None:
        h = self.history()
        label = h.redo() if h else None
        self.message = f"Redo {label}" if label else "Nothing to redo"
        self.validate()

    def dirty(self) -> bool:
        return any(h.dirty for h in self.histories.values())

    def track_path(self) -> str | None:
        """The .glb the Scene shows for the open level: the project's geometry, or a stock track from the catalog."""
        level = self.project.levels[self.level]
        r = refs.parse(level.level["track"], "track")
        if r.is_pack:
            path = self.project.geometry_path(r.name)
            return str(path) if path.is_file() else None
        from jfgstudio.catalog import Catalog
        return str(Catalog.open().track_glb(r.number()))

    def objects(self) -> list[dict]:
        if not self.project or self.level not in self.project.levels:
            return []
        return self.project.levels[self.level].objects["objects"]


S = State()


# ---------------------------------------------------------------- windows

def _title(text: str) -> None:
    if theme.TITLE_FONT:
        imgui.push_font(theme.TITLE_FONT, 0.0)
        imgui.text_colored(theme.colour("ACCENT"), text)
        imgui.pop_font()
    else:
        imgui.text_colored(theme.colour("ACCENT"), text)


def _object_label(o: dict) -> str:
    schema = obj.schema_for(o, S.schemas)
    kind = schema.name if schema else o["type"].split("/", 1)[-1]
    return f"{o.get('name') or kind}  ##{o['uid']}"


def _select(uid: str) -> None:
    if imgui.get_io().key_ctrl:
        S.selection ^= {uid}
    else:
        S.selection = {uid}


def hierarchy() -> None:
    objs = S.objects()
    if not S.project:
        imgui.text_disabled("No project open.\nFile > Open project...")
        return
    if not objs:
        imgui.text_disabled("This level has no objects.")
        return
    keys = {o["uid"]: (obj.schema_for(o, S.schemas).key if obj.schema_for(o, S.schemas) else None) for o in objs}
    squads = {o.get("fields", {}).get("squadronId"): o for o in objs if keys[o["uid"]] == "squadron"}
    members = {}
    for o in objs:
        if keys[o["uid"]] == "squadmember":
            members.setdefault(o.get("fields", {}).get("squadron"), []).append(o)
    regions = [o for o in objs if keys[o["uid"]] == "regionpoint"]
    shown = set()
    for sid, squad in squads.items():
        flags = imgui.TreeNodeFlags_.open_on_arrow | imgui.TreeNodeFlags_.default_open
        if squad["uid"] in S.selection:
            flags |= imgui.TreeNodeFlags_.selected
        is_open = imgui.tree_node_ex(_object_label(squad), flags)
        if imgui.is_item_clicked():
            _select(squad["uid"])
        shown.add(squad["uid"])
        if is_open:
            for m in members.get(sid, []):
                _leaf(m)
                shown.add(m["uid"])
            imgui.tree_pop()
    if regions:
        if imgui.tree_node_ex(f"Navigation regions ({len({r['fields'].get('region') for r in regions})})##regions"):
            for r in regions:
                _leaf(r)
            imgui.tree_pop()
        shown |= {r["uid"] for r in regions}
    for o in objs:
        if o["uid"] not in shown:
            _leaf(o)


def _leaf(o: dict) -> None:
    flags = imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
    if o["uid"] in S.selection:
        flags |= imgui.TreeNodeFlags_.selected
    imgui.tree_node_ex(_object_label(o), flags)
    if imgui.is_item_clicked():
        _select(o["uid"])


def _keys(objs) -> dict:
    return {o["uid"]: (sc.key if (sc := obj.schema_for(o, S.schemas)) else None) for o in objs}


def scene() -> None:
    if not S.project or not S.level:
        imgui.text_disabled("Open a project and pick a level in the Project window.")
        return
    level = S.project.levels[S.level]
    try:
        S.view.load_track(S.track_path())
    except Exception as e:  # no catalog yet, no ROM set: say so in the Scene
        S.view.load_track(None)
        S.view.error = f"stock track: {e}"
    S.view.fog = vp.decode_fog(level.level["header"])
    if S.framed_level != S.level and S.view.mesh is not None:
        S.camera.look_from_above(S.view.mesh.bounds)
        S.framed_level = S.level
    _, S.view.show_fog = imgui.checkbox("Fog", S.view.show_fog)
    imgui.same_line()
    _, S.view.show_hidden = imgui.checkbox("Invisible colliders", S.view.show_hidden)
    imgui.same_line()
    imgui.text_disabled(f"   {level.level['name']}  ({level.level['track']})   speed {S.camera.speed:.0f}")
    if S.view.error:
        imgui.text_colored(theme.colour("ERROR"), S.view.error)
    avail = imgui.get_content_region_avail()
    w, h = int(max(avail.x, 8)), int(max(avail.y, 8))
    objs = S.objects()
    keys = _keys(objs)
    lines = vp.marker_lines(objs, keys, S.selection, vp.region_links(objs, keys))
    tex = S.view.render(w, h, S.camera, lines)
    origin = imgui.get_cursor_screen_pos()
    S.scene_rect = (origin.x, origin.y, w, h)
    if tex is None:
        return
    imgui.image(imgui.ImTextureRef(tex), imgui.ImVec2(w, h), imgui.ImVec2(0, 1), imgui.ImVec2(1, 0))
    hovered = imgui.is_item_hovered()
    draw = imgui.get_window_draw_list()
    for o in objs:
        p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + vp.MARKER_HEIGHT + 6, o["pos"][2]))
        if p and keys.get(o["uid"]) != "regionpoint":
            colour = theme.colour("HIGHLIGHT" if o["uid"] in S.selection else "MUTED")
            draw.add_text(imgui.ImVec2(origin.x + p[0] + 4, origin.y + p[1] - 14), imgui.get_color_u32(colour),
                          o.get("name") or o["uid"])
    using = _gizmo(origin, w, h, objs)
    _camera_input(hovered, objs)
    if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and not using and not gz.is_over() \
            and not imgui.get_io().key_alt:
        _pick(origin, w, h, objs, keys)


def _pick(origin, w, h, objs, keys) -> None:
    mouse = imgui.get_mouse_pos()
    mx, my = mouse.x - origin.x, mouse.y - origin.y
    best, best_d = None, 14.0 ** 2
    for o in objs:
        for lift in (vp.MARKER_HEIGHT, 0.0):
            p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + lift, o["pos"][2]))
            if p:
                d = (p[0] - mx) ** 2 + (p[1] - my) ** 2
                if d < best_d:
                    best, best_d = o["uid"], d
    if best is None:
        if not (imgui.get_io().key_ctrl or imgui.get_io().key_shift):
            S.selection = set()
    else:
        _select(best)


def _yaw_field(o: dict):
    """The schema field that holds an object's facing, and radians per unit, if it has one."""
    schema = obj.schema_for(o, S.schemas)
    f = schema.field("yaw") if schema else None
    if f is None or f.unit not in ("angle10", "angle8"):
        return None, 0.0
    return f, (2 * math.pi * (1024 if f.unit == "angle10" else 256) / 65536)


def _gizmo(origin, w, h, objs) -> bool:
    sel = [o for o in objs if o["uid"] in S.selection]
    if not sel or S.tool not in ("move", "rotate"):
        if S.drag is not None:
            _end_drag(objs)
        return False
    gz.set_drawlist()
    gz.set_rect(origin.x, origin.y, w, h)
    view = gz.Matrix16(vp.pack_matrix(S.camera.view()))
    proj_m = gz.Matrix16(vp.pack_matrix(S.camera.projection(w / h)))
    centre = np.mean([o["pos"] for o in sel], axis=0)
    yaw_f, per_unit = _yaw_field(sel[0]) if len(sel) == 1 else (None, 0.0)
    yaw = sel[0].get("fields", {}).get("yaw", 0) * per_unit if yaw_f else 0.0
    m = gz.Matrix16(vp.pack_matrix(vp.object_matrix(centre, yaw)))
    if S.tool == "move":
        op = gz.OPERATION.translate
        snap = gz.Matrix3([S.grid, S.grid, S.grid]) if S.snap else None
    else:
        if yaw_f is None:
            return False
        op = gz.OPERATION.rotate_y
        snap = gz.Matrix3([360.0 / 64, 0, 0]) if S.snap else None
    mode = gz.MODE.local if S.local else gz.MODE.world
    changed = gz.manipulate(view, proj_m, op, mode, m, None, snap)
    using = gz.is_using()
    if using and S.drag is None:
        S.drag = {o["uid"]: (list(o["pos"]), dict(o.get("fields", {}))) for o in sel}
    if changed:
        new = vp.unpack_matrix(m.values)
        if S.tool == "move":
            delta = new[:3, 3] - centre
            for o in sel:
                p = [float(o["pos"][k] + delta[k]) for k in range(3)]
                if S.snap_floor and S.view.mesh is not None:
                    y = S.view.mesh.floor_height(p[0], p[2], below=p[1] + 60)
                    if y is not None:
                        p[1] = y
                o["pos"] = [round(v) for v in p]
        else:
            angle = math.atan2(new[0, 2], new[0, 0])
            units = round(angle / per_unit) % int(round(2 * math.pi / per_unit))
            sel[0].setdefault("fields", {})["yaw"] = units
    if not using and S.drag is not None:
        _end_drag(objs)
    return using


def _end_drag(objs) -> None:
    h = S.history()
    start = S.drag or {}
    S.drag = None
    if not h:
        return
    changes = []
    for o in objs:
        if o["uid"] in start:
            old_pos, old_fields = start[o["uid"]]
            if list(o["pos"]) != old_pos:
                changes.append(edit.Change(o["uid"], ("pos",), old_pos, list(o["pos"])))
            if o.get("fields", {}).get("yaw") != old_fields.get("yaw"):
                changes.append(edit.Change(o["uid"], ("fields", "yaw"), old_fields.get("yaw"),
                                           o.get("fields", {}).get("yaw")))
    h.record(edit.Command("move" if S.tool == "move" else "rotate", changes))
    S.validate()


def _camera_input(hovered: bool, objs) -> None:
    io = imgui.get_io()
    cam = S.camera
    dt = max(io.delta_time, 1e-3)
    d = io.mouse_delta
    if hovered and io.mouse_wheel:
        cam.speed = float(np.clip(cam.speed * (1.25 if io.mouse_wheel > 0 else 0.8), 50, 20000))
    if imgui.is_mouse_down(imgui.MouseButton_.right) and (hovered or imgui.is_mouse_dragging(imgui.MouseButton_.right)):
        cam.yaw -= d.x * 0.005
        cam.pitch = float(np.clip(cam.pitch - d.y * 0.005, -1.5, 1.5))
        move = np.zeros(3)
        for key, vec in ((imgui.Key.w, cam.forward()), (imgui.Key.s, -cam.forward()), (imgui.Key.d, cam.right()),
                         (imgui.Key.a, -cam.right()), (imgui.Key.e, np.array([0, 1.0, 0])),
                         (imgui.Key.q, np.array([0, -1.0, 0]))):
            if imgui.is_key_down(key):
                move += vec
        cam.pos = cam.pos + move * cam.speed * dt * (3.0 if io.key_shift else 1.0)
    elif imgui.is_mouse_down(imgui.MouseButton_.middle) and (hovered or imgui.is_mouse_dragging(
            imgui.MouseButton_.middle)):
        up = np.cross(cam.right(), cam.forward())  # the screen's up
        cam.pos = cam.pos + (-cam.right() * d.x + up * d.y) * cam.speed * 0.002  # the scene follows the mouse
    elif io.key_alt and imgui.is_mouse_down(imgui.MouseButton_.left) and hovered:
        sel = [o["pos"] for o in objs if o["uid"] in S.selection]
        pivot = np.mean(sel, axis=0) if sel else cam.pos + cam.forward() * 600
        dist = float(np.linalg.norm(cam.pos - pivot)) or 600.0
        cam.yaw -= d.x * 0.005
        cam.pitch = float(np.clip(cam.pitch - d.y * 0.005, -1.5, 1.5))
        cam.frame(pivot, dist)


def frame_selection() -> None:
    sel = [o["pos"] for o in S.objects() if o["uid"] in S.selection]
    if sel:
        S.camera.frame(np.mean(sel, axis=0), 500.0)
    elif S.view.mesh is not None:
        S.camera.look_from_above(S.view.mesh.bounds)


def inspector() -> None:
    sel = [o for o in S.objects() if o["uid"] in S.selection]
    h = S.history()
    if not sel or h is None:
        imgui.text_disabled("Select an object in the Hierarchy or the Scene.")
        return
    if len(sel) > 1:
        imgui.text(f"{len(sel)} objects selected")
        imgui.text_disabled("W moves them together; Delete removes them; Ctrl+D duplicates them.")
        return
    o = sel[0]
    uid = o["uid"]
    schema = obj.schema_for(o, S.schemas)
    _title(o.get("name") or (schema.name if schema else "Object"))
    imgui.text_disabled(f"{o['type']}   uid {uid}")
    changed, name = imgui.input_text("Name", o.get("name", ""), imgui.InputTextFlags_.enter_returns_true)
    if changed:
        h.set_value(uid, ("name",), name or None, "rename")
    if imgui.collapsing_header("Transform", imgui.TreeNodeFlags_.default_open):
        pos = [float(v) for v in o["pos"]]
        changed, new = imgui.drag_float3("Position", pos, 1.0, -32768, 32767, "%.0f")
        if imgui.is_item_activated():
            S.pos_edit = list(o["pos"])
        if changed:
            o["pos"] = [round(v) for v in new]
        if imgui.is_item_deactivated_after_edit() and S.pos_edit is not None:
            h.move({uid: (S.pos_edit, list(o["pos"]))})
            S.pos_edit = None
            S.validate()
        if S.view.mesh is not None and imgui.button("Drop to floor"):
            y = S.view.mesh.floor_height(o["pos"][0], o["pos"][2], below=o["pos"][1] + 60)
            if y is not None:
                h.set_value(uid, ("pos",), [o["pos"][0], round(y), o["pos"][2]], "drop to floor")
    if schema and imgui.collapsing_header(schema.name, imgui.TreeNodeFlags_.default_open):
        if schema.status != "decoded":
            imgui.text_colored(theme.colour("HIGHLIGHT"), "Partly decoded: other fields come from the stock entry.")
        for f in schema.fields:
            value = o.get("fields", {}).get(f.name, f.default)
            lo, hi = obj._RANGES[f.type]
            changed, new = imgui.input_int(f.name, int(value), 1, 10, imgui.InputTextFlags_.enter_returns_true)
            if changed:
                h.set_value(uid, ("fields", f.name), int(min(max(new, lo), hi)), f"set {f.name}")
                S.validate()
            if f.description and imgui.is_item_hovered():
                imgui.set_tooltip(f.description)
    if schema and schema.key == "exit":
        changed, target = imgui.input_text("Leads to", o.get("target", ""), imgui.InputTextFlags_.enter_returns_true)
        if changed:
            h.set_value(uid, ("target",), target or None, "exit target")
            S.validate()
        if imgui.is_item_hovered():
            imgui.set_tooltip("pack:level/<name> or stock:level/<number>")
    if imgui.collapsing_header("Entry"):
        imgui.text(f"size 0x{o['size']:X}" if "size" in o else "size from the stock base")
        if o.get("stock"):
            imgui.text(f"stock base: list {o['stock']['list']}, entry {o['stock']['entry']}")
        if o.get("params"):
            imgui.text_wrapped(f"raw: {o['params']}")


def project_window() -> None:
    if not S.project:
        if imgui.button("Open project..."):
            _open_dialog()
        imgui.same_line()
        if imgui.button("New project..."):
            S.new_project["open"] = True
        return
    _title(S.project.meta["name"])
    imgui.text_disabled(f"{S.project.id}  {S.project.meta['version']}   {S.project.path}")
    if imgui.tree_node_ex("Levels", imgui.TreeNodeFlags_.default_open):
        for name, level in S.project.levels.items():
            if imgui.selectable(f"{level.level['name']}  ({name})", S.level == name)[0]:
                S.level, S.selection = name, set()
        imgui.tree_pop()
    if imgui.tree_node_ex("Planets", imgui.TreeNodeFlags_.default_open):
        for name, p in S.project.planets.items():
            where = p.get("replaces") or f"disc {p.get('disc', {}).get('index', '?')}"
            imgui.bullet_text(f"{p['displayName'].get('en', name)}  ({where}, world {p.get('fixedWorld', '?')})")
        imgui.tree_pop()
    if imgui.tree_node("Geometry"):
        for f in sorted((S.project.path / "geometry").glob("*.glb")):
            imgui.bullet_text(f.name)
        imgui.tree_pop()
    if imgui.tree_node("Dependencies"):
        deps = S.project.meta.get("dependencies", [])
        if not deps:
            imgui.text_disabled("none")
        for d in deps:
            imgui.bullet_text(f"{d['id']} {d['version']}")
        imgui.tree_pop()


def prefabs() -> None:
    imgui.text_disabled("Stock types with decoded fields:")
    for s in sorted(S.schemas.values(), key=lambda s: s.name):
        imgui.bullet_text(s.name)
        if imgui.is_item_hovered():
            imgui.set_tooltip(s.description)
    imgui.spacing()
    imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    imgui.text_wrapped("The catalog of every stock type comes with the Source Manager (task S3).")
    imgui.pop_style_color()


def console() -> None:
    if imgui.button("Validate"):
        S.validate()
    imgui.same_line()
    if S.project:
        errors = sum(p.severity == val.ERROR for p in S.problems)
        warnings = sum(p.severity == val.WARNING for p in S.problems)
        imgui.text(f"{errors} errors, {warnings} warnings")
    imgui.separator()
    for line in S.load_errors:
        imgui.text_colored(theme.colour("ERROR"), line)
    colours = {val.ERROR: "ERROR", val.WARNING: "HIGHLIGHT", val.INFO: "MUTED"}
    for i, p in enumerate(S.problems):
        imgui.push_style_color(imgui.Col_.text, theme.colour(colours[p.severity]))
        clicked = imgui.selectable(f"{p.severity:7} {p.file}{' [' + p.uid + ']' if p.uid else ''}  "
                                   f"{p.message}##p{i}", False)[0]
        imgui.pop_style_color()
        if clicked and p.uid and p.file.startswith("levels/"):
            S.level = p.file.split("/")[1]
            S.selection = {p.uid}


def level_graph() -> None:
    if not S.project:
        return
    for level in S.project.levels.values():
        imgui.text(level.level["name"])
        for o in level.objects["objects"]:
            if o.get("target"):
                imgui.text_disabled(f"    exit {o['uid']}  ->  {o['target']}  start point "
                                    f"{o.get('fields', {}).get('startPoint', 0)}")
    for name, p in S.project.planets.items():
        imgui.text(f"planet {p['displayName'].get('en', name)}  ->  {p['entryLevel']['juno']} (Juno)")


# ---------------------------------------------------------------- toolbar, menus, dialogs

def toolbar() -> None:
    for name, key, tip in TOOLS:
        active = S.tool == name
        if active:
            imgui.push_style_color(imgui.Col_.button, theme.colour("ACCENT", 0.8))
        if imgui.button(f"{name.title()} ({key})"):
            S.tool = name
        if active:
            imgui.pop_style_color()
        if imgui.is_item_hovered():
            imgui.set_tooltip(tip)
        imgui.same_line()
    imgui.text("|")
    imgui.same_line()
    _, S.local = imgui.checkbox("Local", S.local)
    imgui.same_line()
    _, S.snap = imgui.checkbox("Snap", S.snap)
    imgui.same_line()
    _, S.snap_floor = imgui.checkbox("Floor", S.snap_floor)
    if imgui.is_item_hovered():
        imgui.set_tooltip("Moved objects drop onto the floor under them")
    imgui.same_line()
    imgui.set_next_item_width(hello_imgui.em_size(5))
    _, S.grid = imgui.input_float("##grid", S.grid, 0, 0, "%.0f")
    imgui.same_line()
    imgui.text("|")
    imgui.same_line()
    imgui.begin_disabled()
    imgui.button("Play")
    imgui.end_disabled()
    if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip("Build and play in the game: needs the port's launch options (task P5a) and the "
                          "pack builder (S6)")
    imgui.same_line()
    if imgui.button("Validate"):
        S.validate()


def menus() -> None:
    if imgui.begin_menu("File"):
        if imgui.menu_item("New project...", "", False)[0]:
            S.new_project["open"] = True
        if imgui.menu_item("Open project...", "Ctrl+O", False)[0]:
            _open_dialog()
        if imgui.menu_item("Save", "Ctrl+S", False, S.project is not None)[0]:
            S.save()
        imgui.separator()
        if imgui.menu_item("Settings...", "", False)[0]:
            S.settings_open = True
        imgui.end_menu()
    if imgui.begin_menu("Edit"):
        h = S.history()
        if imgui.menu_item("Undo", "Ctrl+Z", False, bool(h and h.done))[0]:
            S.undo()
        if imgui.menu_item("Redo", "Ctrl+Y", False, bool(h and h.undone))[0]:
            S.redo()
        imgui.separator()
        if imgui.menu_item("Duplicate", "Ctrl+D", False, bool(S.selection))[0]:
            duplicate()
        if imgui.menu_item("Delete", "Delete", False, bool(S.selection))[0]:
            delete()
        if imgui.menu_item("Frame selection", "F", False, S.project is not None)[0]:
            frame_selection()
        imgui.end_menu()
    if imgui.begin_menu("Window"):
        for w in hello_imgui.get_runner_params().docking_params.dockable_windows:
            _, w.is_visible = imgui.menu_item(w.label, "", w.is_visible)
        imgui.separator()
        if imgui.menu_item("Reset layout", "", False)[0]:
            hello_imgui.get_runner_params().docking_params.layout_reset = True
        imgui.end_menu()
    if imgui.begin_menu("Build"):
        if imgui.menu_item("Validate", "", False, S.project is not None)[0]:
            S.validate()
        imgui.menu_item("Build pack", "", False, False)
        imgui.menu_item("Play in game", "", False, False)
        imgui.end_menu()


def _open_dialog() -> None:
    folder = pfd.select_folder("Open a .jfgproj folder").result()
    if folder:
        S.open(folder)


def settings_popup() -> None:
    """File > Settings: the decomp, port and ROM paths (the same settings as `jfgstudio settings`)."""
    from jfgstudio import settings
    if S.settings_open:
        S.settings_edit = {k: settings.load().get(k, "") for k in settings.KEYS}
        imgui.open_popup("Settings")
        S.settings_open = False
    if not imgui.begin_popup_modal("Settings", None, imgui.WindowFlags_.always_auto_resize)[0]:
        return
    for key, what in settings.KEYS.items():
        imgui.text(what)
        imgui.set_next_item_width(hello_imgui.em_size(36))
        _, S.settings_edit[key] = imgui.input_text(f"##{key}", S.settings_edit[key])
        imgui.same_line()
        if imgui.button(f"...##{key}"):
            if key == "rom":
                picked = (pfd.open_file("Your US ROM").result() or [None])[0]
            else:
                picked = pfd.select_folder(what).result()
            if picked:
                S.settings_edit[key] = picked
        found = settings.detected(key)
        if not S.settings_edit[key] and found:
            imgui.text_disabled(f"found automatically: {found}")
        imgui.spacing()
    if imgui.button("Save"):
        for key, value in S.settings_edit.items():
            settings.set_value(key, value.strip() or None)
        S.view.load_track(None)  # reload the Scene with the new paths
        S.view.mesh_path = "reload"
        S.message = "Settings saved"
        imgui.close_current_popup()
    imgui.same_line()
    if imgui.button("Cancel"):
        imgui.close_current_popup()
    imgui.end_popup()


def new_project_popup() -> None:
    d = S.new_project
    if d["open"]:
        imgui.open_popup("New project")
        d["open"] = False
    if imgui.begin_popup_modal("New project", None, imgui.WindowFlags_.always_auto_resize)[0]:
        _, d["name"] = imgui.input_text("Name", d["name"])
        _, d["id"] = imgui.input_text("Mod id", d["id"])
        imgui.text_disabled("lower case, e.g. yourname.my_planet")
        _, d["folder"] = imgui.input_text("Folder", d["folder"])
        imgui.same_line()
        if imgui.button("..."):
            d["folder"] = pfd.select_folder("Where to create the project").result() or d["folder"]
        if imgui.button("Create") and d["folder"] and d["id"] and d["name"]:
            path = Path(d["folder"]) / f"{d['id'].split('.')[-1]}.jfgproj"
            try:
                proj.create(path, d["id"], d["name"])
                S.open(str(path))
                imgui.close_current_popup()
            except proj.ProjectError as e:
                S.message = "; ".join(e.problems)
        imgui.same_line()
        if imgui.button("Cancel"):
            imgui.close_current_popup()
        imgui.end_popup()


def duplicate() -> None:
    h = S.history()
    if h and S.selection:
        S.selection = set(h.duplicate(S.selection))
        S.validate()


def delete() -> None:
    h = S.history()
    if h and S.selection:
        h.delete(S.selection)
        S.selection = set()
        S.validate()


def shortcuts() -> None:
    io = imgui.get_io()
    if io.want_text_input:
        return
    flying = imgui.is_mouse_down(imgui.MouseButton_.right)  # WASD / QE fly the camera then
    if not flying and not io.key_ctrl:
        for name, key, _ in TOOLS:
            if imgui.is_key_pressed(getattr(imgui.Key, key.lower()), False):
                S.tool = name
        if imgui.is_key_pressed(imgui.Key.f, False):
            frame_selection()
        if imgui.is_key_pressed(imgui.Key.delete, False):
            delete()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
        S.save()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.o, False):
        _open_dialog()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.z, False):
        S.undo()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.y, False):
        S.redo()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.d, False):
        duplicate()


def status() -> None:
    if S.project:
        errors = sum(p.severity == val.ERROR for p in S.problems)
        imgui.text(f"{S.project.meta['name']}{' *' if S.dirty() else ''}  |  level {S.level or '-'}  |  "
                   f"{len(S.selection)} selected  |  {errors} errors  |  tool {S.tool}")
    if S.message:
        imgui.same_line()
        imgui.text_colored(theme.colour("MUTED"), f"   {S.message}")


# ---------------------------------------------------------------- layout and run

def _layout() -> hello_imgui.DockingParams:
    def split(src, new, direction, ratio):
        s = hello_imgui.DockingSplit()
        s.initial_dock, s.new_dock, s.direction, s.ratio = src, new, direction, ratio
        return s

    d = imgui.Dir
    params = hello_imgui.DockingParams()
    params.docking_splits = [
        split("MainDockSpace", "BottomSpace", d.down, 0.28),
        split("MainDockSpace", "LeftSpace", d.left, 0.2),
        split("MainDockSpace", "RightSpace", d.right, 0.27),
        split("LeftSpace", "LeftBottomSpace", d.down, 0.45),
    ]

    def win(label, space, fn):
        w = hello_imgui.DockableWindow()
        w.label, w.dock_space_name, w.gui_function = label, space, fn
        return w

    params.dockable_windows = [
        win("Scene", "MainDockSpace", scene),
        win("Level Graph", "MainDockSpace", level_graph),
        win("Hierarchy", "LeftSpace", hierarchy),
        win("Prefabs", "LeftBottomSpace", prefabs),
        win("Inspector", "RightSpace", inspector),
        win("Project", "BottomSpace", project_window),
        win("Console", "BottomSpace", console),
    ]
    params.layout_condition = hello_imgui.DockingLayoutCondition.application_start
    return params


def _write_png(path: str, image) -> None:
    height, width, channels = image.shape
    raw = b"".join(b"\0" + image[y].tobytes() for y in range(height))
    colour_type = 6 if channels == 4 else 2

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def run(project_path: str | None = None, screenshot: str | None = None, frames: int = 30) -> int:
    if project_path:
        S.open(project_path)
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = f"JFG-Studio {__version__}"
    params.app_window_params.window_geometry.size = (1600, 960)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space)
    params.imgui_window_params.show_menu_bar = True
    params.imgui_window_params.show_menu_app = False
    params.imgui_window_params.show_menu_view = False
    params.imgui_window_params.show_status_bar = True
    params.imgui_window_params.show_status_fps = False
    params.imgui_window_params.menu_app_title = "JFG-Studio"
    params.imgui_window_params.remember_theme = False
    params.imgui_window_params.background_color = theme.colour("BACKGROUND")
    params.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    params.ini_filename = "JFG-Studio/layout.ini"
    if screenshot:
        params.ini_disable = True  # a screenshot always shows the default layout
    params.docking_params = _layout()
    params.callbacks.setup_imgui_style = theme.apply_style
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.show_menus = menus
    params.callbacks.show_status = status
    toolbar_options = hello_imgui.EdgeToolbarOptions()
    toolbar_options.size_em = 2.6
    toolbar_options.window_bg = theme.colour("BACKGROUND")
    params.callbacks.add_edge_toolbar(hello_imgui.EdgeToolbarType.top, toolbar, toolbar_options)
    counter = {"n": 0}

    def gui():
        gz.begin_frame()
        shortcuts()
        new_project_popup()
        settings_popup()
        if screenshot:
            counter["n"] += 1
            if counter["n"] >= frames:
                params.app_shall_exit = True

    params.callbacks.show_gui = gui
    params.fps_idling.enable_idling = not screenshot
    hello_imgui.run(params)
    if screenshot:
        _write_png(screenshot, hello_imgui.final_app_window_screenshot())
    return 0
