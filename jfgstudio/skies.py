"""A pack's own sky domes (skies/<name>.glb, pack:sky/<name>): a dome or cylinder made in Blender, with its own images
and vertex colours, that a level shows behind everything as the game's own skies (decomp MSR "Sky"). The build makes
it a new model (jfgformats import_new, unlit) and a copy of a stock dome's definition with that model (build.py
skies); the level's header +0x58 names that definition.

The game draws the dome first each frame, centred on the camera at its own size, so it never gets nearer. The game's
domes reach 260 to 9000 units from their centre; their faces point inwards (seen from the middle).
"""

import shutil
from pathlib import Path

from jfgstudio import formats
from jfgstudio import project as proj


class SkyError(ValueError):
    pass


def check(path: Path | str) -> dict:
    """What the game will see of a sky .glb, as the build makes it (jfgformats import_new): {"images", "radius"};
    SkyError with what to change when it can't be a model."""
    formats.load()
    from jfgformats import gltf, models
    try:
        r = models.import_new(str(path), name="sky", lit=False, log=lambda *a: None)
    except Exception as e:  # noqa: BLE001 (the importer's own errors are what Blender has to change)
        raise SkyError(f"{Path(path).name}: {e}") from None
    if r.get("joints"):
        raise SkyError(f"{Path(path).name}: a sky has no skeleton (export it without its armature)")
    doc, _ = gltf.read_glb(str(path))
    radius = 0.0
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            acc = doc["accessors"][prim["attributes"]["POSITION"]]
            radius = max([radius] + [abs(v) for v in acc.get("max", []) + acc.get("min", [])])
    return {"images": len(r["textures"]), "radius": round(radius)}


RADIUS = 9000  # a made dome's size: the game's biggest
RINGS, SEGMENTS = 12, 24


def _rgb(c) -> tuple:
    """[r, g, b] 0-255 (or "#rrggbb") as 0-1 floats."""
    if isinstance(c, str):
        c = [int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    if len(c) != 3 or any(not isinstance(v, int) or not 0 <= v <= 255 for v in c):
        raise SkyError(f"colour {c!r}: [r, g, b], each 0-255, or \"#rrggbb\"")
    return tuple(v / 255 for v in c)


def gradient(top, horizon, bottom=None, radius: float = RADIUS) -> bytes:
    """A sky dome .glb with no images: a sphere seen from inside, its vertex colours going from `top` (straight up)
    to `horizon` and on to `bottom` (straight down; the horizon's when left out). Faces point inwards."""
    import math

    formats.load()
    from jfgformats import gltf
    top, horizon = _rgb(top), _rgb(horizon)
    bottom = _rgb(bottom) if bottom is not None else horizon
    rows = []
    for i in range(RINGS + 1):
        lat = math.pi / 2 - math.pi * i / RINGS  # +90 (up) to -90
        t = math.sin(lat)
        col = tuple(h + (a - h) * t for a, h in zip(top, horizon, strict=True)) if t >= 0 else \
            tuple(h + (b - h) * -t for b, h in zip(bottom, horizon, strict=True))
        rows.append([((round(radius * math.cos(lat) * math.cos(2 * math.pi * k / SEGMENTS), 2), round(radius * t, 2),
                       round(radius * math.cos(lat) * math.sin(2 * math.pi * k / SEGMENTS), 2)), col)
                     for k in range(SEGMENTS)])
    pos, cols = [], []
    for i in range(RINGS):
        for k in range(SEGMENTS):
            a, b = rows[i][k], rows[i][(k + 1) % SEGMENTS]
            c, d = rows[i + 1][k], rows[i + 1][(k + 1) % SEGMENTS]
            for tri in ((a, c, b), (b, c, d)):  # wound to face the centre
                if len({tri[0][0], tri[1][0], tri[2][0]}) < 3:
                    continue  # the poles' rings are points
                for p, col in tri:
                    pos.append(p)
                    cols.append((*col, 1.0))
    g = gltf.GlbWriter(generator="JFG-Studio skies.gradient")
    prim = {"attributes": {"POSITION": g.accessor("f", pos, 5126, "VEC3", bounds=True),
                           "COLOR_0": g.accessor("f", cols, 5126, "VEC4")},
            "mode": 4, "material": 0}
    g.doc.update({"materials": [{"name": "sky", "pbrMetallicRoughness": {"metallicFactor": 0.0}}],
                  "meshes": [{"name": "sky", "primitives": [prim]}], "nodes": [{"name": "sky", "mesh": 0}],
                  "scenes": [{"nodes": [0]}], "scene": 0})
    return g.to_bytes()


def make(project: proj.Project, name: str, top, horizon, bottom=None) -> dict:
    """A gradient sky (gradient) saved as skies/<name>.glb; returns add's report."""
    if not proj.NAME_RE.match(name):
        raise SkyError(f"sky name {name!r}: lower case letters, digits, _ and -")
    path = project.sky_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gradient(top, horizon, bottom))
    return add(project, path, name)


def add(project: proj.Project, src: Path | str, name: str | None = None) -> dict:
    """Checks a sky .glb and copies it into the project (skies/<name>.glb, replacing one of that name); returns
    check's report with "sky", its pack:sky ref."""
    src = Path(src)
    if src.suffix.lower() != ".glb":
        raise SkyError(f"{src.name}: a sky is a .glb (glTF binary, from Blender)")
    name = name or proj.prefab_name(src.stem)
    if not proj.NAME_RE.match(name):
        raise SkyError(f"sky name {name!r}: lower case letters, digits, _ and -")
    report = check(src)
    dest = project.sky_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return {"sky": f"pack:sky/{name}", **report}
