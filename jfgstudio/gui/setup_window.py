"""The first-launch setup window: the player's ROM and the Jet Force Gemini: Recompiled folder, each checked as it's
picked, then the catalog built from the ROM. It opens at start-up while no ROM is set (setup.needed), and from
Help > Setup... at any time."""

import os
import threading

from imgui_bundle import hello_imgui, imgui
from imgui_bundle import portable_file_dialogs as pfd

from jfgstudio import crashlog, settings, setup
from jfgstudio.gui import theme

TITLE = "Welcome to JFG-Studio"


class SetupState:
    def __init__(self):
        self.open = False  # set to open the window next frame
        self.paths = {"rom": "", "port": ""}
        self.checks: dict[str, setup.Check | None] = {"rom": None, "port": None}
        self.checking: set[str] = set()
        self.building = False
        self.status = ""  # the catalog build's last line, or its error
        self.done = False


W = SetupState()


def _app():
    from jfgstudio.gui import app
    return app


def start(screenshot: bool = False) -> None:
    """At start-up: open the window when Studio has no ROM (not for screenshots, unless JFGSTUDIO_SETUP is set)."""
    if os.environ.get("JFGSTUDIO_SETUP") or (not screenshot and setup.needed()):
        open_window()


def open_window() -> None:
    W.paths = {"rom": settings.get("rom") or "", "port": settings.get("port") or ""}
    W.checks = {"rom": None, "port": None}
    W.done, W.status = False, ""
    for key in W.paths:
        if W.paths[key]:
            _check(key)
    W.open = True


def _check(key: str) -> None:
    """Checks a path in the background (the ROM is read whole; the game is asked for its version)."""
    path = W.paths[key].strip()
    W.checking.add(key)

    def run():
        try:
            W.checks[key] = setup.check_rom(path) if key == "rom" else setup.check_game(path)
        except Exception as e:  # noqa: BLE001 (logged; the window keeps working)
            W.checks[key] = setup.Check(False, f"{type(e).__name__}: {e} (log: {crashlog.write(e, 'setup')})")
        finally:
            W.checking.discard(key)

    threading.Thread(target=run, daemon=True).start()


def _build() -> None:
    """Saves the paths and builds the catalog in the background."""
    A = _app()
    settings.set_value("rom", W.paths["rom"].strip() or None)
    settings.set_value("port", W.paths["port"].strip() or None)
    W.building, W.status = True, "Reading your ROM..."

    def run():
        try:
            from jfgstudio.catalog import Catalog
            Catalog.open().build(log=lambda line: setattr(W, "status", line))
            A.S.reset_catalog()
            A.S.view.load_track(None)
            A.S.view.mesh_path = "reload"
            W.done = True
            W.status = "Ready. The game's levels, objects and models are in Studio now."
        except Exception as e:  # noqa: BLE001 (shown; logged)
            W.status = f"The catalog couldn't be built: {e} (log: {crashlog.write(e, 'setup catalog')})"
        finally:
            W.building = False

    threading.Thread(target=run, daemon=True).start()


def _field(key: str, label: str, help_text: str, pick) -> None:
    imgui.text(label)
    imgui.push_style_color(imgui.Col_.text, theme.colour("MUTED"))
    imgui.text_wrapped(help_text)
    imgui.pop_style_color()
    imgui.set_next_item_width(hello_imgui.em_size(34))
    _, W.paths[key] = imgui.input_text(f"##setup_{key}", W.paths[key])
    if imgui.is_item_deactivated_after_edit():
        _check(key)
    imgui.same_line()
    if imgui.button(f"Browse...##setup_{key}") and not W.building:
        picked = pick()
        if picked:
            W.paths[key] = picked
            _check(key)
    check = W.checks.get(key)
    if key in W.checking:
        imgui.text_colored(theme.colour("MUTED"), "Checking...")
    elif check is not None:
        name = "ACCENT" if check.ok and not check.warning else "HIGHLIGHT" if check.warning else "ERROR"
        imgui.push_style_color(imgui.Col_.text, theme.colour(name))
        imgui.text_wrapped(check.text)
        imgui.pop_style_color()
    imgui.spacing()


def setup_window() -> None:
    if W.open:
        imgui.open_popup(TITLE)
        W.open = False
    imgui.set_next_window_size((hello_imgui.em_size(42), 0), imgui.Cond_.appearing)
    if not imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)[0]:
        return
    imgui.push_text_wrap_pos(hello_imgui.em_size(40))
    imgui.text_wrapped("Studio reads your own copy of the game to show its levels, objects and models while you edit, "
                       "and to build your packs. Nothing from it goes into a project or a pack.")
    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    _field("rom", "Your Jet Force Gemini ROM",
           "The unmodified US release, in any byte order (.z64, .v64 or .n64).",
           lambda: (pfd.open_file("Your US ROM", "", ["N64 ROMs", "*.z64 *.v64 *.n64", "All files", "*"])
                    .result() or [None])[0])
    _field("port", "Jet Force Gemini: Recompiled",
           "The folder with JetForceGeminiRecompiled.exe. Play installs your pack there and starts the game in "
           "your level. Packs need version 1.3.0 or newer.",
           lambda: pfd.select_folder("The Jet Force Gemini: Recompiled folder").result())
    imgui.separator()
    imgui.spacing()
    rom_ok = bool(W.checks.get("rom") and W.checks["rom"].ok) and "rom" not in W.checking
    if W.status:
        name = "ERROR" if W.status.startswith("The catalog couldn't") else "ACCENT" if W.done else "MUTED"
        imgui.push_style_color(imgui.Col_.text, theme.colour(name))
        imgui.text_wrapped(W.status)
        imgui.pop_style_color()
        imgui.spacing()
    if W.done:
        if imgui.button("Start"):
            imgui.close_current_popup()
        if W.paths["port"].strip():
            imgui.same_line()
            imgui.text_disabled("Restart Studio to use the game's fonts.")
    else:
        imgui.begin_disabled(not rom_ok or W.building)
        if imgui.button("Save and build the catalog"):
            _build()
        imgui.end_disabled()
        imgui.same_line()
        imgui.begin_disabled(W.building)
        if imgui.button("Later"):
            imgui.close_current_popup()
        imgui.end_disabled()
        if not W.building:
            imgui.same_line()
            imgui.text_disabled("File > Settings... has the same paths.")
    imgui.pop_text_wrap_pos()
    imgui.end_popup()
