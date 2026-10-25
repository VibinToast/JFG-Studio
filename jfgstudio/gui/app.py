"""The editor window: a docked, Unity-style layout (design doc section 6) in the JFG theme.

Phase 0 is the shell: Hierarchy, Scene, Inspector, Project, Prefabs, Console and Level Graph windows, the toolbar and
the menus, working on a loaded project (open, browse, select, validate). The 3D Scene view, editing and Play come in
phase 1.
"""

import struct
import zlib
from pathlib import Path

from imgui_bundle import hello_imgui, imgui
from imgui_bundle import portable_file_dialogs as pfd

from jfgstudio import __version__
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val
from jfgstudio.gui import theme

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
            self.message = "Saved"

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


def scene() -> None:
    _title("Scene")
    if not S.project or not S.level:
        imgui.text_disabled("Open a project and pick a level in the Project window.")
        return
    level = S.project.levels[S.level]
    imgui.text(f"{level.level['name']}   ({level.ref})")
    imgui.text_disabled(f"track {level.level['track']}   sky {level.level.get('sky', -1)}   "
                        f"{len(level.objects['objects'])} objects")
    imgui.spacing()
    avail = imgui.get_content_region_avail()
    draw = imgui.get_window_draw_list()
    p0 = imgui.get_cursor_screen_pos()
    p1 = imgui.ImVec2(p0.x + avail.x, p0.y + max(avail.y, 50))
    draw.add_rect_filled(p0, p1, imgui.get_color_u32(theme.colour("BACKGROUND")), 6.0)
    # A top-down sketch until the 3D view arrives (phase 1): objects as dots, regions as outlines.
    objs = S.objects()
    if objs:
        xs = [o["pos"][0] for o in objs]
        zs = [o["pos"][2] for o in objs]
        span = max(max(xs) - min(xs), max(zs) - min(zs), 1) * 1.15
        cx, cz = (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2
        scale = min(p1.x - p0.x, p1.y - p0.y) / span

        def at(x, z):
            return imgui.ImVec2((p0.x + p1.x) / 2 + (x - cx) * scale, (p0.y + p1.y) / 2 + (z - cz) * scale)

        by_region = {}
        for o in objs:
            if o.get("fields", {}).get("corner") is not None and o["type"].endswith("/0x5"):
                by_region.setdefault(o["fields"].get("region"), []).append(o)
        for corners in by_region.values():
            pts = [at(c["pos"][0], c["pos"][2]) for c in sorted(corners, key=lambda c: c["fields"]["corner"])]
            for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
                draw.add_line(a, b, imgui.get_color_u32(theme.colour("BORDER")), 2.0)
        for o in objs:
            selected = o["uid"] in S.selection
            col = theme.colour("HIGHLIGHT" if selected else "ACCENT")
            draw.add_circle_filled(at(o["pos"][0], o["pos"][2]), 6.0 if selected else 4.0, imgui.get_color_u32(col))
    draw.add_text(imgui.ImVec2(p0.x + 10, p1.y - 24), imgui.get_color_u32(theme.colour("MUTED")),
                  "Top-down preview. The 3D Scene view comes in phase 1.")
    imgui.dummy(imgui.ImVec2(avail.x, max(avail.y, 50)))


def inspector() -> None:
    sel = [o for o in S.objects() if o["uid"] in S.selection]
    if not sel:
        imgui.text_disabled("Select an object in the Hierarchy.")
        return
    if len(sel) > 1:
        imgui.text(f"{len(sel)} objects selected")
        return
    o = sel[0]
    schema = obj.schema_for(o, S.schemas)
    _title(o.get("name") or (schema.name if schema else "Object"))
    imgui.text_disabled(f"{o['type']}   uid {o['uid']}")
    if imgui.collapsing_header("Transform", imgui.TreeNodeFlags_.default_open):
        imgui.input_float3("Position", list(map(float, o["pos"])), "%.0f", imgui.InputTextFlags_.read_only)
    if schema and imgui.collapsing_header(schema.name, imgui.TreeNodeFlags_.default_open):
        if schema.status != "decoded":
            imgui.text_colored(theme.colour("HIGHLIGHT"), "Partly decoded: other fields come from the stock entry.")
        for f in schema.fields:
            value = o.get("fields", {}).get(f.name, f.default)
            imgui.input_int(f.name, value, 0, 0, imgui.InputTextFlags_.read_only)
            if f.description and imgui.is_item_hovered():
                imgui.set_tooltip(f.description)
    if o.get("target"):
        imgui.text(f"Leads to {o['target']}")
    if imgui.collapsing_header("Entry"):
        imgui.text(f"size 0x{o['size']:X}" if "size" in o else "size from the stock base")
        if o.get("stock"):
            imgui.text(f"stock base: list {o['stock']['list']}, entry {o['stock']['entry']}")
        if o.get("params"):
            imgui.text_wrapped(f"raw: {o['params']}")
    imgui.spacing()
    imgui.text_disabled("Editing comes in phase 1.")


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
            imgui.bullet_text(f"{p['displayName'].get('en', name)}  (disc {p['disc']['index']}, "
                              f"world {p.get('fixedWorld', '?')})")
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
        imgui.end_menu()
    if imgui.begin_menu("Edit"):
        imgui.menu_item("Undo", "Ctrl+Z", False, False)
        imgui.menu_item("Redo", "Ctrl+Y", False, False)
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


def shortcuts() -> None:
    io = imgui.get_io()
    if io.want_text_input:
        return
    for name, key, _ in TOOLS:
        if imgui.is_key_pressed(getattr(imgui.Key, key.lower()), False):
            S.tool = name
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
        S.save()
    if io.key_ctrl and imgui.is_key_pressed(imgui.Key.o, False):
        _open_dialog()


def status() -> None:
    if S.project:
        errors = sum(p.severity == val.ERROR for p in S.problems)
        imgui.text(f"{S.project.meta['name']}  |  level {S.level or '-'}  |  {len(S.selection)} selected  |  "
                   f"{errors} errors  |  tool {S.tool}")
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
        shortcuts()
        new_project_popup()
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
