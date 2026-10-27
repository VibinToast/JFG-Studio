"""First launch: the checks behind the editor's setup window (gui/setup_window.py).

Studio needs the player's US ROM (for the catalog and every build) and the Jet Force Gemini: Recompiled folder (Play,
the fonts). The window asks for both when no ROM is set, checks each as it's picked, and builds the catalog.
"""

from dataclasses import dataclass
from pathlib import Path

from jfgstudio import build, formats, rom, settings, testrun


@dataclass
class Check:
    ok: bool
    text: str  # what to show under the field
    warning: bool = False  # usable, but something to fix (an old game version)


def needed() -> bool:
    """True when Studio has no ROM to work from: the setup window opens at start-up."""
    return not settings.get("rom")


def version(text: str) -> tuple[int, ...]:
    """'1.2.3' -> (1, 2, 3), '1.3' -> (1, 3, 0); anything that isn't a number ends it."""
    out = []
    for part in str(text).lstrip("v").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        out.append(int(digits))
        if digits != part:
            break
    return tuple(out + [0] * (3 - len(out)))


def check_rom(path: str) -> Check:
    if not path:
        return Check(False, "Pick your US ROM (.z64, .v64 or .n64).")
    if not Path(path).is_file():
        return Check(False, "There's no file there.")
    try:
        rom.load(path)
    except formats.FormatsUnavailable as e:
        return Check(False, str(e))
    except rom.RomError as e:
        text = str(e)
        if "not the US release" in text:
            text = "This isn't the unmodified US release of Jet Force Gemini. Studio needs that one."
        elif "unknown byte order" in text:
            text = "This isn't an N64 ROM."
        return Check(False, text)
    return Check(True, "The US ROM: good.")


def check_game(folder: str) -> Check:
    """The Jet Force Gemini: Recompiled folder: its exe, and its version against what packs need."""
    if not folder:
        return Check(False, "Optional: needed for Play, and for the game's fonts in the editor.", warning=True)
    exe = testrun.find_exe(folder)
    if exe is None:
        return Check(False, "No Jet Force Gemini: Recompiled here: pick the folder with JetForceGeminiRecompiled.exe.")
    caps = testrun.capabilities(exe)
    have = (caps or {}).get("port_version")
    if not have:
        return Check(True, f"Found {exe.name}, but it's too old to say its version. Packs need "
                     f"{build.DEFAULT_MIN_RECOMP} or newer: update it.", warning=True)
    if version(have) < version(build.DEFAULT_MIN_RECOMP):
        return Check(True, f"Version {have}. Packs need {build.DEFAULT_MIN_RECOMP} or newer, so it won't load "
                     "them: update it.", warning=True)
    return Check(True, f"Version {have}: good.")
