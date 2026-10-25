"""`jfgstudio test` (task S8): build the pack, put it in the port's mods folder and start the game in a level.

Uses the port's launch options (`--mods-only`, `--start-level`, `--start-point`, `--character`) after asking the
port what it supports (`--capabilities`); says plainly when the installed port lacks something.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from jfgstudio import build, formats, rom, settings
from jfgstudio import project as proj

EXE_NAMES = ("JetForceGeminiRecompiled.exe", "JetForceGeminiRecompiled")


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


def main(args) -> int:
    exe = find_exe(settings.get("port"))
    if exe is None:
        print("error: the port is not set or has no game executable: jfgstudio settings set port <folder>")
        return 1
    caps = capabilities(exe)
    if not caps:
        print(f"error: {exe.name} did not answer --capabilities: this port is older than JFG-Studio needs "
              "(the phase 0 port tasks P1/P5)")
        return 1
    options = set(caps.get("launch_options") or [])
    missing = [o for o in ("--mods-only", "--start-level", "--start-point", "--character") if o not in options]
    if missing:
        print(f"error: the port does not support {', '.join(missing)}")
        return 1
    try:
        project = proj.load(args.project)
    except proj.ProjectError as e:
        for p in e.problems:
            print(f"error: {p}")
        return 1
    if not project.levels:
        print("error: the project has no level to start in")
        return 1
    level = args.level or next(iter(project.levels))
    if level in project.levels:
        number = project.levels[level].level.get("fixedId")
    else:
        number = int(level, 0)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{project.id}.nrm"
        try:
            build.build(project, out, rom_path=args.rom)
        except build.BuildError as e:
            for p in e.problems:
                print(f"error: {p}")
            return 1
        except (formats.FormatsUnavailable, rom.RomError) as e:
            print(f"error: {e}")
            return 1
        mods = exe.parent / "mods"
        mods.mkdir(exist_ok=True)
        shutil.copyfile(out, mods / out.name)
    print(f"installed {mods / out.name}")
    if args.no_launch:
        return 0
    cmd = [str(exe), "--mods-only", project.id, "--start-level", str(number), "--start-point", str(args.start_point),
           "--character", args.character]
    print("starting: " + " ".join(cmd[1:]))
    subprocess.Popen(cmd, cwd=exe.parent)
    return 0
