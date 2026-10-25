"""The Triggers and Scripts windows (phase 3, S34): "when ... then ..." rules for non-programmers, compiled to Lua by
the build, and a plain editor for the project's own Lua scripts (the port's API: jfg_lua.cpp).
"""

from imgui_bundle import hello_imgui, imgui

from jfgstudio import scripts as scr
from jfgstudio.gui import theme

API = """jfg.on("level", function(level) ... end)          the first tick in a level
jfg.on("tick", function(level, seconds) ... end)  every tick in a level
jfg.message(text [, seconds])     a line on screen
jfg.music(name) / jfg.music_stop()  one of the pack's music/ tracks
jfg.fog(r, g, b, near, far, ticks)  fade the level's fog
jfg.player()                      x, y, z of the hero (or nil)
jfg.level() / jfg.level_id("level_name" | "stock:21")
jfg.flag(n) / jfg.set_flag(n, on) the game's flags
jfg.unlock_door(id)               as a squadron's death does
jfg.warp(level, start_point)      change level
jfg.log(...)                      a line in the port's jfg_mods.log"""

ACTIONS = {"message": {"do": "message", "text": "Hello", "seconds": 4}, "music": {"do": "music", "track": ""},
           "music_stop": {"do": "music_stop"},
           "fog": {"do": "fog", "rgb": [120, 120, 160], "near": 990, "far": 1022, "ticks": 60},
           "flag": {"do": "flag", "flag": 0, "on": True}, "unlock_door": {"do": "unlock_door", "door": 1},
           "warp": {"do": "warp", "level": "", "start": 0}}


def _app():
    from jfgstudio.gui import app
    return app


def triggers_window() -> None:
    A = _app()
    S = A.S
    if not S.project:
        A._muted("Open a project.")
        return
    if S.triggers is None:
        S.triggers = scr.load_triggers(S.project)
    triggers = S.triggers
    changed = False
    if imgui.button("New trigger"):
        level = f"pack:level/{S.level}" if S.level else ""
        triggers.append({"name": f"trigger {len(triggers) + 1}", "level": level, "when": {"kind": "level start"},
                         "once": True, "then": [dict(ACTIONS["message"])]})
        changed = True
    imgui.same_line()
    if imgui.button("Save triggers"):
        scr.save_triggers(S.project, triggers)
        S.message = "Triggers saved (the build compiles them to Lua)"
    A._muted("Each trigger runs its actions when its condition happens in its level (once per visit unless "
             "'Every time').")
    from jfgstudio.gui import level_tools
    levels = level_tools.level_choices()
    remove = None
    for k, t in enumerate(triggers):
        imgui.push_id(k)
        open_ = imgui.collapsing_header(f"{k + 1}. {t.get('name') or 'trigger'}###t{k}",
                                        imgui.TreeNodeFlags_.default_open)
        if open_:
            c, t["name"] = imgui.input_text("Name", t.get("name", ""))
            changed |= c
            names = dict(levels)
            if imgui.begin_combo("Level", names.get(t.get("level"), t.get("level") or "(pick)"),
                                 imgui.ComboFlags_.height_large):
                for value, text in levels:
                    if imgui.selectable(text, value == t.get("level"))[0]:
                        t["level"] = value
                        changed = True
                imgui.end_combo()
            when = t.setdefault("when", {"kind": "level start"})
            kinds = list(scr.WHEN)
            c, idx = imgui.combo("When", kinds.index(when.get("kind", "level start")), kinds)
            if c:
                when["kind"] = kinds[idx]
                changed = True
            if when["kind"] == "enter":
                box = when.setdefault("box", [-200, -200, 200, 200])
                c, box[:] = imgui.input_int4("Area x0 z0 x1 z1", box)
                changed |= c
                if imgui.button("Area from the Scene's selection"):
                    sel = [o for o in S.objects() if o["uid"] in S.selection]
                    if sel:
                        xs, zs = [o["pos"][0] for o in sel], [o["pos"][2] for o in sel]
                        when["box"] = [min(xs) - 100, min(zs) - 100, max(xs) + 100, max(zs) + 100]
                        changed = True
                    else:
                        S.message = "Select objects in the Scene to frame the area"
            elif when["kind"] == "flag":
                c, when["flag"] = imgui.input_int("Game flag", int(when.get("flag", 0)))
                changed |= c
            c, every = imgui.checkbox("Every time", not t.get("once", True))
            if c:
                t["once"] = not every
                changed = True
            imgui.separator_text("Then")
            drop = None
            for i, a in enumerate(t.setdefault("then", [])):
                imgui.push_id(i)
                changed |= _action_fields(a)
                imgui.same_line()
                if imgui.small_button("x"):
                    drop = i
                imgui.pop_id()
            if drop is not None:
                t["then"].pop(drop)
                changed = True
            if imgui.begin_combo("##add", "Add an action"):
                for name, a in ACTIONS.items():
                    if imgui.selectable(name)[0]:
                        t["then"].append(dict(a))
                        changed = True
                imgui.end_combo()
            if imgui.small_button("Delete this trigger"):
                remove = k
        imgui.pop_id()
    if remove is not None:
        triggers.pop(remove)
        changed = True
    if changed:
        S.planets_dirty = True
        S.triggers_dirty = True


def _action_fields(a: dict) -> bool:
    S = _app().S
    changed = False
    kind = a.get("do")
    imgui.text_colored(theme.colour("ACCENT"), kind)
    imgui.same_line()
    if kind == "message":
        imgui.set_next_item_width(hello_imgui.em_size(18))
        c, a["text"] = imgui.input_text("##text", a.get("text", ""))
        changed |= c
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(4))
        c, a["seconds"] = imgui.input_float("s##secs", float(a.get("seconds", 4)), 0, 0, "%.0f")
        changed |= c
    elif kind == "music":
        tracks = [f.stem for f in sorted((S.project.path / "music").glob("*.ogg"))]
        imgui.set_next_item_width(hello_imgui.em_size(14))
        if imgui.begin_combo("##track", a.get("track") or "(pick)"):
            for name in tracks:
                if imgui.selectable(name, name == a.get("track"))[0]:
                    a["track"] = name
                    changed = True
            imgui.end_combo()
    elif kind == "fog":
        col = [v / 255 for v in a.get("rgb", [0, 0, 0])]
        c, col = imgui.color_edit3("##fog", col, imgui.ColorEditFlags_.no_inputs)
        if c:
            a["rgb"] = [round(v * 255) for v in col]
            changed = True
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(9))
        c, (a["near"], a["far"]) = imgui.input_int2("near far", [a.get("near", 990), a.get("far", 1022)])
        changed |= c
    elif kind == "flag":
        imgui.set_next_item_width(hello_imgui.em_size(6))
        c, a["flag"] = imgui.input_int("##flag", int(a.get("flag", 0)))
        changed |= c
        imgui.same_line()
        c, a["on"] = imgui.checkbox("on", bool(a.get("on", True)))
        changed |= c
    elif kind == "unlock_door":
        imgui.set_next_item_width(hello_imgui.em_size(6))
        c, a["door"] = imgui.input_int("door id", int(a.get("door", 1)))
        changed |= c
    elif kind == "warp":
        from jfgstudio.gui import level_tools
        names = dict(level_tools.level_choices())
        imgui.set_next_item_width(hello_imgui.em_size(14))
        if imgui.begin_combo("##warp", names.get(a.get("level"), a.get("level") or "(pick)"),
                             imgui.ComboFlags_.height_large):
            for value, text in level_tools.level_choices():
                if imgui.selectable(text, value == a.get("level"))[0]:
                    a["level"] = value
                    changed = True
            imgui.end_combo()
        imgui.same_line()
        imgui.set_next_item_width(hello_imgui.em_size(5))
        c, a["start"] = imgui.input_int("start", int(a.get("start", 0)))
        changed |= c
    return changed


def scripts_window() -> None:
    A = _app()
    S = A.S
    if not S.project:
        A._muted("Open a project.")
        return
    folder = S.project.path / "scripts"
    files = sorted(folder.glob("*.lua")) if folder.is_dir() else []
    if imgui.button("New script"):
        folder.mkdir(exist_ok=True)
        n = next(k for k in range(1, 999) if not (folder / f"script{k}.lua").exists())
        path = folder / f"script{n}.lua"
        path.write_text('jfg.on("level", function(level)\n  jfg.log("level " .. level)\nend)\n', encoding="utf-8")
        S.script_file, S.script_text = path.name, path.read_text(encoding="utf-8")
    for f in files:
        imgui.same_line()
        if imgui.selectable(f.name, S.script_file == f.name, 0, imgui.ImVec2(imgui.calc_text_size(f.name).x, 0))[0]:
            S.script_file, S.script_text = f.name, f.read_text(encoding="utf-8")
    if S.script_file is None:
        A._muted("A script reacts to the game through the port's API (below). Triggers made in the Triggers window "
                 "become a script too, when the pack is built.")
    else:
        avail = imgui.get_content_region_avail()
        _, S.script_text = imgui.input_text_multiline("##lua", S.script_text,
                                                      imgui.ImVec2(avail.x, max(avail.y - hello_imgui.em_size(12),
                                                                                hello_imgui.em_size(6))))
        if imgui.button("Save script"):
            (folder / S.script_file).write_text(S.script_text, encoding="utf-8", newline="\n")
            S.message = f"scripts/{S.script_file} saved"
    imgui.separator_text("The port's API")
    imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    imgui.text(API)
    imgui.pop_style_color()
