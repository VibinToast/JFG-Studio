"""The project folder (`<name>.jfgproj/`): load, save and create.

    project.json                    id, version, name, authors, dependencies
    planets/<name>.json             a planet on the select map
    galaxies/<name>.json            a planet map of the pack's own (planets name it with "galaxy")
    levels/<name>/level.json        header fields by name, track, sky, music
    levels/<name>/objects.json      placed objects
    geometry/<name>.glb             tracks from Blender (pack:track/<name>)
    music/<name>.ogg                music (pack:music/<name>)
    kinds/<name>.json, kinds/<name>/  object types made from stock ones, with their textures (pack:kind/<name>)
    baddies.json                    patches of the game's enemy animation tables (baddy_patches.py), optional
    objtypes/<name>.json            object types made from a stock definition (packtypes.py; pack:objtype/<name>)
    thumb.png (or thumb.dds)        the pack's picture on the game's mods page, optional (Studio's icon without it)

Files are saved deterministically (sorted keys, one-space indent, LF, a final newline), so a load followed by a save
gives the same bytes and projects diff cleanly in git.
"""

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import jsonschema

FORMAT = 1
NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")
# A mod id another pack can depend on: the runtime checks every dependency id with N64Recomp's validate_mod_id
# (letters, digits and _, not starting with a digit). A pack's own id may hold a dot, but then nothing can depend on it.
DEPENDABLE_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# The mods page's picture of a pack, in the order the runtime looks for it at the root of the .nrm (mod_manifest.cpp)
THUMBNAILS = ("thumb.dds", "thumb.png")


class ProjectError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def _schema(name: str) -> dict:
    return json.loads(resources.files("jfgstudio.schemas.project").joinpath(f"{name}.schema.json")
                      .read_text(encoding="utf-8"))


_SCHEMAS = {name: _schema(name) for name in ("project", "level", "objects", "planet", "kind", "galaxy", "baddies",
                                                 "objtype")}


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps(doc).encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)


@dataclass
class Level:
    name: str
    level: dict
    objects: dict

    @property
    def ref(self) -> str:
        return f"pack:level/{self.name}"

    @property
    def base(self) -> int | None:
        """The stock level this level edits ("base"), or None for a level of the pack's own."""
        b = self.level.get("base")
        return int(b.rsplit("/", 1)[1], 0) if b else None


@dataclass
class Project:
    path: Path
    meta: dict
    levels: dict[str, Level] = field(default_factory=dict)
    planets: dict[str, dict] = field(default_factory=dict)
    kinds: dict[str, dict] = field(default_factory=dict)
    galaxies: dict[str, dict] = field(default_factory=dict)
    baddies: list[dict] = field(default_factory=list)  # baddies.json
    objtypes: dict[str, dict] = field(default_factory=dict)  # objtypes/<name>.json

    @property
    def id(self) -> str:
        return self.meta["id"]

    def geometry_path(self, name: str) -> Path:
        return self.path / "geometry" / f"{name}.glb"

    def kind_texture(self, name: str, slot: int | str) -> Path:
        return self.path / "kinds" / name / f"{int(slot)}.png"

    def music_path(self, name: str) -> Path:
        """music/<name>.ogg: a pack's own music (OGG Vorbis), streamed by the port in place of the game's tune."""
        return self.path / "music" / f"{name}.ogg"

    def thumbnail_path(self) -> Path | None:
        """thumb.png (or thumb.dds) in the project folder: the picture the game's mods page shows for the pack."""
        return next((f for f in (self.path / n for n in THUMBNAILS) if f.is_file()), None)

    def save(self) -> None:
        from jfgstudio import stock_edit  # the game level's objects' changes, kept as "stockEdits"
        write_json(self.path / "project.json", self.meta)
        for level in self.levels.values():
            stock_edit.sync(level)
            write_json(self.path / "levels" / level.name / "level.json", level.level)
            write_json(self.path / "levels" / level.name / "objects.json", level.objects)
        for name, planet in self.planets.items():
            write_json(self.path / "planets" / f"{name}.json", planet)
        for name, kind in self.kinds.items():
            write_json(self.path / "kinds" / f"{name}.json", kind)
        for name, galaxy in self.galaxies.items():
            write_json(self.path / "galaxies" / f"{name}.json", galaxy)
        for name, t in self.objtypes.items():
            write_json(self.path / "objtypes" / f"{name}.json", t)
        if self.baddies:
            write_json(self.path / "baddies.json", self.baddies)
        elif (self.path / "baddies.json").exists():  # every patch removed
            (self.path / "baddies.json").unlink()
        gone = self.path / "galaxies"
        if gone.is_dir():  # a galaxy deleted in the editor
            for f in gone.glob("*.json"):
                if f.stem not in self.galaxies:
                    f.unlink()


def _read(path: Path, schema: str, problems: list[str]) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        problems.append(f"{path}: cannot read ({e.strerror})")
        return None
    except ValueError as e:
        problems.append(f"{path}: not valid JSON ({e})")
        return None
    validator = jsonschema.Draft202012Validator(_SCHEMAS[schema])
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    for e in errors:
        where = "/".join(str(p) for p in e.absolute_path) or "(top)"
        problems.append(f"{path}: {where}: {e.message}")
    return None if errors else doc


def load(path: Path | str) -> Project:
    """Loads a project; raises ProjectError listing every file problem at once."""
    path = Path(path)
    problems: list[str] = []
    meta = _read(path / "project.json", "project", problems)
    levels = {}
    levels_dir = path / "levels"
    if levels_dir.is_dir():
        for d in sorted(p for p in levels_dir.iterdir() if p.is_dir()):
            if not NAME_RE.match(d.name):
                problems.append(f"{d}: level folder names use lower case letters, digits, _ and -")
                continue
            level = _read(d / "level.json", "level", problems)
            objects = _read(d / "objects.json", "objects", problems)
            if level is not None and objects is not None:
                levels[d.name] = Level(d.name, level, objects)
    planets = {}
    planets_dir = path / "planets"
    if planets_dir.is_dir():
        for f in sorted(planets_dir.glob("*.json")):
            if not NAME_RE.match(f.stem):
                problems.append(f"{f}: planet file names use lower case letters, digits, _ and -")
                continue
            planet = _read(f, "planet", problems)
            if planet is not None:
                planets[f.stem] = planet
    kinds = {}
    kinds_dir = path / "kinds"
    if kinds_dir.is_dir():
        for f in sorted(kinds_dir.glob("*.json")):
            if not NAME_RE.match(f.stem):
                problems.append(f"{f}: kind file names use lower case letters, digits, _ and -")
                continue
            kind = _read(f, "kind", problems)
            if kind is not None:
                kinds[f.stem] = kind
    galaxies = {}
    galaxies_dir = path / "galaxies"
    if galaxies_dir.is_dir():
        for f in sorted(galaxies_dir.glob("*.json")):
            if not NAME_RE.match(f.stem):
                problems.append(f"{f}: galaxy file names use lower case letters, digits, _ and -")
                continue
            galaxy = _read(f, "galaxy", problems)
            if galaxy is not None:
                galaxies[f.stem] = galaxy
    objtypes = {}
    if (path / "objtypes").is_dir():
        for f in sorted((path / "objtypes").glob("*.json")):
            if not NAME_RE.match(f.stem):
                problems.append(f"{f}: object type file names use lower case letters, digits, _ and -")
                continue
            t = _read(f, "objtype", problems)
            if t is not None:
                objtypes[f.stem] = t
    baddies = []
    if (path / "baddies.json").exists():
        baddies = _read(path / "baddies.json", "baddies", problems) or []
    if problems:
        raise ProjectError(problems)
    return Project(path, meta, levels, planets, kinds, galaxies, baddies, objtypes)


def create(path: Path | str, mod_id: str, name: str, author: str = "") -> Project:
    """A new, empty project folder."""
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise ProjectError([f"{path} is not empty"])
    meta = {"format": FORMAT, "id": mod_id, "version": "0.1.0", "name": name, "authors": [author] if author else [],
            "dependencies": []}
    problems: list[str] = []
    validator = jsonschema.Draft202012Validator(_SCHEMAS["project"])
    problems += [f"project.json: {e.message}" for e in validator.iter_errors(meta)]
    if problems:
        raise ProjectError(problems)
    project = Project(path, meta)
    for folder in ("levels", "planets", "geometry", "music", "kinds", "textures", "text", "scripts", "prefabs"):
        (path / folder).mkdir(parents=True, exist_ok=True)
    project.save()
    return project


def set_thumbnail(project: Project, src: Path | str | None) -> Path | None:
    """Copies a PNG or DDS into the project as its mods page picture (thumb.png / thumb.dds), replacing the one there;
    None removes it."""
    data = None
    if src is not None:
        src = Path(src)
        if src.suffix.lower() not in (".png", ".dds"):
            raise ProjectError([f"{src.name}: the mods page takes a PNG or DDS picture"])
        data = src.read_bytes()
    for name in THUMBNAILS:
        (project.path / name).unlink(missing_ok=True)
    if data is None:
        return None
    dest = project.path / f"thumb{src.suffix.lower()}"
    dest.write_bytes(data)
    return dest


def default_thumbnail() -> bytes:
    """Studio's icon: the mods page picture of a pack whose project has none (a blank tile otherwise)."""
    return resources.files("jfgstudio").joinpath("assets/app_settings/icon.png").read_bytes()


def thumbnail_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's or DDS's (width, height) from its header; None if it is neither."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:4] == b"DDS " and len(data) >= 20:
        return int.from_bytes(data[16:20], "little"), int.from_bytes(data[12:16], "little")
    return None


def edit_stock_level(project: Project, name: str, level_id: int, display_name: str, track_id: int) -> Level:
    """A level that edits stock level `level_id`: what it holds is added to the stock level when the pack loads."""
    if not NAME_RE.match(name):
        raise ProjectError([f"{name!r}: level names use lower case letters, digits, _ and -"])
    if name in project.levels:
        raise ProjectError([f"level {name} already exists"])
    level = Level(name, {"format": FORMAT, "name": display_name[:31], "base": f"stock:level/{level_id}",
                         "track": f"stock:track/{track_id}", "header": {}},
                  {"format": FORMAT, "objects": []})
    project.levels[name] = level
    return level


def level_references(project: Project, name: str) -> list[str]:
    """The project's files (other than the level's own) that name level `name` (pack:level/<name>): exits leading to
    it, planets and galaxies entering it, scripts. Deleting the level leaves them pointing at nothing."""
    ref = re.compile(r"pack:level/" + re.escape(name) + r"(?![A-Za-z0-9_\-])")
    own = (Path(project.path) / "levels" / name).resolve()
    out = []
    for f in sorted(Path(project.path).rglob("*")):
        if f.suffix not in (".json", ".lua") or not f.is_file() or own in f.resolve().parents or "build" in f.parts:
            continue
        try:
            if ref.search(f.read_text(encoding="utf-8")):
                out.append(f.relative_to(project.path).as_posix())
        except (OSError, UnicodeDecodeError):
            continue
    return out


def delete_level(project: Project, name: str) -> None:
    """Removes level `name` from the project and its folder from disk. A stock-level edit going means the pack no
    longer changes that game level."""
    import shutil
    if name not in project.levels:
        raise ProjectError([f"no level {name}"])
    folder = Path(project.path) / "levels" / name
    if folder.is_dir():
        shutil.rmtree(folder)
    del project.levels[name]


def new_level(project: Project, name: str, display_name: str, track: str) -> Level:
    if not NAME_RE.match(name):
        raise ProjectError([f"{name!r}: level names use lower case letters, digits, _ and -"])
    if name in project.levels:
        raise ProjectError([f"level {name} already exists"])
    level = Level(name,
                  {"format": FORMAT, "name": display_name, "track": track, "sky": -1,
                   # Valid as it is: no fog (all zero), stock tune 4 (set both in the Level window).
                   "header": {"cutsceneData": 3, "world": 15, "region": 0, "regionIndex": 0, "fogNear": 0,
                              "fogFar": 0, "fog3": 0, "fogR": 0, "fogG": 0, "fogB": 0, "tune": 4,
                              "tuneChannelMask": 65535}},
                  {"format": FORMAT, "objects": []})
    project.levels[name] = level
    return level
