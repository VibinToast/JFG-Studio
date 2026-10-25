"""Access to `jfgformats`, the decomp's format library (decomp repo, tools/jfgformats/).

Studio never carries its own copy of the format code: it imports the package from the user's decomp checkout, set
with `jfgstudio settings set decomp <path>`.
"""

import importlib
import sys
from pathlib import Path

from jfgstudio import settings


class FormatsUnavailable(RuntimeError):
    pass


def load():
    """Returns the jfgformats module, or raises FormatsUnavailable with what to do."""
    decomp = settings.get("decomp")
    if not decomp:
        raise FormatsUnavailable("The decomp checkout is not set: jfgstudio settings set decomp <path>")
    tools = Path(decomp) / "tools"
    if not (tools / "jfgformats").is_dir():
        raise FormatsUnavailable(
            f"{tools / 'jfgformats'} does not exist yet (decomp task F1); pull the decomp or check the path")
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        return importlib.import_module("jfgformats")
    except ImportError as e:
        raise FormatsUnavailable(f"jfgformats did not import: {e}") from e
