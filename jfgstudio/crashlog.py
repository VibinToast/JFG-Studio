"""Crash logs, in <data>/logs (Help > Open log folder in the editor).

A Python error is written to crash-<time>.log with its traceback: one that escapes the program, or one the editor
caught in a window (the window shows the error and the editor keeps running). A native crash (in the renderer or
ImGui) is written to native-crash.log by faulthandler, after a line naming the session.
"""

import datetime
import faulthandler
import platform
import sys
import traceback
from pathlib import Path

from jfgstudio import __version__, settings

NATIVE_LOG_MAX = 1 << 20  # native-crash.log starts again past this size
_native = None  # faulthandler needs the file kept open


def log_dir() -> Path:
    d = settings.data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install() -> None:
    """Logs uncaught errors and native crashes from here on."""
    global _native
    path = log_dir() / "native-crash.log"
    if path.exists() and path.stat().st_size > NATIVE_LOG_MAX:
        path.unlink()
    _native = open(path, "a", encoding="utf-8")  # noqa: SIM115 (open for the whole run)
    _native.write(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} JFG-Studio {__version__} session start\n")
    _native.flush()
    faulthandler.enable(_native)
    sys.excepthook = _hook


def write(exc: BaseException, context: str = "") -> Path:
    """Writes one error with its traceback; returns the log's path."""
    now = datetime.datetime.now()
    path = log_dir() / f"crash-{now:%Y%m%d-%H%M%S}.log"
    lines = [f"JFG-Studio {__version__}, Python {platform.python_version()}, {platform.platform()}",
             f"time: {now:%Y-%m-%d %H:%M:%S}"]
    if context:
        lines.append(f"where: {context}")
    lines.append("")
    lines += traceback.format_exception(type(exc), exc, exc.__traceback__)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(line.rstrip("\n") for line in lines) + "\n\n")
    return path


def _hook(kind, value, tb) -> None:
    try:
        path = write(value, "uncaught")
        print(f"JFG-Studio crashed; the log is {path}", file=sys.stderr)
    except OSError:
        pass
    sys.__excepthook__(kind, value, tb)
