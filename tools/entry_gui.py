"""JFG-Studio.exe: the editor (windowed; an argument is a project to open)."""

import os
import sys

if sys.stdout is None:  # a windowed exe has no console: keep print() from failing
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115

from jfgstudio.cli import main  # noqa: E402

sys.exit(main(["gui", *sys.argv[1:]]))
