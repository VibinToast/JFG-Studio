# PyInstaller spec for the release: one folder with JFG-Studio.exe (the editor, a windowed exe) and
# JFG-Studio-cli.exe (the command line and the MCP server, a console exe: a windowed exe has no stdin/stdout).
# Run by tools/build_release.ps1, which sets JFGFORMATS_PATH to a copy of the decomp's tools/ taken at a pinned
# commit. jfgformats is bundled from there; it is never committed to this repo.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (PyInstaller defines SPECPATH)
FORMATS = os.environ["JFGFORMATS_PATH"]
ICON = os.path.join(ROOT, "jfgstudio", "assets", "icon.ico")

datas = (collect_data_files("jfgstudio", excludes=["**/__pycache__/**", "tests/**"])
         + collect_data_files("imgui_bundle")
         + collect_data_files("jsonschema_specifications"))
hidden = collect_submodules("jfgstudio") + collect_submodules("jfgformats") + collect_submodules("glcontext")
excludes = ["tkinter", "jfgformats.tests", "jfgstudio.tests", "matplotlib", "PIL"]


def analysis(script):
    return Analysis(  # noqa: F821
        [os.path.join(ROOT, "tools", script)], pathex=[ROOT, FORMATS], datas=datas, hiddenimports=hidden,
        excludes=excludes, noarchive=False)


gui = analysis("entry_gui.py")
cli = analysis("entry_cli.py")


def exe(a, name, console):
    return EXE(  # noqa: F821
        PYZ(a.pure), a.scripts, [], exclude_binaries=True, name=name, icon=ICON, console=console,  # noqa: F821
        upx=False)


coll = COLLECT(  # noqa: F821
    exe(gui, "JFG-Studio", False), gui.binaries, gui.datas,
    exe(cli, "JFG-Studio-cli", True), cli.binaries, cli.datas,
    name="JFG-Studio", upx=False)
