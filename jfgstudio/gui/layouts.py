"""Saved window layouts: where the windows are docked and how big, as ImGui keeps it (its ini text), under a name in
JFG-Studio/layouts/ next to layout.ini (the layout Studio reopens with). Window > Save layout as... keeps the current
one, Window > Layouts loads one, Window > Delete layout removes one, Window > Reset layout goes back to the default.

ImGui reads ini text only between frames, so a load waits for pre_new_frame."""
from __future__ import annotations

import re
from pathlib import Path

from imgui_bundle import hello_imgui, imgui

_pending: str | None = None          # ini text to load before the next frame
_popup = {"open": False, "name": "", "error": ""}


def folder() -> Path:
    base = Path(hello_imgui.ini_folder_location(hello_imgui.IniFolderType.app_user_config_folder))
    return base / "JFG-Studio" / "layouts"


def clean_name(name: str) -> str:
    """A layout's name as its file is called: letters, digits, spaces, - and _."""
    return re.sub(r"[^A-Za-z0-9 _-]", "", name).strip()[:40]


def names() -> list[str]:
    d = folder()
    return sorted(p.stem for p in d.glob("*.ini")) if d.is_dir() else []


def save(name: str) -> str:
    """Saves the current layout as `name` (replacing one of that name); returns the name used."""
    name = clean_name(name)
    if not name:
        raise ValueError("give the layout a name (letters, digits, spaces, - or _)")
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / f"{name}.ini").write_text(imgui.save_ini_settings_to_memory(), encoding="utf-8")
    return name


def load(name: str) -> bool:
    """Loads layout `name` before the next frame; False if there is none."""
    global _pending
    path = folder() / f"{clean_name(name)}.ini"
    if not path.is_file():
        return False
    _pending = path.read_text(encoding="utf-8")
    return True


def delete(name: str) -> None:
    (folder() / f"{clean_name(name)}.ini").unlink(missing_ok=True)


def pre_new_frame() -> None:
    """The runner's pre_new_frame callback: applies a layout load asked for during the last frame."""
    global _pending
    if _pending is not None:
        imgui.load_ini_settings_from_memory(_pending)
        _pending = None


def menu_items(message) -> None:
    """The Window menu's layout entries; message(text) reports to the status bar."""
    if imgui.menu_item("Save layout as...", "", False)[0]:
        _popup.update(open=True, name="", error="")
    saved = names()
    if imgui.begin_menu("Layouts", bool(saved)):
        for n in saved:
            if imgui.menu_item(n, "", False)[0] and load(n):
                message(f"layout {n}")
        imgui.end_menu()
    if imgui.begin_menu("Delete layout", bool(saved)):
        for n in saved:
            if imgui.menu_item(n, "", False)[0]:
                delete(n)
                message(f"layout {n} deleted")
        imgui.end_menu()


def save_popup(message) -> None:
    """The Save layout popup (call every frame)."""
    if _popup["open"]:
        imgui.open_popup("Save layout")
        _popup["open"] = False
    if imgui.begin_popup_modal("Save layout", None, imgui.WindowFlags_.always_auto_resize)[0]:
        if imgui.is_window_appearing():
            imgui.set_keyboard_focus_here()
        entered, _popup["name"] = imgui.input_text("Name", _popup["name"],
                                                   imgui.InputTextFlags_.enter_returns_true)
        if clean_name(_popup["name"]) in names():
            imgui.text_disabled("replaces the layout of that name")
        if _popup["error"]:
            imgui.text_disabled(_popup["error"])
        if imgui.button("Save") or entered:
            try:
                message(f"layout {save(_popup['name'])} saved")
                imgui.close_current_popup()
            except (ValueError, OSError) as e:
                _popup["error"] = str(e)
        imgui.same_line()
        if imgui.button("Cancel"):
            imgui.close_current_popup()
        imgui.end_popup()
