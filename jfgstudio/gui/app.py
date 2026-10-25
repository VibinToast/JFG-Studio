"""The editor window: a docked, Unity-style layout (design doc section 6) in the JFG theme.

Hierarchy, Scene (3D: gui/viewport.py), Inspector, Project, Prefabs, Console and Level Graph windows, the toolbar and
the menus. Edits go through jfgstudio.edit's per-level undo history.
"""

import copy
import json
import math
import os
import struct
import threading
import zlib
from pathlib import Path

import numpy as np
from imgui_bundle import hello_imgui, imgui, imguizmo
from imgui_bundle import portable_file_dialogs as pfd

from jfgstudio import __version__, crashlog, edit, refs
from jfgstudio import objects as obj
from jfgstudio import prefabs as pre
from jfgstudio import project as proj
from jfgstudio import validate as val
from jfgstudio.gui import kind_tools, level_tools, model_viewer, planet_tools, script_tools, theme
from jfgstudio.gui import viewport as vp

gz = imguizmo.im_guizmo

TOOLS = (("hand", "Q", "Hand: pan and orbit the view"), ("move", "W", "Move"),
         ("rotate", "E", "Rotate: types with a facing field"), ("scale", "R", "Scale: types with a size field"),
         ("region", "G", "Region: click a navigation region's corners on the floor"),
         ("water", "T", "Water: drag a rectangle on the floor to add a water surface to the pack's geometry"),
         ("patrol", "P", "Patrol: click a squad patrol network's points; each links to the last"))


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
        self.new_project = {"open": False, "folder": "", "id": "", "name": "", "glb": ""}
        self.message = ""
        self.histories: dict[str, edit.History] = {}
        self.view = vp.Viewport()
        self.camera = vp.Camera()
        self.framed_level = None
        self.snap_floor = True
        self.drag = None  # uid -> start position, while the gizmo moves the selection
        self.scene_rect = (0.0, 0.0, 1.0, 1.0)
        self.pos_edit = None  # the Inspector's position before a drag of its fields
        self.gizmo_note = ""
        self.box_start = None  # where a box selection drag started (Scene pixels)
        self.region_draft: list[list[float]] = []  # the Region tool's corners so far
        self.water_draft = None  # where a Water drag started
        self.patrol_last = None  # the Patrol tool's last point (uid), which the next one links to
        self.water_height = 10.0
        self.water_auto = True  # the water height follows the floor where the drag starts (+15)
        self.exit_filter = ""
        self.stock_pick = {"open": False, "filter": "", "id": None}
        self.kind: str | None = None  # the Kinds window's kind
        self.triggers: list | None = None  # triggers.json, read when the Triggers window first shows
        self.triggers_dirty = False
        self.weapons: dict | None = None  # weapons.json, read when the Weapons window first shows
        self.weapons_dirty = False
        self.script_file: str | None = None  # the Scripts window's file and text
        self.script_text = ""
        self.kind_pick = {"open": False, "filter": "", "base": None, "name": ""}
        self.planet: str | None = None  # the Planet window's planet
        self.planets_dirty = False  # planet edits are saved with the project (they have no undo yet)
        self.map_doc = None
        self.play_log: list[str] = []  # the last Play / Build run's output, shown in the Console
        self.playing = False
        self.play_character = "juno"
        self.errors: list[str] = []  # errors a window hit (logged in crash logs), shown in the Console
        self.errors_seen: set[str] = set()  # why the rotate / scale handle is not shown for the selection
        self.drag_scale = 0  # the size field's value when a scale drag started
        self.settings_open = False
        self.settings_edit = {}
        self.cat = None  # the local catalog of the user's ROM, opened on first use
        self.cat_error = ""
        self.objtypes: dict[int, dict] = {}  # object id -> name, behaviour, models, stock examples
        self.behaviours: dict[int, int] = {}
        self.list_levels: dict[int, list[str]] = {}  # stock object list -> the levels using it
        self.model_paths: dict[int, str | None] = {}
        self.stock_entries: dict[tuple, bytes] = {}
        self.instances = []  # (uid, model path, matrix) drawn in the last frame, for picking
        self.prefab = {"search": "", "used": True, "oid": None, "example": 0, "squad": 0, "door": 0, "members": 2}

    # project actions
    def open(self, path: str) -> None:
        try:
            self.project = proj.load(path)
        except proj.ProjectError as e:
            self.load_errors = e.problems
            self.message = f"Could not open {path}: {len(e.problems)} problems (see Console)"
            return
        self.load_errors = []
        self.triggers, self.triggers_dirty, self.script_file = None, False, None
        self.weapons, self.weapons_dirty = None, False
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
            self.planets_dirty = False
            if self.triggers is not None and self.triggers_dirty:
                from jfgstudio import scripts as scr
                scr.save_triggers(self.project, self.triggers)
                self.triggers_dirty = False
            if self.weapons is not None and self.weapons_dirty:
                from jfgstudio import scripts as scr
                scr.save_weapons(self.project, self.weapons)
                self.weapons_dirty = False
            self.message = "Saved"

    def history(self) -> edit.History | None:
        if not self.project or self.level not in self.project.levels:
            return None
        if self.level not in self.histories:
            self.histories[self.level] = edit.History(self.project.levels[self.level])
        return self.histories[self.level]

    def history_for(self, name: str) -> edit.History:
        if name not in self.histories:
            self.histories[name] = edit.History(self.project.levels[name])
        return self.histories[name]

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
        return self.planets_dirty or any(h.dirty for h in self.histories.values())

    def track_path(self) -> str | None:
        """The .glb the Scene shows for the open level: the project's geometry, or a stock track from the catalog."""
        level = self.project.levels[self.level]
        r = refs.parse(level.level["track"], "track")
        if r.is_pack:
            path = self.project.geometry_path(r.name)
            return str(path) if path.is_file() else None
        if self.catalog() is None:
            raise RuntimeError(self.cat_error)
        return str(self.cat.track_glb(r.number()))

    # the user's catalog (names, models and stock entries come from the user's own ROM)
    def catalog(self):
        """The local catalog, or None (with cat_error) when the ROM or the decomp is not set."""
        if self.cat is None and not self.cat_error:
            try:
                from jfgstudio.catalog import Catalog
                self.cat = Catalog.open()
                self.objtypes = self.cat.objtypes()
                self.behaviours = {k: v["behaviour"] for k, v in self.objtypes.items()}
                with open(self.cat.dir / "levels.json", encoding="utf-8") as f:
                    for lv in json.load(f):
                        for key in ("objectList0", "objectList1"):
                            self.list_levels.setdefault(lv[key], []).append(lv["name"])
            except Exception as e:  # shown in the Prefabs window and the Scene
                self.cat, self.cat_error = None, str(e)
        return self.cat

    def reset_catalog(self) -> None:
        self.cat, self.cat_error, self.objtypes, self.behaviours = None, "", {}, {}
        self.prefab.pop("squads", None)
        self.list_levels, self.model_paths, self.stock_entries = {}, {}, {}
        self.map_doc = None

    def schema(self, o: dict) -> obj.Schema | None:
        self.catalog()
        return obj.schema_for(o, self.schemas, self.behaviours)

    def stock_entry(self, list_id: int, index: int) -> bytes | None:
        key = (list_id, index)
        if key not in self.stock_entries:
            try:
                self.stock_entries[key] = self.cat.stock_entry(list_id, index) if self.catalog() else None
            except IndexError:
                self.stock_entries[key] = None
        return self.stock_entries[key]

    def fields(self, o: dict, schema: obj.Schema | None) -> dict:
        """An object's field values as the build writes them: its stock base (or raw params, or zeros), then its own
        fields on top."""
        if schema is None:
            return dict(o.get("fields", {}))
        base = self.stock_entry(o["stock"]["list"], o["stock"]["entry"]) if "stock" in o else None
        params = bytes.fromhex(o["params"]) if "params" in o else (base or bytes(10))[obj.PARAMS_OFFSET:]
        size = o.get("size") or (len(base) if base else schema.min_size)
        entry = bytes(obj.PARAMS_OFFSET) + params[:max(size - obj.PARAMS_OFFSET, 0)].ljust(size - obj.PARAMS_OFFSET,
                                                                                            b"\0")
        out = obj.decode_entry(bytes([0, 0, size]) + entry[3:], schema).get("fields", {})
        out.update(o.get("fields", {}))
        return out

    def model_path(self, oid: int) -> str | None:
        """The .glb of an object type's first model, exported into the catalog the first time."""
        if oid not in self.model_paths:
            t = self.objtypes.get(oid) if self.catalog() else None
            path = None
            if t and t["kind"] == "models" and t["assets"]:
                try:
                    path = str(self.cat.model_glb(t["assets"][0]))
                except Exception:  # a model jfgformats can't export is drawn as a marker
                    path = None
            self.model_paths[oid] = path
        return self.model_paths[oid]

    def sky_for(self, level: dict):
        """(dome .glb, mode) for a level's sky (header +0x58 is a definition index; +0x69 the mode), or None."""
        d = level.get("sky", -1)
        mode = level["header"].get("loadTexture", 0)
        if d is None or d < 0 or mode not in (0, 1) or self.catalog() is None:
            return None
        key = ("sky", d)
        if key not in self.model_paths:
            ids = self.cat.definition_models(d)
            try:
                self.model_paths[key] = str(self.cat.model_glb(ids[0])) if ids else None
            except Exception:  # noqa: BLE001 (a dome jfgformats can't export: no sky in the preview)
                self.model_paths[key] = None
        path = self.model_paths[key]
        return (path, mode) if path else None

    def stock_objects(self) -> list[dict]:
        """For a stock-level edit: the stock level's own objects (read from the user's catalog), as display-only
        project objects (uids "stock:<list>:<index>")."""
        if not self.project or self.level not in self.project.levels:
            return []
        base = self.project.levels[self.level].base
        if base is None or self.catalog() is None:
            return []
        key = ("stock-objects", base)
        if key not in self.model_paths:
            out = []
            lv = self.cat.levels()[base]
            for n in sorted({lv["objectList0"], lv["objectList1"]}):
                try:
                    entries = self.cat.objlist(n)
                except (IndexError, ValueError):
                    continue
                for i, e in enumerate(entries):
                    if e is None:
                        continue
                    d = obj.decode_entry(e[1])
                    out.append({"uid": f"stock:{n}:{i}", "type": f"stock:objtype/0x{d['id']:X}", "pos": d["pos"],
                                "stock": {"list": n, "entry": i}, "name": self.type_name(d["id"])})
            self.model_paths[key] = out
        return self.model_paths[key]

    def type_name(self, oid: int) -> str:
        t = self.objtypes.get(oid) if self.catalog() else None
        return t["name"] if t else f"0x{oid:X}"

    def objects(self) -> list[dict]:
        if not self.project or self.level not in self.project.levels:
            return []
        return self.project.levels[self.level].objects["objects"]


S = State()


# ---------------------------------------------------------------- windows

def guarded(name: str, fn):
    """A window function that can't take the editor down: an error is logged once (crashlog) and shown in the window
    and the Console; ImGui's error recovery closes what the function left open."""
    def run():
        try:
            fn()
        except Exception as e:  # noqa: BLE001 (any error in one window)
            key = f"{name}: {type(e).__name__}: {e}"
            if key not in S.errors_seen:
                S.errors_seen.add(key)
                try:
                    path = crashlog.write(e, f"the {name} window")
                except OSError:
                    path = "(could not write the log)"
                S.errors.append(f"{key}  (log: {path})")
                S.message = f"{name} hit an error: see the Console"
            imgui.text_colored(theme.colour("ERROR"), f"This window hit an error: {type(e).__name__}: {e}")
    return run


def labelled(fn):
    """A window whose fields leave room for their labels on the right."""
    def run():
        imgui.push_item_width(-hello_imgui.em_size(8.5))
        try:
            fn()
        finally:
            imgui.pop_item_width()
    return run


def _muted(text: str) -> None:
    """Muted text that wraps at the window's edge."""
    imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    imgui.text_wrapped(text)
    imgui.pop_style_color()


def _title(text: str) -> None:
    if theme.TITLE_FONT:
        imgui.push_font(theme.TITLE_FONT, 0.0)
        imgui.text_colored(theme.colour("ACCENT"), text)
        imgui.pop_font()
    else:
        imgui.text_colored(theme.colour("ACCENT"), text)


def _object_label(o: dict) -> str:
    if o["type"].startswith("pack:kind/") and S.project:
        kind = S.project.kinds.get(o["type"].split("/", 1)[1])
        if kind:
            return f"{o.get('name') or kind['name']}  ##{o['uid']}"
    oid = obj.stock_id(o)
    schema = S.schema(o)
    kind = S.type_name(oid) if oid is not None else (schema.name if schema else o["type"].split("/", 1)[-1])
    return f"{o.get('name') or kind}  ##{o['uid']}"


def _select(uid: str) -> None:
    if imgui.get_io().key_ctrl:
        S.selection ^= {uid}
    else:
        S.selection = {uid}


def hierarchy() -> None:
    objs = S.objects()
    if not S.project:
        _muted("No project open. File > Open project...")
        return
    if not objs:
        imgui.text_disabled("This level has no objects.")
        return
    keys = _keys(objs)
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
    return {o["uid"]: (sc.key if (sc := S.schema(o)) else None) for o in objs}


def _matrix(o: dict, schema: obj.Schema | None, oid: int | None) -> np.ndarray:
    """Where an object's model goes: its position, the facing and size its entry gives, the definition's scale."""
    values = S.fields(o, schema)
    angles = {}
    scale = (S.objtypes.get(oid, {}).get("scale") or 1.0) if oid is not None else 1.0
    if schema:
        for role in ("yaw", "pitch", "roll"):
            f = schema.role(role)
            if f is not None:
                angles[role] = obj.angle_of(f, values.get(f.name, f.default))
        f = schema.role("scale")
        if f is not None:
            scale *= obj.scale_of(f, values.get(f.name, f.default))
        scale *= schema.scale_factor(values)
    return vp.object_matrix([float(v) for v in o["pos"]], scale=scale, **angles)


def _rings(objs) -> dict:
    """uid -> the radii of an object's ranges (fields with the "radius" role), for the Scene."""
    out = {}
    for o in objs:
        schema = S.schema(o)
        if schema:
            fields = [f for f in schema.fields if f.role == "radius"]
            if fields:
                values = S.fields(o, schema)
                out[o["uid"]] = [values.get(f.name, f.default) * f.radius_scale for f in fields]
    return out


def _instances(objs, pickable: bool = True) -> tuple[list, dict]:
    """The objects drawn with their models: (path, matrix, tint) for the renderer, and their boxes by uid. Pickable
    ones are remembered for clicking in the Scene."""
    out, boxes = [], {}
    if pickable:
        S.instances = []
    for o in objs:
        oid = kind_tools.base_oid(o)
        schema = S.schema(o)
        if oid is None or (schema and schema.hidden):
            continue
        path = S.model_path(oid)
        mesh = S.view.model(path) if path else None
        if mesh is None or not mesh.parts:
            continue
        m = _matrix(o, schema, oid)
        if o["type"].startswith("pack:kind/"):
            kind = S.project.kinds.get(o["type"].split("/", 1)[1], {})
            m = m @ np.diag([kind.get("scale", 1.0)] * 3 + [1.0])
        tint = (1.0, 1.0, 0.2, 0.35) if o["uid"] in S.selection else (0.0, 0.0, 0.0, 0.0)
        out.append((path, m, tint))
        boxes[o["uid"]] = vp.box_corners(mesh.bounds, m)
        if pickable:
            S.instances.append((o["uid"], path, m))
    return out, boxes


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
    hd = level.level["header"]
    S.view.clear = tuple(hd.get(k, 0) / 255 for k in ("clearR", "clearG", "clearB"))
    S.view.sky = S.sky_for(level.level) if S.view.show_sky else None
    if S.framed_level != S.level and S.view.mesh is not None:
        S.camera.look_from_above(S.view.mesh.bounds)
        S.framed_level = S.level
    _, S.view.show_fog = imgui.checkbox("Fog", S.view.show_fog)
    imgui.same_line()
    _, S.view.show_hidden = imgui.checkbox("Invisible colliders", S.view.show_hidden)
    imgui.same_line()
    _, S.view.show_sky = imgui.checkbox("Sky", S.view.show_sky)
    imgui.same_line()
    imgui.text_disabled(f"   {level.level['name']}  ({level.level['track']})   speed {S.camera.speed:.0f}")
    if S.view.error:
        imgui.text_colored(theme.colour("ERROR"), S.view.error)
    avail = imgui.get_content_region_avail()
    w, h = int(max(avail.x, 8)), int(max(avail.y, 8))
    objs = S.objects()
    keys = _keys(objs)
    instances, boxes = _instances(objs)
    stock = S.stock_objects()  # a stock-level edit: the stock level's own objects, drawn but not editable
    if stock:
        stock_keys = _keys(stock)
        stock_inst, stock_boxes = _instances(stock, pickable=False)
        instances = instances + [(p, m, (0.25, 0.3, 0.4, 0.45)) for p, m, _ in stock_inst]
        lines = vp.marker_lines(objs + stock, {**keys, **stock_keys}, S.selection,
                                vp.region_links(objs + stock, {**keys, **stock_keys}), {**boxes, **stock_boxes},
                                _rings(objs))
    else:
        lines = vp.marker_lines(objs, keys, S.selection, vp.region_links(objs, keys), boxes, _rings(objs))
    tex = S.view.render(w, h, S.camera, lines, instances)
    origin = imgui.get_cursor_screen_pos()
    S.scene_rect = (origin.x, origin.y, w, h)
    if tex is None:
        return
    imgui.image(imgui.ImTextureRef(tex), imgui.ImVec2(w, h), imgui.ImVec2(0, 1), imgui.ImVec2(1, 0))
    if imgui.begin_drag_drop_target():
        payload = imgui.accept_drag_drop_payload_py_id(PREFAB_PAYLOAD)
        if payload is not None:
            mouse = imgui.get_mouse_pos()
            _place(payload.data_id, _ground_point(mouse.x - origin.x, mouse.y - origin.y, (w, h)))
        imgui.end_drag_drop_target()
    hovered = imgui.is_item_hovered()
    draw = imgui.get_window_draw_list()
    for o in objs:
        p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + vp.MARKER_HEIGHT + 6, o["pos"][2]))
        if o["uid"] in boxes:
            top = max(c[1] for c in boxes[o["uid"]])
            p = vp.project(S.camera, (w, h), (o["pos"][0], top + 6, o["pos"][2])) if o["uid"] in S.selection else None
        if p and keys.get(o["uid"]) != "regionpoint":
            colour = theme.colour("HIGHLIGHT" if o["uid"] in S.selection else "MUTED")
            draw.add_text(imgui.ImVec2(origin.x + p[0] + 4, origin.y + p[1] - 14), imgui.get_color_u32(colour),
                          o.get("name") or o["uid"])
    if S.tool in ("region", "water", "patrol"):
        if S.drag is not None:
            _end_drag(objs)
        level_tools.scene_tool(origin, w, h, hovered)
        _camera_input(hovered, objs)
        return
    using = _gizmo(origin, w, h, objs)
    if S.gizmo_note:
        draw.add_text(imgui.ImVec2(origin.x + 10, origin.y + 8), imgui.get_color_u32(theme.colour("HIGHLIGHT")),
                      S.gizmo_note)
    _camera_input(hovered, objs)
    if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and not using and not gz.is_over() \
            and not imgui.get_io().key_alt:
        _pick(origin, w, h, objs, keys)
        mouse = imgui.get_mouse_pos()
        S.box_start = (mouse.x - origin.x, mouse.y - origin.y)
    _box_select(origin, w, h, objs, draw, using)


def _box_select(origin, w, h, objs, draw, using: bool) -> None:
    """Dragging from a click in the Scene draws a box; on release it selects the objects inside (Ctrl or Shift adds)."""
    if S.box_start is None:
        return
    if using or gz.is_using():
        S.box_start = None
        return
    mouse = imgui.get_mouse_pos()
    x0, y0 = S.box_start
    x1, y1 = mouse.x - origin.x, mouse.y - origin.y
    big = abs(x1 - x0) > 4 and abs(y1 - y0) > 4
    if imgui.is_mouse_down(imgui.MouseButton_.left):
        if big:
            a = imgui.ImVec2(origin.x + min(x0, x1), origin.y + min(y0, y1))
            b = imgui.ImVec2(origin.x + max(x0, x1), origin.y + max(y0, y1))
            draw.add_rect_filled(a, b, imgui.get_color_u32(theme.colour("ACCENT", 0.12)))
            draw.add_rect(a, b, imgui.get_color_u32(theme.colour("ACCENT")), 0.0, 1.0)
        return
    S.box_start = None
    if not big:
        return
    inside = set()
    for o in objs:
        p = vp.project(S.camera, (w, h), o["pos"])
        if p and min(x0, x1) <= p[0] <= max(x0, x1) and min(y0, y1) <= p[1] <= max(y0, y1):
            inside.add(o["uid"])
    io = imgui.get_io()
    S.selection = (S.selection | inside) if (io.key_ctrl or io.key_shift) else inside
    S.message = f"{len(inside)} selected"


def _pick(origin, w, h, objs, keys) -> None:
    """Selects what is under the mouse: a marker within 14 pixels, else the nearest model the ray hits."""
    mouse = imgui.get_mouse_pos()
    mx, my = mouse.x - origin.x, mouse.y - origin.y
    best, best_d = None, 14.0 ** 2
    modelled = {uid for uid, _, _ in S.instances}
    for o in objs:
        if o["uid"] in modelled:
            continue
        for lift in (vp.MARKER_HEIGHT, 0.0):
            p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + lift, o["pos"][2]))
            if p:
                d = (p[0] - mx) ** 2 + (p[1] - my) ** 2
                if d < best_d:
                    best, best_d = o["uid"], d
    if best is None:
        ray_o, ray_d = vp.screen_ray(S.camera, (w, h), mx, my)
        track_t = vp.raycast(S.view.mesh.visible, ray_o, ray_d) if S.view.mesh is not None else None
        best_t = track_t if track_t is not None else math.inf
        for uid, path, m in S.instances:
            inv = np.linalg.inv(m)
            t = vp.raycast(S.view.model(path).visible, (inv @ np.array([*ray_o, 1.0]))[:3], inv[:3, :3] @ ray_d)
            if t is not None and t < best_t + 1.0:
                best, best_t = uid, t
    if best is None:
        if not (imgui.get_io().key_ctrl or imgui.get_io().key_shift):
            S.selection = set()
    else:
        _select(best)


def _role_field(o: dict, role: str) -> obj.Field | None:
    """The schema field an object keeps its facing ("yaw") or size ("scale") in, if its type has one."""
    schema = S.schema(o)
    return schema.role(role) if schema else None


def _gizmo(origin, w, h, objs) -> bool:
    sel = [o for o in objs if o["uid"] in S.selection]
    S.gizmo_note = ""
    if not sel or S.tool not in ("move", "rotate", "scale"):
        if S.drag is not None:
            _end_drag(objs)
        return False
    if S.tool != "move":
        role = "yaw" if S.tool == "rotate" else "scale"
        if len(sel) > 1:
            S.gizmo_note = f"{S.tool.title()} works on one object at a time"
            return False
        if _role_field(sel[0], role) is None:
            S.gizmo_note = (f"{S.tool.title()}: this type has no {'facing' if role == 'yaw' else 'size'} field "
                            "the game reads (or it is not decoded yet)")
            return False
    gz.set_drawlist()
    gz.set_rect(origin.x, origin.y, w, h)
    view = gz.Matrix16(vp.pack_matrix(S.camera.view()))
    proj_m = gz.Matrix16(vp.pack_matrix(S.camera.projection(w / h)))
    centre = np.mean([o["pos"] for o in sel], axis=0)
    values = S.fields(sel[0], S.schema(sel[0])) if len(sel) == 1 else {}
    yaw_f = _role_field(sel[0], "yaw") if len(sel) == 1 else None
    yaw = obj.angle_of(yaw_f, values.get(yaw_f.name, yaw_f.default)) if yaw_f else 0.0
    m = gz.Matrix16(vp.pack_matrix(vp.object_matrix(centre, yaw)))
    if S.tool == "move":
        op = gz.OPERATION.translate
        snap = gz.Matrix3([S.grid, S.grid, S.grid]) if S.snap else None
    elif S.tool == "rotate":
        op = gz.OPERATION.rotate_y
        snap = gz.Matrix3([360.0 / 64, 0, 0]) if S.snap else None
    else:
        op = gz.OPERATION.scaleu
        snap = gz.Matrix3([0.05, 0.05, 0.05]) if S.snap else None
    mode = gz.MODE.local if S.local else gz.MODE.world
    changed = gz.manipulate(view, proj_m, op, mode, m, None, snap)
    using = gz.is_using()
    if using and S.drag is None:
        S.drag = {o["uid"]: (list(o["pos"]), dict(o.get("fields", {}))) for o in sel}
        scale_f = _role_field(sel[0], "scale") if S.tool == "scale" else None
        S.drag_scale = values.get(scale_f.name, scale_f.default) if scale_f else 0
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
        elif S.tool == "rotate":
            angle = math.atan2(new[0, 2], new[0, 0])
            sel[0].setdefault("fields", {})[yaw_f.name] = obj.value_of_angle(yaw_f, angle)
        elif S.drag is not None:
            f = _role_field(sel[0], "scale")
            factor = float(np.linalg.norm(new[:3, 0]))  # relative to the size when the drag started
            lo, hi = obj._RANGES[f.type]
            sel[0].setdefault("fields", {})[f.name] = int(min(max(round(max(S.drag_scale, 1) * factor), lo), hi))
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
            new_fields = o.get("fields", {})
            for k in sorted(set(old_fields) | set(new_fields)):
                if new_fields.get(k) != old_fields.get(k):
                    changes.append(edit.Change(o["uid"], ("fields", k), old_fields.get(k), new_fields.get(k)))
    h.record(edit.Command(S.tool, changes))
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
        _muted("Select an object in the Hierarchy or the Scene.")
        return
    if len(sel) > 1:
        imgui.text(f"{len(sel)} objects selected")
        _muted("W moves them together; Delete removes them; Ctrl+D duplicates them.")
        return
    o = sel[0]
    uid = o["uid"]
    schema = S.schema(o)
    oid = obj.stock_id(o)
    _title(o.get("name") or (S.type_name(oid) if oid is not None else schema.name if schema else "Object"))
    t = S.objtypes.get(oid) if oid is not None else None
    _muted(f"{o['type']}   uid {uid}")
    if t:
        _muted(f"{t['name']}: behaviour {t['behaviour']}, {t['kind']} "
               f"{', '.join(f'0x{a:X}' for a in t['assets']) or '-'}, used {t.get('uses', 0)} times in stock levels")
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
    is_exit = bool(schema and schema.key == "exit")
    if is_exit and imgui.collapsing_header("Destination", imgui.TreeNodeFlags_.default_open):
        level_tools.exit_picker(o)
    if schema and imgui.collapsing_header(schema.name, imgui.TreeNodeFlags_.default_open):
        if schema.status != "decoded":
            imgui.push_style_color(imgui.Col_.text, theme.colour("HIGHLIGHT"))
            imgui.text_wrapped("Partly decoded: other fields come from the stock entry.")
            imgui.pop_style_color()
        values = S.fields(o, schema)
        for f in schema.fields:
            if is_exit and f.link in ("level", "startPoint"):
                continue  # the Destination picker sets these
            _field_input(uid, f, int(values.get(f.name, f.default)), f.name in o.get("fields", {}))
    if imgui.collapsing_header("Entry"):
        imgui.text(f"size 0x{o['size']:X}" if "size" in o else "size from the stock base")
        if o.get("stock"):
            imgui.text(f"stock base: list {o['stock']['list']}, entry {o['stock']['entry']}")
        if o.get("params"):
            imgui.text_wrapped(f"raw: {o['params']}")


def _field_input(uid: str, f: obj.Field, value: int, own: bool) -> None:
    """One schema field in the Inspector: a list for named values, a number otherwise (angles also in degrees).
    Values from the stock base are shown dimmed until they are set."""
    h = S.history()
    lo, hi = obj._RANGES[f.type]
    if not own:
        imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    if f.enum:
        current = f"{value}: {f.enum[value]}" if value in f.enum else str(value)
        if imgui.begin_combo(f.name, current):
            for v, label in f.enum.items():
                if imgui.selectable(f"{v}: {label}", v == value)[0] and v != value:
                    h.set_value(uid, ("fields", f.name), v, f"set {f.name}")
                    S.validate()
            imgui.end_combo()
    else:
        changed, new = imgui.input_int(f.name, value, 1, 10, imgui.InputTextFlags_.enter_returns_true)
        if changed:
            h.set_value(uid, ("fields", f.name), int(min(max(new, lo), hi)), f"set {f.name}")
            S.validate()
    if not own:
        imgui.pop_style_color()
    tip = f.description
    if f.unit in obj.ANGLE_SHIFT:
        tip = f"{math.degrees(obj.angle_of(f, value)):.0f} degrees. {tip}"
    if not own:
        tip += "\n(from the stock entry)"
    if tip and imgui.is_item_hovered():
        imgui.set_tooltip(tip)


def _new_level() -> None:
    """A new level of the pack on the first geometry (a stock track when the project has none)."""
    if S.project is None:
        return
    geometry = sorted((S.project.path / "geometry").glob("*.glb"))
    track = f"pack:track/{geometry[0].stem}" if geometry else "stock:track/18"
    n = next(k for k in range(1, 999) if f"level{k}" not in S.project.levels)
    proj.new_level(S.project, f"level{n}", f"New level {n}", track)
    S.level, S.selection = f"level{n}", set()
    S.planets_dirty = True  # unsaved: a new level
    S.validate()


def stock_level_popup() -> None:
    """Edit a stock level: what the pack adds to it (objects, header fields) is patched in when the pack loads."""
    d = S.stock_pick
    if d["open"]:
        imgui.open_popup("Edit a stock level")
        d["open"] = False
    if not imgui.begin_popup_modal("Edit a stock level", None, imgui.WindowFlags_.always_auto_resize)[0]:
        return
    if S.catalog() is None:
        imgui.text(f"No catalog: {S.cat_error}")
    else:
        _, d["filter"] = imgui.input_text_with_hint("##stockfilter", "Search levels", d["filter"])
        imgui.begin_child("stocklevels", imgui.ImVec2(hello_imgui.em_size(28), hello_imgui.em_size(16)))
        for lv in S.cat.levels():
            label = f"{lv['id']}: {lv['name']}"
            if d["filter"] and d["filter"].lower() not in label.lower():
                continue
            if imgui.selectable(label, d["id"] == lv["id"])[0]:
                d["id"] = lv["id"]
        imgui.end_child()
        imgui.text_disabled("Objects you place are added to the level; its own stay as they are.")
        if imgui.button("Create") and d["id"] is not None:
            lv = S.cat.levels()[d["id"]]
            name = f"stock{lv['id']}"
            try:
                proj.edit_stock_level(S.project, name, lv["id"], lv["name"], lv["track"])
                S.level, S.selection = name, set()
                S.planets_dirty = True
                S.validate()
                imgui.close_current_popup()
            except proj.ProjectError as e:
                S.message = "; ".join(e.problems)
        imgui.same_line()
    if imgui.button("Cancel"):
        imgui.close_current_popup()
    imgui.end_popup()


def project_window() -> None:
    if not S.project:
        if imgui.button("Open project..."):
            _open_dialog()
        imgui.same_line()
        if imgui.button("New project..."):
            S.new_project["open"] = True
        return
    _title(S.project.meta["name"])
    _muted(f"{S.project.id}  {S.project.meta['version']}   {S.project.path}")
    if imgui.tree_node_ex("Levels", imgui.TreeNodeFlags_.default_open):
        for name, level in S.project.levels.items():
            kind = f"edits stock level {level.base}" if level.base is not None else name
            if imgui.selectable(f"{level.level['name']}  ({kind})##{name}", S.level == name)[0]:
                S.level, S.selection = name, set()
        imgui.tree_pop()
    if imgui.button("New level"):
        _new_level()
    imgui.same_line()
    if imgui.button("Edit a stock level..."):
        S.stock_pick["open"] = True
    stock_level_popup()
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


PREFAB_PAYLOAD = "JFG_PREFAB"
KIND_BASE = -100  # prefab ids -100, -101 ...: the project's kinds, in name order
GROUPS = {-1: ("Squad, region and locked door", "A squadron of stock members, a square navigation region they keep "
                                                 "to, and a door that stays locked until they are dead"),
          -2: ("Exit and return start point", "An exit (set where it leads in the Inspector) and a start point in "
                                              "front of it for the way back"),
          -3: ("Navigation region", "A square of 4 region corners; give its id to a squadron")}


def prefabs() -> None:
    """Every stock object type in the user's catalog, by category: drag one into the Scene, or select it and Place."""
    pf = S.prefab
    if S.catalog() is None:
        imgui.text_colored(theme.colour("ERROR"), "No catalog: set your ROM and the decomp in File > Settings.")
        imgui.text_wrapped(S.cat_error)
        return
    avail = imgui.get_content_region_avail()
    wide = avail.x > 2.2 * avail.y and avail.x > hello_imgui.em_size(36)  # docked along the bottom
    if wide:
        imgui.begin_child("list", imgui.ImVec2(avail.x * 0.55, 0))
    imgui.set_next_item_width(-hello_imgui.em_size(6.5))
    _, pf["search"] = imgui.input_text_with_hint("##search", "Search types", pf["search"])
    imgui.same_line()
    _, pf["used"] = imgui.checkbox("In stock", pf["used"])
    if imgui.is_item_hovered():
        imgui.set_tooltip("Only types that stock levels place")
    below = 0.0 if wide else _details_height(avail.x)
    imgui.begin_child("types", imgui.ImVec2(0, -below))
    if imgui.tree_node_ex("Groups", imgui.TreeNodeFlags_.default_open):
        for gid, (name, tip) in GROUPS.items():
            _prefab_row(gid, name, tip)
        imgui.tree_pop()
    search = pf["search"].lower()
    groups: dict[str, list] = {}
    for oid, t in S.objtypes.items():
        if pf["used"] and not t.get("uses"):
            continue
        schema = obj.schema_for_id(oid, S.schemas, S.behaviours)
        if search and search not in t["name"].lower() and search not in f"0x{oid:x}" and not (
                schema and search in schema.name.lower()):
            continue
        groups.setdefault(schema.category if schema else "Other (fields not decoded)", []).append((oid, t, schema))
    order = sorted(groups, key=lambda c: (c.startswith("Other"), c))
    if S.project and S.project.kinds:
        flags = imgui.TreeNodeFlags_.default_open
        if imgui.tree_node_ex(f"Pack kinds ({len(S.project.kinds)})##packkinds", flags):
            for k, (name, kind) in enumerate(sorted(S.project.kinds.items())):
                _prefab_row(KIND_BASE - k, f"{kind['name']}  (pack:kind/{name})",
                            f"Made from {kind['base']}: placed with its base's stock entry and fields")
            imgui.tree_pop()
    for category in order:
        flags = imgui.TreeNodeFlags_.default_open if search else 0
        if imgui.tree_node_ex(f"{category} ({len(groups[category])})##{category}", flags):
            for oid, t, schema in sorted(groups[category], key=lambda r: r[1]["name"].lower()):
                tip = (f"{schema.name}: {schema.description}" if schema else "Fields not decoded: placed as a copy "
                       "of a stock entry") + f"\nbehaviour {t['behaviour']}, used {t.get('uses', 0)} times"
                _prefab_row(oid, f"{t['name']}  0x{oid:X}", tip)
            imgui.tree_pop()
    imgui.end_child()
    if wide:
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child("details", imgui.ImVec2(0, 0))
        _prefab_details()
        imgui.end_child()
    else:
        imgui.separator()
        _prefab_details()


HINT = "Drag a type into the Scene, or select it and press Place."


def _details_height(width: float) -> float:
    """How much of a tall Prefabs window the selected entry's options take, so the list gets the rest."""
    row = imgui.get_frame_height_with_spacing()
    pid = S.prefab["oid"]
    text = HINT if pid is None else GROUPS[pid][1] if pid in (-2, -3) else "x" * 60 if pid <= KIND_BASE else ""
    wrapped = imgui.calc_text_size(text, False, max(width, 1.0)).y + imgui.get_style().item_spacing.y if text else 0
    rows = 0 if pid is None else 4 if pid == -1 else 1 if pid < 0 else 2
    return wrapped + rows * row + imgui.get_style().item_spacing.y * 2 + 2


def _prefab_row(pid: int, label: str, tip: str) -> None:
    if imgui.selectable(f"{label}##pf{pid}", S.prefab["oid"] == pid)[0]:
        S.prefab["oid"], S.prefab["example"] = pid, 0
    if imgui.is_item_hovered():
        imgui.set_tooltip(tip)
    if imgui.begin_drag_drop_source():
        imgui.set_drag_drop_payload_py_id(PREFAB_PAYLOAD, pid)
        imgui.text(f"Place {label}")
        imgui.end_drag_drop_source()


def _example_label(list_id: int, index: int) -> str:
    levels = S.list_levels.get(list_id, [])
    return f"list {list_id} #{index}" + (f" ({levels[0]})" if levels else "")


def _prefab_details() -> None:
    """The selected entry's options: which stock entry to start from, and Place."""
    pf = S.prefab
    pid = pf["oid"]
    if pid is None:
        _muted(HINT)
        return
    if pid == -1:
        squads = _squad_examples()
        labels = [_squad_label(e) for e in squads]
        if labels:
            _, pf["squad"] = imgui.combo("Squadron", min(pf["squad"], len(labels) - 1), labels)
        doors = _door_types()
        _, pf["door"] = imgui.combo("Door", min(pf["door"], len(doors)), ["no door"] + [
            f"{S.objtypes[d]['name']} 0x{d:X}" for d in doors])
        _, pf["members"] = imgui.slider_int("Members", pf["members"], 1, 8)
    elif pid >= 0:
        t = S.objtypes.get(pid, {})
        schema = obj.schema_for_id(pid, S.schemas, S.behaviours)
        options = [_example_label(*e) for e in t.get("examples", [])]
        if schema and schema.status == "decoded":
            options.append("defaults (no stock entry)")
        if options:
            _, pf["example"] = imgui.combo("Start from", min(pf["example"], len(options) - 1), options)
            if imgui.is_item_hovered():
                imgui.set_tooltip("A stock entry to copy (its fields not set in the Inspector keep the game's values)")
        else:
            imgui.text_colored(theme.colour("ERROR"), "No stock entry to start from.")
    elif pid <= KIND_BASE:
        _muted("One of the pack's kinds (Kinds window): placed with its base's first stock entry.")
    else:
        _muted(GROUPS[pid][1])
    if imgui.button("Place at view centre"):
        _place(pid, _ground_point(S.scene_rect[2] / 2, S.scene_rect[3] / 2, S.scene_rect[2:]))


def _squad_examples() -> list:
    """Stock squadrons whose members are list entries too (some kinds spawn members the lists never hold), one per
    baddy type."""
    if "squads" not in S.prefab:
        out, seen = [], set()
        for n, raw in enumerate(S.cat.pair(S.cat.levels_mod.OBJLIST_TABLE, S.cat.levels_mod.OBJLIST_DATA)):
            for i, e in enumerate(S.cat.levels_mod.objlist_entries(raw)):
                if e[0] == pre.SQUADRON_ID and len(e[1]) >= 0x38:
                    baddy = struct.unpack_from(">H", e[1], 0x36)[0]
                    if baddy not in seen and S.objtypes.get(baddy, {}).get("examples"):
                        seen.add(baddy)
                        S.stock_entries[(n, i)] = e[1]
                        out.append([n, i])
        S.prefab["squads"] = sorted(out, key=lambda e: S.type_name(struct.unpack_from(">H", S.stock_entries[
            tuple(e)], 0x36)[0]).lower())
    return S.prefab["squads"]


def _squad_label(example) -> str:
    entry = S.stock_entry(*example)
    if not entry or len(entry) < 0x38:
        return _example_label(*example)
    baddy = struct.unpack_from(">H", entry, 0x36)[0]
    return f"{S.type_name(baddy)} x{entry[0x0B]}  ({_example_label(*example)})"


def _door_types() -> list[int]:
    return sorted((oid for oid, t in S.objtypes.items() if t["behaviour"] == 7 and t.get("examples")),
                  key=lambda oid: -S.objtypes[oid].get("uses", 0))


def _ground_point(mx: float, my: float, size) -> list[float]:
    """Where the ray under a pixel of the Scene meets the level, or a point in front of the camera dropped to the
    floor."""
    ray_o, ray_d = vp.screen_ray(S.camera, size, mx, my)
    mesh = S.view.mesh
    t = vp.raycast(mesh.collision, ray_o, ray_d) if mesh is not None else None
    if t is not None:
        return list(ray_o + ray_d * t)
    p = list(S.camera.pos + S.camera.forward() * 500.0)
    y = mesh.floor_height(p[0], p[2]) if mesh is not None else None
    if y is not None:
        p[1] = y
    return p


def _place(pid: int, pos) -> None:
    """Adds a prefab to the open level (one undo step) and selects what it made."""
    h = S.history()
    if h is None:
        S.message = "Open a level first"
        return
    pf = S.prefab
    placer = pre.Placer(h.level, S.schemas, S.behaviours)
    try:
        if pid <= KIND_BASE:
            name, kind = sorted(S.project.kinds.items())[KIND_BASE - pid]
            base = refs.parse(kind["base"], "objtype").number()
            examples = S.objtypes.get(base, {}).get("examples", [])
            o = placer.single(base, pos, tuple(examples[0]) if examples else None, name=kind["name"])
            o["type"] = f"pack:kind/{name}"
            if not o.get("schema") and S.behaviours.get(base) == 24:
                o["schema"] = "squadmember"  # a squad member: give it a squadron in the Inspector
        elif pid == -1:
            squads = _squad_examples()
            if not squads:
                raise ValueError("the catalog has no stock squadron to start from")
            example = squads[min(pf["squad"], len(squads) - 1)]
            baddy = struct.unpack_from(">H", S.stock_entry(*example), 0x36)[0]
            member_examples = S.objtypes.get(baddy, {}).get("examples") or [None]
            doors = _door_types()
            door = doors[pf["door"] - 1] if 0 < pf["door"] <= len(doors) else None
            placer.squad(pos, example, baddy, member_examples[0], pf["members"], door,
                         S.objtypes[door]["examples"][0] if door is not None else None)
        elif pid == -2:
            placer.exit_pair(pos)
        elif pid == -3:
            placer.region(pos)
        else:
            examples = S.objtypes.get(pid, {}).get("examples", [])
            chosen = pf["example"] if pid == pf["oid"] else 0
            placer.single(pid, pos, tuple(examples[chosen]) if chosen < len(examples) else None,
                          name=S.type_name(pid))
    except ValueError as e:
        S.message = f"Can't place it: {e}"
        return
    what = "kind" if pid <= KIND_BASE else GROUPS[pid][0] if pid < 0 else S.type_name(pid)
    h.add(placer.made, f"place {what}")
    S.selection = {o["uid"] for o in placer.made}
    S.message = f"Placed {len(placer.made)} object{'s' if len(placer.made) > 1 else ''}"
    S.validate()


def console() -> None:
    if imgui.button("Validate"):
        S.validate()
    imgui.same_line()
    if S.project:
        errors = sum(p.severity == val.ERROR for p in S.problems)
        warnings = sum(p.severity == val.WARNING for p in S.problems)
        imgui.text(f"{errors} errors, {warnings} warnings")
    imgui.separator()
    for line in S.load_errors + S.errors:
        imgui.push_style_color(imgui.Col_.text, theme.colour("ERROR"))
        imgui.text_wrapped(line)
        imgui.pop_style_color()
    if S.play_log:
        imgui.separator_text("Play / build" + (" (running)" if S.playing else ""))
        for line in list(S.play_log):
            colour = "ERROR" if line.startswith("error") else "TEXT"
            imgui.push_style_color(imgui.Col_.text, theme.colour(colour))
            imgui.text_wrapped(line)
            imgui.pop_style_color()
        imgui.separator()
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
    if S.tool == "water":
        imgui.same_line()
        _, S.water_auto = imgui.checkbox("Auto height", S.water_auto)
        if imgui.is_item_hovered():
            imgui.set_tooltip("The surface goes 15 units above the floor where the drag starts")
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(5))
        imgui.begin_disabled(S.water_auto)
        _, S.water_height = imgui.input_float("##waterheight", S.water_height, 0, 0, "%.0f")
        imgui.end_disabled()
    imgui.same_line()
    imgui.text("|")
    imgui.same_line()
    imgui.begin_disabled(S.project is None or S.playing)
    if imgui.button("Building..." if S.playing else "Play"):
        play()
    imgui.end_disabled()
    if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip("Build the pack, install it in the port and start the game in this level (at the selected "
                          "start point, else start point 0). The output is in the Console")
    imgui.same_line()
    imgui.set_next_item_width(hello_imgui.em_size(5.5))
    if imgui.begin_combo("##hero", S.play_character.title()):
        for hero in ("juno", "vela", "lupus"):
            if imgui.selectable(hero.title(), hero == S.play_character)[0]:
                S.play_character = hero
        imgui.end_combo()
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
        if imgui.menu_item("Import geometry...", "", False, S.project is not None)[0]:
            import_geometry_dialog()
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
    if imgui.begin_menu("Help"):
        if imgui.menu_item("Open log folder", "", False)[0]:
            import os
            os.startfile(crashlog.log_dir())  # noqa: S606 (Windows: the folder in Explorer)
        imgui.end_menu()
    if imgui.begin_menu("Build"):
        if imgui.menu_item("Validate", "", False, S.project is not None)[0]:
            S.validate()
        if imgui.menu_item("Build pack", "", False, S.project is not None and not S.playing)[0]:
            play(launch=False)
        if imgui.menu_item("Play in game", "", False, S.project is not None and not S.playing)[0]:
            play()
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
        S.reset_catalog()
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
        _, d["glb"] = imgui.input_text("Geometry", d["glb"])
        imgui.same_line()
        if imgui.button("...##glb"):
            d["glb"] = (pfd.open_file("Your level from Blender", "", ["glTF binary", "*.glb"]).result()
                        or [d["glb"]])[0]
        imgui.text_disabled("optional: a .glb from Blender; Studio makes the first level on it")
        if imgui.button("Create") and d["folder"] and d["id"] and d["name"]:
            path = Path(d["folder"]) / f"{d['id'].split('.')[-1]}.jfgproj"
            try:
                create_project(path, d["id"], d["name"], d["glb"].strip() or None)
                imgui.close_current_popup()
            except proj.ProjectError as e:
                S.message = "; ".join(e.problems)
        imgui.same_line()
        if imgui.button("Cancel"):
            imgui.close_current_popup()
        imgui.end_popup()


def create_project(path: Path, mod_id: str, name: str, glb: str | None) -> None:
    """File > New project: the project, and with a .glb its geometry and a first level with a start point."""
    from jfgstudio import importer
    p = proj.create(path, mod_id, name)
    if glb:
        dest = importer.import_geometry(p, glb)
        from jfgstudio.gui.viewport import TrackMesh
        try:
            mesh = TrackMesh(str(dest))
        except Exception:  # noqa: BLE001 (the check below reports what is wrong with the file)
            mesh = None
        importer.first_level(p, dest.stem, mesh)
        p.save()
    S.open(str(path))
    if glb:
        check_geometry(dest)


def import_geometry_dialog() -> None:
    """File > Import geometry: copies a .glb into the project and checks it (the Console has the report)."""
    from jfgstudio import importer
    if S.project is None:
        return
    picked = (pfd.open_file("A level from Blender", "", ["glTF binary", "*.glb"]).result() or [None])[0]
    if not picked:
        return
    try:
        dest = importer.import_geometry(S.project, picked)
    except proj.ProjectError as e:
        S.message = "; ".join(e.problems)
        return
    if not S.project.levels:
        importer.first_level(S.project, dest.stem)
        S.level = next(iter(S.project.levels))
        S.planets_dirty = True  # a new level to save
    S.message = f"geometry/{dest.name} added: pick it as a level's track in the Level window"
    check_geometry(dest)


def check_geometry(path) -> None:
    """Runs the import check in the background; the report goes to the Console."""
    from jfgstudio import importer
    if S.playing:
        return
    S.play_log = [f"Import check: {Path(path).name}"]
    S.playing = True

    def run():
        try:
            if S.catalog() is None:
                S.play_log.append(f"error: no catalog to check against: {S.cat_error}")
                return
            report = importer.check(path, S.cat)
            S.play_log.extend(report.lines)
            S.message = f"{Path(path).name}: " + ("ready to build" if report.ok else "has problems (see the Console)")
        except Exception as e:  # noqa: BLE001 (logged; the editor keeps running)
            S.play_log.append(f"error: {type(e).__name__}: {e} (log: {crashlog.write(e, 'import check')})")
        finally:
            S.playing = False

    threading.Thread(target=run, daemon=True).start()


def play(launch: bool = True) -> None:
    """Builds (in the background, from a copy of the project as it is now) and, with `launch`, installs the pack and
    starts the game in the open level. The output goes to the Console."""
    from jfgstudio import build, formats, rom, testrun
    if S.project is None or S.playing:
        return
    start_point = 0
    for o in S.objects():
        if o["uid"] in S.selection and (sc := S.schema(o)) and sc.key == "startpoint":
            start_point = S.fields(o, sc).get("startPoint", 0)
    project = copy.deepcopy(S.project)
    level, hero = S.level, S.play_character
    S.play_log = [f"{'Play' if launch else 'Build'}: {project.meta['name']}"
                  + (f", level {level}, start point {start_point}, as {hero.title()}" if launch else "")]
    S.playing = True

    def run():
        try:
            if launch:
                testrun.play(project, level, start_point, hero, log=S.play_log.append)
            else:
                out = project.path / "build" / f"{project.id}.nrm"
                out.parent.mkdir(exist_ok=True)
                build.build(project, out, log=S.play_log.append)
            S.message = "Started the game" if launch else "Built the pack"
        except build.BuildError as e:
            S.play_log.extend(f"error: {p}" for p in e.problems)
            S.message = "The build failed: see the Console"
        except (testrun.TestRunError, formats.FormatsUnavailable, rom.RomError, OSError) as e:
            S.play_log.append(f"error: {e}")
            S.message = "Play failed: see the Console"
        except Exception as e:  # noqa: BLE001 (logged; the editor keeps running)
            path = crashlog.write(e, "Play")
            S.play_log.append(f"error: {type(e).__name__}: {e} (log: {path})")
            S.message = "Play failed: see the Console"
        finally:
            S.playing = False

    threading.Thread(target=run, daemon=True).start()


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
    h = S.history()
    if h and h.done and not imgui.is_mouse_down(imgui.MouseButton_.left):
        h.done[-1].merge = False  # a slider or colour drag ended: the next edit is a step of its own
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
        split("MainDockSpace", "BottomSpace", d.down, 0.3),
        split("MainDockSpace", "LeftSpace", d.left, 0.2),
        split("MainDockSpace", "RightSpace", d.right, 0.27),
    ]

    def win(label, space, fn):
        w = hello_imgui.DockableWindow()
        w.label, w.dock_space_name, w.gui_function = label, space, guarded(label, fn)
        return w

    params.dockable_windows = [
        win("Scene", "MainDockSpace", scene),
        win("Level Graph", "MainDockSpace", level_graph),
        win("Planet", "MainDockSpace", planet_tools.planet_window),
        win("Models", "MainDockSpace", model_viewer.models_window),
        win("Kinds", "RightSpace", labelled(kind_tools.kinds_window)),
        win("Triggers", "RightSpace", labelled(script_tools.triggers_window)),
        win("Scripts", "MainDockSpace", script_tools.scripts_window),
        win("Weapons", "RightSpace", labelled(script_tools.weapons_window)),
        win("Hierarchy", "LeftSpace", hierarchy),
        win("Inspector", "RightSpace", labelled(inspector)),
        win("Level", "RightSpace", labelled(level_tools.level_window)),
        win("Project", "BottomSpace", project_window),
        win("Console", "BottomSpace", console),
        win("Prefabs", "BottomSpace", prefabs),
    ]
    params.dockable_windows[-1].focus_window_at_next_frame = True
    focus = os.environ.get("JFGSTUDIO_FOCUS")  # tests and screenshots: bring that window to the front
    for w in params.dockable_windows:
        if focus and w.label == focus:
            w.focus_window_at_next_frame = True
    params.layout_condition = hello_imgui.DockingLayoutCondition.first_use_ever  # then the user's own layout
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
    crashlog.install()
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
    params.callbacks.show_menus = guarded("menu", menus)
    params.callbacks.show_status = guarded("status bar", status)
    toolbar_options = hello_imgui.EdgeToolbarOptions()
    toolbar_options.size_em = 2.6
    toolbar_options.window_bg = theme.colour("BACKGROUND")
    params.callbacks.add_edge_toolbar(hello_imgui.EdgeToolbarType.top, guarded("toolbar", toolbar), toolbar_options)

    def post_init():
        io = imgui.get_io()
        io.config_error_recovery = True  # a guarded window's error leaves ImGui's stacks for it to close
        io.config_error_recovery_enable_assert = False

    params.callbacks.post_init = post_init
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

    params.callbacks.show_gui = guarded("editor", gui)
    params.fps_idling.enable_idling = not screenshot
    hello_imgui.run(params)
    if screenshot:
        _write_png(screenshot, hello_imgui.final_app_window_screenshot())
    return 0
