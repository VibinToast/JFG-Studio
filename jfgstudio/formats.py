"""Access to `jfgformats`, the decomp's format library (decomp repo, tools/jfgformats/).

Studio never carries its own copy of the format code in this repo. Run from source, it imports the package from the
user's decomp checkout (`jfgstudio settings set decomp <path>`), or an installed one. The release exe bundles a copy
taken from a pinned decomp commit at build time (tools/build_release.ps1), and uses that.
"""

import importlib
import sys
from pathlib import Path

from jfgstudio import settings

MIN_VERSION = (0, 6, 0)  # animseq (animation paths), sprites


class FormatsUnavailable(RuntimeError):
    pass


def frozen() -> bool:
    """True in the release exe (PyInstaller), where jfgformats is bundled."""
    return bool(getattr(sys, "frozen", False))


def _checked(module):
    version = tuple(int(p) for p in str(getattr(module, "__version__", "0")).split(".")[:3] if p.isdigit())
    if version < MIN_VERSION:
        need = ".".join(map(str, MIN_VERSION))
        raise FormatsUnavailable(f"jfgformats {getattr(module, '__version__', '?')} is too old: Studio needs {need} "
                                 "or newer (pull the decomp)")
    return module


def _from_decomp(decomp: str):
    tools = Path(decomp) / "tools"
    if not (tools / "jfgformats").is_dir():
        raise FormatsUnavailable(f"{tools / 'jfgformats'} does not exist: pull the decomp or check the path")
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        return _checked(importlib.import_module("jfgformats"))
    except ImportError as e:
        raise FormatsUnavailable(f"jfgformats did not import: {e}") from e


def load():
    """Returns the jfgformats module, or raises FormatsUnavailable with what to do."""
    if frozen():
        try:
            return _checked(importlib.import_module("jfgformats"))
        except ImportError as e:
            raise FormatsUnavailable(f"this build of Studio is missing its jfgformats: {e}") from e
    decomp = settings.get("decomp")
    if decomp:
        return _from_decomp(decomp)
    try:
        return _checked(importlib.import_module("jfgformats"))
    except ImportError:
        raise FormatsUnavailable("jfgformats was not found: set the decomp checkout in File > Settings "
                                 "(or jfgstudio settings set decomp <path>)") from None
