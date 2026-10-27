"""Command line: `jfgstudio` (or `python -m jfgstudio`). With no command it opens the editor."""

import argparse
import sys

from jfgstudio import __version__, settings
from jfgstudio import project as proj
from jfgstudio import validate as val


def cmd_gui(args) -> int:
    from jfgstudio.gui.app import run  # imgui-bundle is only needed for the editor

    return run(project_path=args.project, screenshot=args.screenshot, frames=args.frames)


def cmd_validate(args) -> int:
    try:
        project = proj.load(args.project)
    except proj.ProjectError as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    problems = val.validate(project)
    shown = [p for p in problems if args.all or p.severity != val.INFO]
    for p in shown:
        print(p)
    errors = sum(p.severity == val.ERROR for p in problems)
    warnings = sum(p.severity == val.WARNING for p in problems)
    print(f"{project.meta['name']}: {errors} errors, {warnings} warnings, {len(project.levels)} levels, "
          f"{len(project.planets)} planets")
    return 1 if errors else 0


def cmd_new(args) -> int:
    try:
        proj.create(args.project, args.id, args.name, args.author or "")
    except proj.ProjectError as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    print(f"created {args.project}")
    return 0


def cmd_settings(args) -> int:
    if args.action == "set":
        if args.key not in settings.KEYS:
            print(f"error: unknown setting {args.key!r} (known: {', '.join(settings.KEYS)})")
            return 1
        settings.set_value(args.key, args.value)
    current = settings.load()
    for key, what in settings.shown_keys().items():
        value = current.get(key) or (f"{settings.detected(key)}  (found automatically)" if settings.detected(key)
                                     else "(not set)")
        print(f"{key:7} {value}\n        {what}")
    print(f"config: {settings.config_dir()}\ndata:   {settings.data_dir()}")
    return 0


def cmd_build(args) -> int:
    from jfgstudio import build, formats, rom

    try:
        project = proj.load(args.project)
        out = args.out or str(project.path / "build" / f"{project.id}.nrm")
        build.build(project, out, rom_path=args.rom)
    except (proj.ProjectError, build.BuildError) as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    except (formats.FormatsUnavailable, rom.RomError) as e:
        print(f"error: {e}")
        return 1
    return 0


def cmd_catalog(args) -> int:
    from jfgstudio import formats, rom
    from jfgstudio.catalog import Catalog

    try:
        Catalog.open(args.rom).build()
    except (formats.FormatsUnavailable, rom.RomError) as e:
        print(f"error: {e}")
        return 1
    return 0


def cmd_test(args) -> int:
    from jfgstudio import testrun

    return testrun.main(args)


def cmd_mcp(args) -> int:
    from jfgstudio import mcp_server

    return mcp_server.serve(args.project)


def cmd_not_yet(args) -> int:
    print(f"jfgstudio {args.command}: not built yet (phase 0 task {args.task}, Docs/PHASE0_TASKS.md)")
    return 2


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jfgstudio", description="Level and modpack editor for Jet Force Gemini: "
                                                                 "Recompiled")
    ap.add_argument("--version", action="version", version=f"JFG-Studio {__version__}")
    sub = ap.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="open the editor (the default)")
    gui.add_argument("project", nargs="?", help="a .jfgproj folder to open")
    gui.add_argument("--screenshot", help="save a PNG of the window after --frames frames, then quit")
    gui.add_argument("--frames", type=int, default=30, help=argparse.SUPPRESS)
    gui.set_defaults(run=cmd_gui)

    v = sub.add_parser("validate", help="check a project against the game's rules")
    v.add_argument("project")
    v.add_argument("--all", action="store_true", help="also show notes (info)")
    v.set_defaults(run=cmd_validate)

    n = sub.add_parser("new", help="create an empty project")
    n.add_argument("project", help="folder to create (e.g. my_planet.jfgproj)")
    n.add_argument("--id", required=True, help="mod id, e.g. yourname_my_planet (letters, digits, _)")
    n.add_argument("--name", required=True, help="display name")
    n.add_argument("--author")
    n.set_defaults(run=cmd_new)

    s = sub.add_parser("settings", help="show or set paths (decomp, port, rom)")
    s.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(run=cmd_settings)

    b = sub.add_parser("build", help="build the project into a .nrm pack")
    b.add_argument("project")
    b.add_argument("-o", "--out", help="the .nrm to write (default: <project>/build/<id>.nrm)")
    b.add_argument("--rom", help="your US ROM (default: the rom setting)")
    b.set_defaults(run=cmd_build)

    c = sub.add_parser("catalog", help="build the local catalog from your ROM (in your data folder)")
    c.add_argument("--rom", help="your US ROM (default: the rom setting)")
    c.set_defaults(run=cmd_catalog)

    t = sub.add_parser("test", help="build, install into the port and start the game in a level")
    t.add_argument("project")
    t.add_argument("--level", help="pack level name or number (default: the first level)")
    t.add_argument("--start-point", type=int, default=0)
    t.add_argument("--character", choices=["vela", "juno", "lupus"], default="juno")
    t.add_argument("--rom", help="your US ROM (default: the rom setting)")
    t.add_argument("--no-launch", action="store_true", help="build and install, but don't start the game")
    t.set_defaults(run=cmd_test)

    m = sub.add_parser("mcp", help="serve Studio's tools to an AI agent (MCP over stdio)")
    m.add_argument("project", nargs="?", help="a .jfgproj folder to open at the start")
    m.set_defaults(run=cmd_mcp)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command is None:
        args = parser().parse_args(["gui", *(argv if argv is not None else sys.argv[1:])])
    if args.command == "settings" and args.action == "set" and (not args.key or args.value is None):
        print("usage: jfgstudio settings set <key> <value>  (an empty value clears it)")
        return 1
    return args.run(args)
