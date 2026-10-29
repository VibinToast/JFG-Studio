"""User settings (paths to the decomp, the port and the ROM). Kept in the user's config folder, never in a project."""

import json
import os
import sys
from pathlib import Path

import platformdirs

KEYS = {
    "decomp": "the decomp checkout (Studio imports tools/jfgformats from it)",
    "port": "the Jet Force Gemini: Recompiled folder",
    "rom": "your own US ROM (only used to build the local catalog)",
}


def frozen() -> bool:
    """True in the release exe (PyInstaller): jfgformats is bundled, so there is no decomp to set."""
    return bool(getattr(sys, "frozen", False))


def shown_keys() -> dict[str, str]:
    """The settings a user sets: the release exe has no decomp setting."""
    return {k: v for k, v in KEYS.items() if not (frozen() and k == "decomp")}


def command() -> str:
    """The command line's name in messages: the release's console exe, or the source install's script."""
    return "JFG-Studio-cli" if frozen() else "jfgstudio"


def needed() -> str:
    """What the catalog needs set, for messages."""
    return "your ROM" if frozen() else "your ROM and the decomp"


def config_dir() -> Path:
    override = os.environ.get("JFGSTUDIO_CONFIG_DIR")  # tests and portable installs
    return Path(override) if override else Path(platformdirs.user_config_dir("JFG-Studio", appauthor=False))


def data_dir() -> Path:
    """Where the local catalog and caches go: derived from the user's ROM, never shared."""
    override = os.environ.get("JFGSTUDIO_DATA_DIR")
    return Path(override) if override else Path(platformdirs.user_data_dir("JFG-Studio", appauthor=False))


def _path() -> Path:
    return config_dir() / "settings.json"


def load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in KEYS}


def _workspace() -> Path:
    """The folder Studio usually sits in: JetForceGemini/JFG-Modding-Tools/JFG-Studio -> JetForceGemini."""
    return Path(__file__).resolve().parents[3]


def detected(key: str) -> str | None:
    """A path found without a setting, in the usual layout: the decomp next to JFG-Modding-Tools, the user's ROM
    beside it. None when it isn't there (and always in the release exe, which sits wherever it was unzipped)."""
    if frozen():
        return None
    base = _workspace()
    if key == "decomp":
        candidate = base / "Decomp"
        return str(candidate) if (candidate / "tools" / "jfgformats").is_dir() else None
    if key == "rom":
        for name in ("baserom.us.z64", "jfg-us.z64"):
            if (base / name).is_file():
                return str(base / name)
    return None


def get(key: str) -> str | None:
    """The setting, else a path found in the usual layout (see detected)."""
    return load().get(key) or detected(key)


def set_value(key: str, value: str | None) -> None:
    if key not in KEYS:
        raise KeyError(key)
    data = load()
    if value:
        data[key] = value
    else:
        data.pop(key, None)
    _path().parent.mkdir(parents=True, exist_ok=True)
    with open(_path(), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")


# ---- recent projects (the editor reopens the last one; File > Open recent lists them)

RECENT_MAX = 8


def _recent_path() -> Path:
    return config_dir() / "recent.json"


def recent_projects() -> list[str]:
    """Projects opened in the editor, the last first, those still there only."""
    try:
        with open(_recent_path(), encoding="utf-8") as f:
            paths = json.load(f)
    except (OSError, ValueError):
        return []
    return [p for p in paths if isinstance(p, str) and Path(p).is_dir()][:RECENT_MAX]


def note_recent(project: str) -> None:
    """Puts a project the editor opened first in the recent list."""
    path = str(Path(project).resolve())
    paths = [path] + [p for p in recent_projects() if os.path.normcase(p) != os.path.normcase(path)]
    try:
        _recent_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_recent_path(), "w", encoding="utf-8", newline="\n") as f:
            json.dump(paths[:RECENT_MAX], f, indent=1)
            f.write("\n")
    except OSError:
        pass  # a read-only config folder: nothing remembered, nothing broken
