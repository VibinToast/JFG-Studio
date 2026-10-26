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

from jfgstudio import __version__, crashlog, edit, refs, stock_edit
from jfgstudio import objects as obj
from jfgstudio import prefabs as pre
from jfgstudio import project as proj
from jfgstudio import validate as val
from jfgstudio.gui import icons, kind_tools, level_tools, model_viewer, planet_tools, script_tools, theme
from jfgstudio.gui import viewport as vp

gz = imguizmo.im_guizmo

TOOLS = (("hand", "Q", "Hand: pan and orbit the view"), ("move", "W", "Move"),
         ("rotate", "E", "Rotate: types with a facing field"), ("scale", "R", "Scale: types with a size field"),
         ("region", "G", "Region: click a navigation region's corners on the floor"),
         ("water", "T", "Water: drag a rectangle on the floor to add a water surface to the pack's geometry"),
         ("patrol", "P", "Patrol: click a squad patrol network's points; each links to the last"))
# Sprites the game keeps for its own debugging (a red ball, an "AI" badge, a small blue tag, a music note) on objects
# it never draws in play (triggers, region and patrol points, animators, texture scrolls, sound points): those objects
# get an icon in the Scene instead
DEBUG_SPRITES = {1, 38, 42, 43}


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
        self.tex_lists = {}  # (track, glb, mtime) -> the level's track texture list, None while it is read
        self.track_textures = None  # track_textures.TrackTextures, made with the catalog
        self.frame_on_open = False  # frame the selection once the level's Scene is shown (an exit's Go to)
        self.snap_floor = True
        self.drag = None  # uid -> start position, while the gizmo moves the selection
        self.scene_rect = (0.0, 0.0, 1.0, 1.0)
        self.pos_edit = None  # the Inspector's position before a drag of its fields
        self.gizmo_note = ""
        self.box_start = None  # where a box selection drag started (Scene pixels)
        self.region_draft: list[list[float]] = []  # the Region tool's corners so far
        self.region_hole = False  # the Region tool draws a hole (an obstacle) of the region around it
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
        self.squads: dict | None = None  # enemy -> its stock squads (squads.index), read on first use
        self.instances = []  # (uid, model path, matrix) drawn in the last frame, for picking
        self.menu_click = None  # where a right click in the Scene started (a menu, unless it drags)
        self.menu_uid = None  # the object the Scene's menu is for
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
        self.squads = None
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

    def sprite(self, oid: int, env: list | None = None) -> dict | None:
        """The catalog's picture of an object type the game draws as a sprite (Catalog.sprite; `env` recolours
        it), or None: a model type, a debug sprite, no catalog, or a jfgformats with no sprite reader."""
        key = ("sprite", oid, tuple(env or ()))
        if key not in self.model_paths:
            t = self.objtypes.get(oid) if self.catalog() else None
            info = None
            if t and t["kind"] == "sprites" and t["assets"] and t["assets"][0] not in DEBUG_SPRITES:
                try:
                    info = self.cat.sprite(t["assets"][0], env)
                except Exception:  # noqa: BLE001 (a sprite that can't be exported keeps the object's shape)
                    info = None
            self.model_paths[key] = info
        return self.model_paths[key]

    def level_textures(self) -> list[dict] | None:
        """The open level's track texture list (track_textures: what a texscroll's texture numbers), or None while
        it is being read (a pack track runs the importer: a few seconds, in the background) or can't be."""
        if self.catalog() is None or not self.level:
            return None
        track = self.project.levels[self.level].level["track"]
        glb = self.track_path() if track.startswith("pack:") else None
        if track.startswith("pack:") and glb is None:
            return None
        key = (track, glb, Path(glb).stat().st_mtime if glb else None)
        if key not in self.tex_lists:
            self.tex_lists[key] = None
            if self.track_textures is None:
                from jfgstudio.track_textures import TrackTextures
                self.track_textures = TrackTextures(self.cat)

            def read():
                try:
                    self.tex_lists[key] = self.track_textures.of(track, glb)
                except Exception as e:  # noqa: BLE001 (shown where the list is asked for)
                    self.tex_lists[key] = []
                    self.message = f"Can't read the level's textures: {e}"
            threading.Thread(target=read, daemon=True).start()
        return self.tex_lists[key]

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

    def game_objects(self, removed: bool = False) -> list[dict]:
        """For a stock-level edit: the game level's own objects (from the user's catalog) with the project's changes
        on them (stock_edit; uids "stock:<list>:<index>"); the removed ones only with `removed`."""
        if not self.project or self.level not in self.project.levels:
            return []
        level = self.project.levels[self.level]
        if level.base is None or self.catalog() is None:
            return []
        return [o for o in stock_edit.materialize(level, self.cat) if removed or not o.get("deleted")]

    def game_editable(self) -> bool:
        return bool(self.project and self.level in self.project.levels
                    and stock_edit.editable(self.project.levels[self.level]))

    def own_objects(self) -> list[dict]:
        if not self.project or self.level not in self.project.levels:
            return []
        return self.project.levels[self.level].objects["objects"]

    def all_objects(self) -> list[dict]:
        """The level's own objects and the game level's (shown, and selectable to look at, even when read-only)."""
        return self.own_objects() + self.game_objects()

    def type_name(self, oid: int) -> str:
        t = self.objtypes.get(oid) if self.catalog() else None
        return t["name"] if t else f"0x{oid:X}"

    def objects(self) -> list[dict]:
        """What the tools edit: the level's own objects, and the game level's too when they are editable."""
        return self.own_objects() + (self.game_objects() if self.game_editable() else [])


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


def _squad_title(o: dict) -> str | None:
    """A squadron by what it holds: "Tribal (<type>)" for a Tribal to rescue, else "<enemy> squad"."""
    schema = S.schema(o)
    if schema is None or schema.key != "squadron":
        return None
    baddy = S.fields(o, schema).get("baddyType")
    if not baddy:
        return None
    name = S.type_name(baddy)
    return f"Tribal ({name})" if baddy in pre.TRIBAL_TYPES else f"{name} squad"


def _short_label(o: dict) -> str:
    """An object's name, or its type's (the Scene's labels)."""
    if o.get("name"):
        return o["name"]
    oid = obj.stock_id(o)
    if stock_edit.is_stock(o["uid"]) and oid is not None:
        return _squad_title(o) or S.type_name(oid)
    return o["uid"]


def _object_label(o: dict) -> str:
    if o["type"].startswith("pack:kind/") and S.project:
        kind = S.project.kinds.get(o["type"].split("/", 1)[1])
        if kind:
            return f"{o.get('name') or kind['name']}  ##{o['uid']}"
    oid = obj.stock_id(o)
    schema = S.schema(o)
    kind = _squad_title(o) or (S.type_name(oid) if oid is not None else
                               schema.name if schema else o["type"].split("/", 1)[-1])
    return f"{o.get('name') or kind}  ##{o['uid']}"


def _select(uid: str) -> None:
    if imgui.get_io().key_ctrl:
        S.selection ^= {uid}
    else:
        S.selection = {uid}


def hierarchy() -> None:
    objs = S.own_objects()
    if not S.project:
        _muted("No project open. File > Open project...")
        return
    level = S.project.levels.get(S.level)
    if level is not None and level.base is not None:
        _game_objects_header(level)
    if not objs:
        imgui.text_disabled("No objects added yet." if level is not None and level.base is not None
                            else "This level has no objects.")
    else:
        _own_tree(objs)
    if level is not None and level.base is not None:
        _game_tree()


def _game_objects_header(level) -> None:
    """The switch that lets the game level's own objects be edited, for a stock-level edit."""
    h = S.history()
    on = stock_edit.editable(level)
    changed, value = imgui.checkbox("Edit the game level's objects", on)
    if changed and h is not None:
        h.set_value(edit.LEVEL, ("editStock",), True if value else None,
                    "edit game objects" if value else "lock game objects")
        if not value:
            S.selection = {u for u in S.selection if not stock_edit.is_stock(u)}
    if imgui.is_item_hovered():
        theme.tip("Off: the game level's objects are read-only (listed grey); what you place is added to the "
                          "level.\nOn: move, change and remove them too. The pack carries only your changes (the "
                          "port's level patches), and Revert puts an object back as the game has it.")
    if S.catalog() is None:
        _muted("The game level's objects need your ROM (File > Settings).")
    imgui.separator()


def _game_tree() -> None:
    game = S.game_objects(removed=True)
    if not game:
        return
    live = [o for o in game if not o.get("deleted")]
    gone = [o for o in game if o.get("deleted")]
    edited = sum(stock_edit.changed(o) for o in game)
    title = f"The game level's objects ({len(live)}{f', {edited} changed' if edited else ''})##game"
    if imgui.tree_node_ex(title, imgui.TreeNodeFlags_.default_open):
        locked = not S.game_editable()
        for o in live:
            _leaf(o, dim=locked, marked=stock_edit.changed(o))
        if gone and imgui.tree_node_ex(f"Removed ({len(gone)})##gone"):
            for o in gone:
                _leaf(o, dim=True)
            imgui.tree_pop()
        imgui.tree_pop()


def _own_tree(objs) -> None:
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
        if imgui.is_item_clicked() or imgui.is_item_clicked(imgui.MouseButton_.right) \
                and squad["uid"] not in S.selection:
            _select(squad["uid"])
        if imgui.begin_popup_context_item(f"menu##{squad['uid']}"):
            _object_menu(squad)
            imgui.end_popup()
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


def _leaf(o: dict, dim: bool = False, marked: bool = False) -> None:
    flags = imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
    if o["uid"] in S.selection:
        flags |= imgui.TreeNodeFlags_.selected
    if dim:
        imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    label = _object_label(o)
    if marked:  # a game object the project changed
        name, _, tag = label.partition("  ##")
        label = f"{name} *  ##{tag}"
    imgui.tree_node_ex(label, flags)
    if dim:
        imgui.pop_style_color()
    if imgui.is_item_clicked() or imgui.is_item_clicked(imgui.MouseButton_.right) and o["uid"] not in S.selection:
        _select(o["uid"])
    if imgui.begin_popup_context_item(f"menu##{o['uid']}"):
        _object_menu(o)
        imgui.end_popup()


def _object_menu(o: dict) -> None:
    """The right-click menu of an object (the Hierarchy's and the Scene's): what makes sense for it."""
    h = S.history()
    game = stock_edit.is_stock(o["uid"])
    writable = h is not None and (not game or S.game_editable())
    removed = bool(o.get("deleted"))
    imgui.text_disabled(_short_label(o))
    imgui.separator()
    if imgui.menu_item("Frame in Scene", "F", False, not removed)[0]:
        S.selection = {o["uid"]}
        frame_selection()
    sc = S.schema(o)
    if sc and sc.key in ("squadron", "squadmember"):
        if imgui.menu_item("Select the whole squad", "", False, True)[0]:
            S.selection = _squad_uids(o)
    if removed:
        if imgui.menu_item("Put back in the level", "", False, writable)[0]:
            h.set_value(o["uid"], ("deleted",), None, "put back")
            S.validate()
        return
    imgui.separator()
    if game:
        if imgui.menu_item("Duplicate" if writable else "Copy into the level", "Ctrl+D", False, h is not None)[0]:
            S.selection = set(h.duplicate({o["uid"]}))
            S.validate()
        if imgui.is_item_hovered() and not writable:
            theme.tip("A copy of it as one of the objects you add: the game's own stays as it is")
    elif imgui.menu_item("Duplicate", "Ctrl+D", False, writable)[0]:
        S.selection = set(h.duplicate(S.selection | {o["uid"]}))
        S.validate()
    if imgui.menu_item("Drop to floor", "", False, writable and S.view.mesh is not None)[0]:
        y = S.view.mesh.floor_height(o["pos"][0], o["pos"][2], below=o["pos"][1] + 60)
        if y is not None:
            h.set_value(o["uid"], ("pos",), [o["pos"][0], obj.on_ground(y, sc), o["pos"][2]], "drop to floor")
    if sc and sc.key == "squadron" and imgui.menu_item("Add cover", "", False, writable and S.view.mesh is not None)[0]:
        level_tools.add_cover(o)
    if imgui.menu_item("Remove from the level" if game else "Delete", "Del", False, writable)[0]:
        if o["uid"] not in S.selection:
            S.selection = {o["uid"]}
        delete()
    if game:
        imgui.separator()
        if imgui.menu_item("Revert to the game's", "", False, writable and stock_edit.changed(o))[0]:
            _revert_game([o])
        level = S.project.levels[S.level]
        if imgui.menu_item("Edit the game level's objects", "", stock_edit.editable(level), h is not None)[0]:
            on = not stock_edit.editable(level)
            h.set_value(edit.LEVEL, ("editStock",), True if on else None,
                        "edit game objects" if on else "lock game objects")


def _squad_uids(o: dict) -> set[str]:
    """A squadron or member's whole squad: the squadron, its members and its region's corners."""
    objs = S.all_objects()
    keys = _keys(objs)
    sid = o.get("fields", {}).get("squadronId" if keys[o["uid"]] == "squadron" else "squadron")
    sq = next((x for x in objs if keys[x["uid"]] == "squadron" and S.fields(x, S.schema(x)).get("squadronId") == sid),
              None)
    if sq is None:
        return {o["uid"]}
    region = S.fields(sq, S.schema(sq)).get("region")
    out = {sq["uid"]}
    for x in objs:
        f = S.fields(x, S.schema(x)) if keys[x["uid"]] in ("squadmember", "regionpoint") else {}
        if keys[x["uid"]] == "squadmember" and f.get("squadron") == sid or \
                keys[x["uid"]] == "regionpoint" and region is not None and f.get("region") == region:
            out.add(x["uid"])
    return out


def _revert_game(objs: list[dict]) -> None:
    """Game objects back as the game has them, as one undo step."""
    h = S.history()
    changes = []
    for o in objs:
        g = o["game"]
        for path, old, new in ((("pos",), list(o["pos"]), list(g["pos"])),
                               (("fields",), dict(o.get("fields", {})), {}),
                               (("name",), o.get("name"), None), (("deleted",), o.get("deleted"), None)):
            if old != new and not (old in (None, {}) and new in (None, {})):
                changes.append(edit.Change(o["uid"], path, old, new))
    h.do(edit.Command("revert to the game's", changes))
    S.validate()


def _keys(objs) -> dict:
    return {o["uid"]: (sc.key if (sc := S.schema(o)) else None) for o in objs}


def _squad_sizes(objs) -> dict[int, float]:
    """squadronId -> the size its members are drawn at (entry +0x15 / 100, 0 = 1.0; it replaces their definition's
    scale), with a kind's Size on top as the build writes it."""
    out = {}
    for o in objs:
        schema = S.schema(o)
        if schema is None or schema.key != "squadron":
            continue
        values = S.fields(o, schema)
        size = (values.get("size", 0) or 100) / 100.0
        kind_ref = (o.get("kindFields") or {}).get("baddyType")
        if kind_ref and kind_ref.startswith("pack:kind/"):
            size *= S.project.kinds.get(kind_ref.split("/", 1)[1], {}).get("scale", 1.0)
        out[values.get("squadronId")] = size
    return out


def _matrix(o: dict, schema: obj.Schema | None, oid: int | None, squad_sizes: dict | None = None) -> np.ndarray:
    """Where an object's model goes: its position, the facing and size its entry gives, the definition's scale (a
    squad member: its squadron's size instead)."""
    values = S.fields(o, schema)
    angles = {}
    scale = (S.objtypes.get(oid, {}).get("scale") or 1.0) if oid is not None else 1.0
    if schema and schema.key == "squadmember" and squad_sizes and values.get("squadron") in squad_sizes:
        scale = squad_sizes[values["squadron"]]
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
    squad_sizes = _squad_sizes(objs)
    with_members = {S.fields(o, sc).get("squadron") for o in objs
                    if (sc := S.schema(o)) is not None and sc.key == "squadmember"}
    for o in objs:
        oid = kind_tools.base_oid(o)
        schema = S.schema(o)
        if oid is None:
            continue
        key = schema.key if schema else None
        values = S.fields(o, schema) if key == "squadron" else {}
        if key == "squadron" and values.get("squadronId") not in with_members and values.get("baddyType"):
            # It spawns its own members (flyers, drones, a Tribal): drawn as its enemy, at the squad's size
            baddy = values["baddyType"]
            path = S.model_path(baddy)
            mesh = S.view.model(path) if path else None
            if mesh is None or not mesh.parts:
                continue
            yaw_f = schema.role("yaw")
            yaw = obj.angle_of(yaw_f, values.get(yaw_f.name, yaw_f.default)) if yaw_f else 0.0
            m = vp.object_matrix([float(v) for v in o["pos"]], yaw=yaw,
                                 scale=squad_sizes.get(values.get("squadronId"), 1.0))
        else:
            # a hidden type (the game draws nothing for it) shows only as its shape, if it has one
            path = None if schema and schema.hidden else S.model_path(oid)
            sprite = None if path or (schema and schema.hidden) else S.sprite(
                oid, schema.env_colour(S.fields(o, schema)) if schema else None)
            if sprite:  # the game's sprite, turned to face the camera
                path = f"sprite:{sprite['png']}"
                if path not in S.view.models:
                    S.view.models[path] = vp.SpriteMesh(sprite)
            shape = path is None and key in vp.SHAPES  # no model or sprite to draw: a shape stands in
            if shape:
                path = f"shape:{vp.SHAPES[key]}:{key}"
            mesh = S.view.model(path) if path else None
            if mesh is None or not mesh.parts:
                continue
            m = _matrix(o, schema, None if shape else oid, squad_sizes)  # a shape is its own size, not the type's
            if sprite:
                m = vp.billboard(m[:3, 3], float(np.linalg.norm(m[:3, 0])), S.camera)
        if o["type"].startswith("pack:kind/") and not (schema and schema.key == "squadmember"):
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
        if os.environ.get("JFGSTUDIO_FRAME") or S.frame_on_open:  # screenshots, Go to: framed on the selection
            frame_selection()
            S.frame_on_open = False
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
    _scrolls(S.all_objects())
    stock = [] if S.game_editable() else S.game_objects()  # read-only game objects: drawn grey, not editable
    if stock:
        stock_keys = _keys(stock)
        stock_inst, stock_boxes = _instances(stock, pickable=False)
        instances = instances + [(p, m, (0.25, 0.3, 0.4, 0.45)) for p, m, _ in stock_inst]
        lines = vp.marker_lines(objs + stock, {**keys, **stock_keys}, S.selection,
                                vp.region_links(objs + stock, {**keys, **stock_keys}) + _camera_links(objs + stock),
                                {**boxes, **stock_boxes}, _rings(objs))
    else:
        lines = vp.marker_lines(objs, keys, S.selection, vp.region_links(objs, keys) + _camera_links(objs), boxes,
                                _rings(objs))
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
    _icons(draw, origin, w, h, objs, keys, {**boxes, **stock_boxes} if stock else boxes, stock,
           stock_keys if stock else {})
    gap = icons.radius() + 4
    for o in objs:
        p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + vp.MARKER_HEIGHT, o["pos"][2]))
        p = (p[0] + gap - 4, p[1] + 7) if p else None  # beside its icon
        if o["uid"] in boxes:
            top = max(c[1] for c in boxes[o["uid"]])
            p = vp.project(S.camera, (w, h), (o["pos"][0], top + 6, o["pos"][2])) if o["uid"] in S.selection else None
        quiet = stock_edit.is_stock(o["uid"]) and o["uid"] not in S.selection and not o.get("name")
        if p and keys.get(o["uid"]) != "regionpoint" and not quiet:  # the game's own: named when selected
            colour = theme.colour("HIGHLIGHT" if o["uid"] in S.selection else "MUTED")
            draw.add_text(imgui.ImVec2(origin.x + p[0] + 4, origin.y + p[1] - 14), imgui.get_color_u32(colour),
                          _short_label(o))
    for o in S.all_objects():
        if o["uid"] in S.selection and (sc := S.schema(o)) and sc.key == "doorswitch":
            door = pre.light_door(o, _doors())[0]
            a = vp.project(S.camera, (w, h), o["pos"])
            b = vp.project(S.camera, (w, h), door["pos"]) if door else None
            if a and b:
                draw.add_line(imgui.ImVec2(origin.x + a[0], origin.y + a[1]),
                              imgui.ImVec2(origin.x + b[0], origin.y + b[1]),
                              imgui.get_color_u32(theme.colour("HIGHLIGHT")), 2.0)
    if S.tool in ("region", "water", "patrol"):
        if S.drag is not None:
            _end_drag(objs)
        level_tools.scene_tool(origin, w, h, hovered)
        _camera_input(hovered, objs)
        return
    _scene_menu(origin, w, h, objs, hovered)
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


def _camera_links(objs) -> list:
    """Camera objects' frustums (which way each shot looks), and from a selected cutscene camera a line to every
    object that plays it (a door lock light's cutscene with its id)."""
    out = []
    for o in objs:
        schema = S.schema(o)
        if schema is None or schema.key not in vp.CAMERAS or o.get("deleted"):
            continue
        values = S.fields(o, schema)
        angles = [obj.angle_of(schema.field(n), values.get(n, 0)) for n in ("yaw", "pitch", "roll")]
        fwd, up = vp.camera_view(schema.key, *angles)
        selected = o["uid"] in S.selection
        colour = vp.SELECTED_COLOUR if selected else vp.KIND_COLOURS[schema.key]
        out += vp.camera_lines(o["pos"], fwd, up, colour)
        if selected and schema.key == "cutcamera" and values.get("id"):
            for x in objs:
                sx = S.schema(x)
                if sx is not None and sx.field("cutscene") and S.fields(x, sx).get("cutscene") == values["id"]:
                    out.append((tuple(o["pos"]), tuple(x["pos"]), vp.SELECTED_COLOUR))
    return out


def _icons(draw, origin, w, h, objs, keys, boxes, stock, stock_keys) -> None:
    """Every object drawn without a model gets an icon on its post (gui/icons.py), the selected ones on top; the
    game's read-only objects dimmed."""
    marks = []
    for o, key, dim in [(o, keys.get(o["uid"]), False) for o in objs] + [(o, stock_keys.get(o["uid"]), True)
                                                                         for o in stock]:
        if o["uid"] in boxes or key == "regionpoint" or o.get("deleted"):
            continue
        lift = 0.0 if key in vp.CAMERAS else vp.MARKER_HEIGHT  # a camera's icon is where the shot is taken
        p = vp.project(S.camera, (w, h), (o["pos"][0], o["pos"][1] + lift, o["pos"][2]))
        if p:
            oid = kind_tools.base_oid(o)
            marks.append((o["uid"] in S.selection, p, icons.kind(key, S.behaviours.get(oid)),
                          vp.KIND_COLOURS.get(key, vp.OTHER_COLOUR), dim))
    for selected, p, icon, colour, dim in sorted(marks, key=lambda m: m[0]):
        icons.draw(draw, origin.x + p[0], origin.y + p[1], icon, colour, selected, dim)


def _scene_menu(origin, w, h, objs, hovered: bool) -> None:
    """A right click (not a drag: that flies the camera) on an object opens its menu."""
    io = imgui.get_io()
    if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.right):
        S.menu_click = (io.mouse_pos.x, io.mouse_pos.y)
    if imgui.is_mouse_released(imgui.MouseButton_.right) and S.menu_click is not None:
        x0, y0 = S.menu_click
        S.menu_click = None
        if math.hypot(io.mouse_pos.x - x0, io.mouse_pos.y - y0) < 4:
            uid = _hit(origin, w, h, objs)
            if uid is not None:
                if uid not in S.selection:
                    S.selection = {uid}
                S.menu_uid = uid
                imgui.open_popup("scene menu")
    if imgui.begin_popup("scene menu"):
        o = next((x for x in S.all_objects() if x["uid"] == S.menu_uid), None)
        if o is None:
            imgui.close_current_popup()
        else:
            _object_menu(o)
        imgui.end_popup()


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
    """Selects what is under the mouse (_hit), or clears the selection."""
    best = _hit(origin, w, h, objs)
    if best is None:
        if not (imgui.get_io().key_ctrl or imgui.get_io().key_shift):
            S.selection = set()
    else:
        _select(best)


def _hit(origin, w, h, objs) -> str | None:
    """What is under the mouse: a marker within 14 pixels, else the nearest model the ray hits."""
    mouse = imgui.get_mouse_pos()
    mx, my = mouse.x - origin.x, mouse.y - origin.y
    best, best_d = None, (icons.radius() + 3) ** 2
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
    return best


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
                        p[1] = obj.on_ground(y, S.schema(o))
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
    sel = [o["pos"] for o in S.all_objects() if o["uid"] in S.selection]
    if sel:
        S.camera.frame(np.mean(sel, axis=0), 500.0)
    elif S.view.mesh is not None:
        S.camera.look_from_above(S.view.mesh.bounds)


def inspector() -> None:
    sel = [o for o in S.all_objects() + S.game_objects(removed=True) if o["uid"] in S.selection]
    sel = list({o["uid"]: o for o in sel}.values())
    h = S.history()
    if not sel or h is None:
        _muted("Select an object in the Hierarchy or the Scene.")
        return
    game = [o for o in sel if stock_edit.is_stock(o["uid"])]
    locked = bool(game) and not S.game_editable()
    if game:
        _muted("One of the game level's own objects: read-only. Turn on \"Edit the game level's objects\" in the "
               "Hierarchy to change it, or right-click > Copy into the level." if locked else
               "One of the game level's own objects: the pack carries only what you change.")
        if len(game) == 1 and stock_edit.changed(game[0]):
            imgui.begin_disabled(locked)
            if imgui.button("Revert to the game's"):
                _revert_game(game)
            imgui.end_disabled()
    light = len(sel) == 1 and (sc := S.schema(sel[0])) is not None and sc.key == "doorswitch"
    if light and locked:
        _light_link(sel[0])  # its links select the door and squad even while the light is read-only
    imgui.begin_disabled(locked)
    try:
        _inspector_body(sel, h, light_shown=light and locked)
    finally:
        imgui.end_disabled()


def _inspector_body(sel: list[dict], h, light_shown: bool = False) -> None:
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
                h.set_value(uid, ("pos",), [o["pos"][0], obj.on_ground(y, S.schema(o)), o["pos"][2]], "drop to floor")
        if imgui.is_item_hovered():
            theme.tip("Onto the floor below, a little above it (enemies and start points a bit more), so it "
                              "doesn't spawn inside a slope and fall through")
    if schema and schema.key == "doorswitch" and not light_shown:
        _light_link(o)
    if schema and schema.key == "squadron":
        imgui.begin_disabled(S.view.mesh is None)
        if imgui.button("Add cover"):
            level_tools.add_cover(o)
        imgui.end_disabled()
        if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
            theme.tip("Makes the blocks, crates and pillars inside the squad's region holes of it: smart squad "
                              "members only move to the corners of holes and inward corners of their region, and "
                              "stand and fire without them. Draw one by hand with the Region tool's Hole mode")
        imgui.same_line()
        if imgui.button("Patrol"):
            level_tools.make_patrol(o)
        if imgui.is_item_hovered():
            theme.tip("Walks the patrol points nearest to it (the Patrol tool, P) and fights once it sees the "
                              "hero: patrol mode (kind 6), the game's own patrol squad of its enemy copied, behaviour "
                              "2, alert and hurt behaviour 0x11. Keep a patrol point within 300 of it")
    is_exit = bool(schema and schema.key == "exit")
    if is_exit and imgui.collapsing_header("Destination", imgui.TreeNodeFlags_.default_open):
        level_tools.exit_picker(o)
    if schema and imgui.collapsing_header(schema.name, imgui.TreeNodeFlags_.default_open):
        if schema.status != "decoded":
            imgui.push_style_color(imgui.Col_.text, theme.colour("HIGHLIGHT"))
            imgui.text_wrapped("Partly decoded: other fields come from the stock entry.")
            imgui.pop_style_color()
        values = S.fields(o, schema)
        if schema.key == "texscroll":
            _texture_picker(uid, int(values.get("texture", 0)), "texture" in o.get("fields", {}))
        for f in schema.fields:
            if is_exit and f.link in ("level", "startPoint"):
                continue  # the Destination picker sets these
            if schema.key == "texscroll" and f.name == "texture":
                continue  # _texture_picker
            _field_input(uid, f, int(values.get(f.name, f.default)), f.name in o.get("fields", {}))
    if imgui.collapsing_header("Entry"):
        imgui.text(f"size 0x{o['size']:X}" if "size" in o else "size from the stock base")
        if o.get("stock"):
            imgui.text(f"stock base: list {o['stock']['list']}, entry {o['stock']['entry']}")
        if o.get("params"):
            imgui.text_wrapped(f"raw: {o['params']}")


def _doors() -> list[dict]:
    return [o for o in S.all_objects() if (sc := S.schema(o)) and sc.key == "door"]


def _light_link(o: dict) -> None:
    """A door lock light's door, and what locks that door: the light has no door id of its own, it shows the nearest
    door, which a squad, a switch or a key keeps locked (decomp MSR "Door switch")."""
    if not imgui.collapsing_header("Lights up for", imgui.TreeNodeFlags_.default_open):
        return
    door, dist, second = pre.light_door(o, _doors())
    if door is None:
        imgui.text_colored(theme.colour("ERROR"), "No door in the level: this light never changes.")
        return
    _muted("The light has no setting of its own: it shows the nearest door, and lights up once nothing keeps that "
           "door locked. Move it to pick another door.")
    if imgui.selectable(f"Door: {_short_label(door)}  ({dist:.0f} away)##lightdoor", False)[0]:
        S.selection = {door["uid"]}
    if second < dist * pre.LIGHT_TIE:
        imgui.text_colored(theme.colour("HIGHLIGHT"), f"Another door is nearly as close ({second:.0f}): move the "
                                                      "light nearer its own.")
    lockers = pre.door_lockers(S.fields(door, S.schema(door)), S.all_objects(), lambda x: _keys([x])[x["uid"]],
                               lambda x: S.fields(x, S.schema(x)))
    if not lockers:
        imgui.text_colored(theme.colour("HIGHLIGHT"), "Nothing locks that door, so the light is on from the start. "
                                                      "Give the door a squad (its door id) or a switch.")
    for kind, x in lockers:
        if kind == "key":
            imgui.text(f"Locked by: key (item {x}), in the door's Key lock")
        elif imgui.selectable(f"Locked by {'squad' if kind == 'squad' else 'switch'}: {_short_label(x)}"
                              f"##lock{x['uid']}", False)[0]:
            S.selection = _squad_uids(x) if kind == "squad" else {x["uid"]}
    if imgui.is_item_hovered():
        theme.tip("Click to select it. A squad locks every door with its door id until all its members are "
                          "dead; set the door id on the squadron (Inspector) to tie another squad to this door.")


def _texture_picker(uid: str, value: int, own: bool) -> None:
    """A texscroll's texture: one of the level's track textures, picked by its picture. Its surfaces are
    highlighted in the Scene while it is selected."""
    textures = S.level_textures()
    if textures is None:
        _muted(f"texture {value}: reading the level's textures...")
        return
    if not textures:
        _muted(f"texture {value}: the level has no textures to list")
        return
    k = min(max(value, 0), len(textures) - 1)  # the game clamps it

    def thumb(t, size):
        glo = S.view.thumbnail_rgba(("tracktex", id(textures), t["index"]), t["width"], t["height"], t["rgba"])
        imgui.image(imgui.ImTextureRef(glo), imgui.ImVec2(size, size))

    size = imgui.get_frame_height()
    thumb(textures[k], size)
    imgui.same_line()
    label = f"texture {value}" + (f"  (the game uses {k})" if k != value else "")
    if not own:
        imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    opened = imgui.begin_combo("texture", label, imgui.ComboFlags_.height_large)
    if not own:
        imgui.pop_style_color()
    if imgui.is_item_hovered():
        theme.tip("Which of the level's textures it scrolls (an index into the track's texture list). Its surfaces "
                  "glow in the Scene while this is selected, and every texscroll's surfaces move there as in the game")
    if opened:
        for t in textures:
            thumb(t, size * 1.6)
            imgui.same_line()
            if imgui.selectable(f"texture {t['index']}  ({t['width']} x {t['height']})##tex{t['index']}",
                                t["index"] == value, 0, imgui.ImVec2(0, size * 1.6))[0]:
                S.history().set_value(uid, ("fields", "texture"), t["index"], "texscroll texture")
                S.validate()
        imgui.end_combo()


def _scrolls(objs: list[dict]) -> None:
    """The level's texscrolls for the Scene: the speed each moves its texture at (summed, as the game adds them),
    and the surfaces of the selected ones."""
    scrolls = [o for o in objs if (sc := S.schema(o)) is not None and sc.key == "texscroll" and not o.get("deleted")]
    S.view.scrolls, S.view.scroll_marked = {}, set()
    textures = S.level_textures() if scrolls else None
    if not textures:
        return
    from jfgstudio.track_textures import scroll_rate
    for o in scrolls[:16]:  # the game drops the 17th and later
        values = S.fields(o, S.schema(o))
        t = textures[min(max(int(values.get("texture", 0)), 0), len(textures) - 1)]
        du, dv = S.view.scrolls.get(t["rgba"], (0.0, 0.0))
        S.view.scrolls[t["rgba"]] = (du + scroll_rate(values.get("uSpeed", 0), t["width"]),
                                     dv + scroll_rate(values.get("vSpeed", 0), t["height"]))
        if o["uid"] in S.selection:
            S.view.scroll_marked.add(t["rgba"])


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
        theme.tip(tip)


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
VARIANT_BASE = 1 << 20  # prefab ids from here: a type with a field set (oid << 4 | value), _split()
UNCOUNTED = {"Ammo", "Health", "Breakable glass"}  # kind names that are their own plural
# Types with no fields decoded, by behaviour (the decomp's behaviour table, by its init functions): the category and
# fold Prefabs lists them under. They are placed as copies of a stock entry, like the rest of Other.
BEHAVIOUR_FOLDS = {
    43: ("Pickups", "Weapon pickups"), 80: ("Pickups", "Power-ups"), 81: ("Pickups", "Power-ups"),
    103: ("Pickups", "Robot parts"), 134: ("Pickups", "Gems"),
    55: ("Props", "Bridges"), 3: ("Props", "Swaying scenery"), 4: ("Props", "Swaying scenery"),
    5: ("Props", "Swaying scenery"), 91: ("Props", "Breakable crates"), 36: ("Props", "Shard clusters"),
    57: ("Lights & effects", "Fires and animated sprites"), 58: ("Lights & effects", "Fires and animated sprites"),
    62: ("Lights & effects", "Fires and animated sprites"), 85: ("Lights & effects", "Fires and animated sprites"),
    32: ("Lights & effects", "Sprays and bubbles"), 99: ("Lights & effects", "Fireflies"),
    11: ("Lights & effects", "Weather"), 12: ("Lights & effects", "Lens flares"),
    127: ("Lights & effects", "Light shows"), 132: ("Lights & effects", "Spotlights"),
    145: ("Lights & effects", "Fireworks"), 105: ("Lights & effects", "Sound points"),
    148: ("Lights & effects", "Sound points"),
    17: ("Cameras", "Fixed cameras"), 100: ("Cameras", "Cutscene cameras"), 70: ("Cameras", "Menu cameras"),
    71: ("Cameras", "Menu cameras"),
    20: ("Creatures", "Fish"), 21: ("Creatures", "Frogs"), 22: ("Creatures", "Butterflies"), 87: ("Creatures", "Grubs"),
    90: ("Characters", "Hint givers"), 93: ("Characters", "Sidekick pads"), 124: ("Characters", "Transformers"),
    125: ("Characters", "Terminals"),
    128: ("Doors & switches", "Night-vision pads"), 129: ("Doors & switches", "Blast doors"),
    102: ("Doors & switches", "Robot targets"),
    106: ("Bosses", "Boss setup"), 107: ("Bosses", "Boss setup"),
    137: ("Bosses", "Boss parts"), 138: ("Bosses", "Boss parts"), 142: ("Bosses", "Boss parts"),
    121: ("Races & multiplayer", "Race checkpoints"), 122: ("Races & multiplayer", "Race pickups"),
    123: ("Races & multiplayer", "Race obstacles"), 119: ("Races & multiplayer", "Targets"),
    120: ("Races & multiplayer", "Targets"), 143: ("Races & multiplayer", "Arcade machines"),
    92: ("Races & multiplayer", "Character select"), 72: ("Races & multiplayer", "Character select"),
    136: ("Enemies & navigation", "Region ids"), 149: ("Enemies & navigation", "AI points"),
    27: ("Enemies & navigation", "AI junctions"),
}
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
    _, pf["used"] = imgui.checkbox("Placeable", pf["used"])
    if imgui.is_item_hovered():
        theme.tip("Only types Studio can place: decoded ones, and ones with a stock entry to copy (their own, or "
                  "one of a type that shares their behaviour)")
    below = 0.0 if wide else _details_height(avail.x)
    imgui.begin_child("types", imgui.ImVec2(0, -below))
    if imgui.tree_node_ex("Groups", imgui.TreeNodeFlags_.default_open):
        for gid, (name, tip) in GROUPS.items():
            _prefab_row(gid, name, tip)
        imgui.tree_pop()
    search = pf["search"].lower()
    groups: dict[str, list] = {}
    for oid, t in S.objtypes.items():
        schema = obj.schema_for_id(oid, S.schemas, S.behaviours)
        if pf["used"] and not t.get("examples") and not (schema and schema.status == "decoded"):
            continue
        category, fold = _prefab_place(t, schema)
        if search and search not in t["name"].lower() and search not in f"0x{oid:x}" and not (
                schema and search in schema.name.lower()) and search not in (fold or "").lower():
            continue
        groups.setdefault(category, []).append((oid, t, schema, fold))
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
            folds: dict[str | None, list] = {}
            for row in groups[category]:
                folds.setdefault(row[3], []).append(row)
            # kinds of two or more get a fold each, unless one kind is the whole category
            folded = {f for f in folds if f and 1 < len(folds[f]) < len(groups[category])}
            for fold in sorted(folded):
                if imgui.tree_node_ex(f"{fold} ({len(folds[fold])})##{category}/{fold}", flags):
                    _prefab_rows(folds[fold])
                    imgui.tree_pop()
            _prefab_rows([r for f, rows in folds.items() if f not in folded for r in rows])
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


def _prefab_rows(rows) -> None:
    for oid, t, schema, _ in sorted(rows, key=lambda r: r[1]["name"].lower()):
        tip = (f"{schema.name}: {schema.description}" if schema else "Fields not decoded: placed as a copy of a "
               "stock entry") + f"\nbehaviour {t['behaviour']}, used {t.get('uses', 0)} times"
        variants = _variants(oid, schema)
        if not variants:
            _prefab_row(oid, f"{t['name']}  0x{oid:X}", tip)
        for value, label in variants:  # one row per named value (the health pickup's sizes)
            _prefab_row(VARIANT_BASE + (oid << 4) + value, f"{t['name']} ({label})  0x{oid:X}", tip)


def _variants(oid: int, schema: obj.Schema | None) -> list[tuple[int, str]]:
    """(value, name) of the field a type is listed once per value of (schema "variants"), or []."""
    v = schema.variants if schema else None
    f = schema.field(v["field"]) if v else None
    if not f or oid not in [int(i, 0) for i in v["ids"]]:
        return []
    return [(value, label) for value, label in sorted(f.enum.items()) if 0 <= value < 16]


def _sprite_key(pid: int) -> tuple[int, list | None]:
    """(object id, env colour) of a prefab's sprite: a health size's colour."""
    oid, fields = _split(pid)
    schema = obj.schema_for_id(oid, S.schemas, S.behaviours) if fields else None
    return oid, schema.env_colour(fields) if schema else None


def _split(pid: int) -> tuple[int, dict | None]:
    """A prefab id of a type -> (object id, the fields its variant sets, or None)."""
    if pid < VARIANT_BASE:
        return pid, None
    oid, value = divmod(pid - VARIANT_BASE, 16)
    schema = obj.schema_for_id(oid, S.schemas, S.behaviours)
    return oid, {schema.variants["field"]: value}


def _prefab_place(t: dict, schema: obj.Schema | None) -> tuple[str, str | None]:
    """(category, fold) a type is listed under in Prefabs: its schema's category and kind, or for a type with no
    fields decoded, where its behaviour puts it (BEHAVIOUR_FOLDS), else Other."""
    if schema:
        return schema.category, _plural(schema.name)
    return BEHAVIOUR_FOLDS.get(t["behaviour"], ("Other (fields not decoded)", None))


def _plural(name: str) -> str:
    """A kind's name for a fold of several: "Door lock light" -> "Door lock lights", "Exit (loading zone)" -> "Exits
    (loading zone)"."""
    head, sep, rest = name.partition(" (")
    return name if head.endswith("s") or head in UNCOUNTED else head + "s" + sep + rest


HINT = "Drag a type into the Scene, or select it and press Place."


def _details_height(width: float) -> float:
    """How much of a tall Prefabs window the selected entry's options take, so the list gets the rest."""
    row = imgui.get_frame_height_with_spacing()
    pid = S.prefab["oid"]
    text = HINT if pid is None else GROUPS[pid][1] if pid in (-2, -3) else "x" * 60 if pid <= KIND_BASE else ""
    wrapped = imgui.calc_text_size(text, False, max(width, 1.0)).y + imgui.get_style().item_spacing.y if text else 0
    rows = 0 if pid is None else 4 if pid == -1 else 1 if pid < 0 else 2
    if pid is not None and pid >= 0 and S.sprite(_split(pid)[0]):
        rows += hello_imgui.em_size(4) / row
    return wrapped + rows * row + imgui.get_style().item_spacing.y * 2 + 2


def _sprite_image(oid: int, height: float) -> bool:
    """A sprite type's picture `height` tall at the cursor (the Prefabs window's thumbnails); False if it has none."""
    info = S.sprite(*_sprite_key(oid)) if oid >= 0 else None
    thumb = S.view.thumbnail(info["png"]) if info else None
    if thumb is None:
        return False
    glo, (w, h) = thumb
    imgui.image(imgui.ImTextureRef(glo), imgui.ImVec2(height * w / max(h, 1), height))
    return True


def _prefab_row(pid: int, label: str, tip: str) -> None:
    line = imgui.get_text_line_height()
    if not _sprite_image(pid, line):
        imgui.dummy(imgui.ImVec2(line, line))  # names line up with the rows that have a picture
    imgui.same_line()
    if imgui.selectable(f"{label}##pf{pid}", S.prefab["oid"] == pid)[0]:
        S.prefab["oid"], S.prefab["example"] = pid, 0
    if imgui.is_item_hovered():
        theme.tip(tip)
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
        oid = _split(pid)[0]
        if S.sprite(oid):
            _sprite_image(pid, hello_imgui.em_size(4))
            imgui.same_line()
            _muted("Drawn by the game as this sprite (its first frame), always facing the camera.")
        t = S.objtypes.get(oid, {})
        schema = obj.schema_for_id(oid, S.schemas, S.behaviours)
        like = f"like {S.type_name(int(t['borrowed'], 16))}: " if t.get("borrowed") else ""
        options = [like + _example_label(*e) for e in t.get("examples", [])]
        if schema and schema.status == "decoded":
            options.append("defaults (no stock entry)")
        if options:
            _, pf["example"] = imgui.combo("Start from", min(pf["example"], len(options) - 1), options)
            if imgui.is_item_hovered():
                theme.tip("A stock entry to copy (its fields not set in the Inspector keep the game's values)" + (
                    "\nNo stock level places this type: it copies an entry of a type with the same behaviour and "
                    "keeps its own id" if like else ""))
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
                         S.objtypes[door]["examples"][0] if door is not None else None, mesh=S.view.mesh)
        elif pid == -2:
            placer.exit_pair(pos)
        elif pid == -3:
            placer.region(pos)
        else:
            oid, fields = _split(pid)
            examples = S.objtypes.get(oid, {}).get("examples", [])
            chosen = pf["example"] if pid == pf["oid"] else 0
            placer.single(oid, pos, tuple(examples[chosen]) if chosen < len(examples) else None,
                          name=S.type_name(oid), fields=fields)
    except ValueError as e:
        S.message = f"Can't place it: {e}"
        return
    if S.snap_floor and S.view.mesh is not None:  # each object onto the floor under it (a squad spreads out)
        for o in placer.made:
            y = S.view.mesh.floor_height(o["pos"][0], o["pos"][2], below=o["pos"][1] + 60)
            if y is not None:
                o["pos"][1] = obj.on_ground(y, S.schema(o))
    what = "kind" if pid <= KIND_BASE else GROUPS[pid][0] if pid < 0 else S.type_name(_split(pid)[0])
    h.add(placer.made, f"place {what}")
    S.selection = {o["uid"] for o in placer.made}
    S.message = f"Placed {len(placer.made)} object{'s' if len(placer.made) > 1 else ''}"
    if pid == -1 and S.view.mesh is not None:
        holes = sum(placer.key(o) == "regioninfo" for o in placer.made)
        S.message += (f", {holes} hole(s) for cover" if holes else
                      " (no obstacle in its region: a smart squad stands and fires; put a crate in it, then Add cover, "
                      "or draw a hole with the Region tool)")
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
            theme.tip(tip)
        imgui.same_line()
    imgui.text("|")
    imgui.same_line()
    _, S.local = imgui.checkbox("Local", S.local)
    imgui.same_line()
    _, S.snap = imgui.checkbox("Snap", S.snap)
    imgui.same_line()
    _, S.snap_floor = imgui.checkbox("Floor", S.snap_floor)
    if imgui.is_item_hovered():
        theme.tip("Moved objects drop onto the floor under them")
    imgui.same_line()
    imgui.set_next_item_width(hello_imgui.em_size(5))
    _, S.grid = imgui.input_float("##grid", S.grid, 0, 0, "%.0f")
    if S.tool == "region":
        imgui.same_line()
        _, S.region_hole = imgui.checkbox("Hole", S.region_hole)
        if imgui.is_item_hovered():
            theme.tip("Draw a hole of the region around it: an obstacle squads keep out of and take cover "
                              "behind (wound clockwise, with a region setting linking it to the area)")
    if S.tool == "water":
        imgui.same_line()
        _, S.water_auto = imgui.checkbox("Auto height", S.water_auto)
        if imgui.is_item_hovered():
            theme.tip("The surface goes 15 units above the floor where the drag starts")
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
        theme.tip("Build the pack, install it in the port and start the game in this level (at the selected "
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
    for lv in S.project.levels.values():
        stock_edit.sync(lv)  # the game objects' changes, as the build reads them
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
    uids = {u for u in S.selection if not stock_edit.is_stock(u) or S.game_editable()}
    if h and uids:
        h.delete(uids)
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
        split("BottomSpace", "BottomRightSpace", d.right, 0.5),
    ]

    def win(label, space, fn):
        w = hello_imgui.DockableWindow()
        w.label, w.dock_space_name, w.gui_function = label, space, guarded(label, fn)
        return w

    # The first window of each space is its front tab: Scene, Hierarchy, Inspector, Project and Console show.
    params.dockable_windows = [
        win("Scene", "MainDockSpace", scene),
        win("Level Graph", "MainDockSpace", level_graph),
        win("Planet", "MainDockSpace", planet_tools.planet_window),
        win("Models", "MainDockSpace", model_viewer.models_window),
        win("Scripts", "MainDockSpace", script_tools.scripts_window),
        win("Hierarchy", "LeftSpace", hierarchy),
        win("Prefabs", "LeftSpace", prefabs),
        win("Inspector", "RightSpace", labelled(inspector)),
        win("Level", "RightSpace", labelled(level_tools.level_window)),
        win("Kinds", "RightSpace", labelled(kind_tools.kinds_window)),
        win("Triggers", "RightSpace", labelled(script_tools.triggers_window)),
        win("Weapons", "RightSpace", labelled(script_tools.weapons_window)),
        win("Project", "BottomSpace", project_window),
        win("Console", "BottomRightSpace", console),
    ]
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
        pick = os.environ.get("JFGSTUDIO_SELECT")  # screenshots: "level:uid" opens that level with the object selected
        if pick and S.project and ":" in pick:
            level, uid = pick.split(":", 1)
            if level in S.project.levels:
                S.level = level
                S.selection = {uid}
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = f"JFG-Studio {__version__}"
    hello_imgui.set_assets_folder(str(Path(__file__).resolve().parent.parent / "assets"))  # app_settings/icon.png
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
