"""`jfgstudio test` and the editor's Play button (tasks S8, S19): build the pack, put it in the port's mods folder and
start the game in a level.

Uses the port's launch options (`--mods-only`, `--start-level`, `--start-point`, `--character`) after asking the
port what it supports (`--capabilities`); says plainly when the installed port lacks something. A pack level with a
fixed number starts by number; one the content linker numbers starts by name (`--start-level MOD_ID:level_<name>`),
which the port resolves at load.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from jfgstudio import build, formats, rom, settings
from jfgstudio import project as proj

EXE_NAMES = ("JetForceGeminiRecompiled.exe", "JetForceGeminiRecompiled")
NEEDED = ("--mods-only", "--start-level", "--start-point", "--character")


class TestRunError(RuntimeError):
    pass


def find_exe(port: str | None) -> Path | None:
    if not port:
        return None
    for folder in (Path(port), Path(port) / "bin"):
        for name in EXE_NAMES:
            if (folder / name).is_file():
                return folder / name
    return None


def capabilities(exe: Path) -> dict | None:
    try:
        out = subprocess.run([str(exe), "--capabilities"], capture_output=True, text=True, timeout=30,
                             cwd=exe.parent)
        return json.loads(out.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def start_level_arg(project: proj.Project, level: str, by_name: bool) -> str:
    """What --start-level gets: a stock number, a pack level's fixed number, or its linker name."""
    if level not in project.levels:
        return str(int(level, 0))
    fixed = project.levels[level].level.get("fixedId")
    if fixed is not None:
        return str(fixed)
    if not by_name:
        raise TestRunError(f"level {level} has no fixed number and this port can't start a level by name "
                           "(update the port)")
    return f"{project.id}:level_{level}"


def play(project: proj.Project, level: str | None = None, start_point: int = 0, character: str = "juno",
         rom_path: str | None = None, launch: bool = True, log=print) -> Path:
    """Builds and installs the pack, then starts the game in `level` (the first level by default). Returns the
    installed pack's path. Raises TestRunError, build.BuildError, formats.FormatsUnavailable or rom.RomError."""
    exe = find_exe(settings.get("port"))
    if exe is None:
        raise TestRunError("the port is not set or has no game executable: File > Settings (or jfgstudio settings "
                           "set port <folder>)")
    caps = capabilities(exe)
    if not caps:
        raise TestRunError(f"{exe.name} did not answer --capabilities: this port is older than JFG-Studio needs")
    options = caps.get("launch_options") or {}
    missing = [o for o in NEEDED if o not in options]
    if missing:
        raise TestRunError(f"the port does not support {', '.join(missing)}")
    by_name = isinstance(options, dict) and "by_name" in (options.get("--start-level") or {})
    if not project.levels:
        raise TestRunError("the project has no level to start in")
    level = level or next(iter(project.levels))
    start = start_level_arg(project, level, by_name)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{project.id}.nrm"
        build.build(project, out, rom_path=rom_path, log=log)
        mods = exe.parent / "mods"
        mods.mkdir(exist_ok=True)
        installed = mods / out.name
        shutil.copyfile(out, installed)
    log(f"installed {installed}")
    if launch:
        cmd = [str(exe), "--mods-only", project.id, "--start-level", start, "--start-point", str(start_point),
               "--character", character]
        log("starting: " + " ".join(cmd[1:]))
        subprocess.Popen(cmd, cwd=exe.parent)
    return installed


def main(args) -> int:
    try:
        project = proj.load(args.project)
        play(project, args.level, args.start_point, args.character, args.rom, launch=not args.no_launch)
    except proj.ProjectError as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    except build.BuildError as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    except (TestRunError, formats.FormatsUnavailable, rom.RomError) as e:
        print(f"error: {e}")
        return 1
    return 0
