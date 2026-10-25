"""User settings (paths to the decomp, the port and the ROM). Kept in the user's config folder, never in a project."""

import json
import os
from pathlib import Path

import platformdirs

KEYS = {
    "decomp": "the decomp checkout (Studio imports tools/jfgformats from it)",
    "port": "the Jet Force Gemini: Recompiled folder",
    "rom": "your own US ROM (only used to build the local catalog)",
}


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


def get(key: str) -> str | None:
    return load().get(key)


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
