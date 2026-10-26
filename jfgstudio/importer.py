"""Importing geometry (task S20): copy a Blender `.glb` into a project and check it the way the build will.

The check runs jfgformats' track importer on it (the same `import_track` the build uses), then `check_track_buffer`,
and reports what the game will see: segments, batches, triangles, textures (new images become new textures of the
game's, added at load) and the surfaces the material words make. A problem the build would refuse is reported here
first.
"""

import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from jfgstudio import formats
from jfgstudio import objects as obj
from jfgstudio import project as proj

SEGMENT_MAX = 127


@dataclass
class Report:
    ok: bool
    lines: list[str] = field(default_factory=list)
    bounds: tuple | None = None  # ((x, y, z) min, max) of the geometry


def check(path: str | Path, catalog, log=None) -> Report:
    """Runs the track importer on a .glb against the user's catalog. Never raises for a bad file: the report says."""
    jf = formats.load()
    from jfgformats import models, tracks
    lines = []
    try:
        data = models.ModelData(catalog.assets)
        from jfgstudio import game_textures
        library = game_textures.Library(catalog)
        ready, used = library.prepare_glb(Path(path))
        result = tracks.import_track(data, str(ready), track_id=catalog.track_count(), log=lines.append,
                                     stock_textures=True)
        tracks.check_track_buffer(result["data"], name=Path(path).name)
        track = tracks.read_track(result["data"])
    except (jf.FormatError, OSError, ValueError, KeyError) as e:
        return Report(False, [*lines, f"error: {e}"])
    segments = track.get("segments", [])
    batches = sum(len(s.get("batches", [])) for s in segments)
    triangles = sum(len(s.get("triangles", [])) for s in segments)
    out = [f"{Path(path).name}: {len(segments)} segments (at most {SEGMENT_MAX}), {batches} batches, "
           f"{triangles} triangles, {len(track.get('textures', []))} textures "
           f"({len(result.get('newTextures', {}))} new images)",
           f"buffer: {len(result['data'])} bytes unpacked; fits the game's track buffer"]
    size = len(result["data"])
    if size > TRACK_LARGEST:
        out.append(f"error: {size // 1024} KB unpacked is more than the game's largest level ({TRACK_LARGEST // 1024} "
                   "KB): the level's objects then find no memory and the game crashes (play-tested at 329 KB). Use "
                   "fewer triangles: coarser walls, textures that repeat (game_textures repeats both), fewer props")
    elif size > TRACK_LARGE:
        out.append(f"warning: {size // 1024} KB unpacked: bigger than 9 in 10 of the game's levels "
                   f"({TRACK_LARGE // 1024} KB); keep its objects few, or use fewer triangles")
    if used:
        out.append(f"game textures: {len(set(used))} of the game's level textures, named by number (the pack carries "
                   "none of them)")
    art = sum(1 for png in result.get("newTextures", {}).values() if library.game_art(png))
    if art:
        out.append(f"error: {art} image(s) are the game's own art but not level textures: a pack can't carry them. "
                   "Use the game's level textures (game_textures) or images of your own")
    out += [line for line in lines if line.strip()]
    lost = lost_collision(track)
    if lost:
        out.append(f"error: {lost} colliding triangles come after non-colliding (nocollide) ones in their segment: the "
                   "game gives them no collision (the player falls through). The importer puts them last within a draw "
                   "pass: make the see-through surfaces after them colliding, or the decoration solid")
    report = Report(not lost and not art and size <= TRACK_LARGEST, out)
    try:
        from jfgstudio.gui.viewport import TrackMesh  # numpy only; no window needed
        mesh = TrackMesh(str(path))
        report.bounds = (tuple(float(v) for v in mesh.bounds[0]), tuple(float(v) for v in mesh.bounds[1]))
        kinds = sorted({p[5] for p in mesh.parts})
        report.lines.append("surfaces: " + ", ".join(kinds))
    except Exception as e:  # noqa: BLE001 (the preview reader is optional for the report)
        report.lines.append(f"(no preview: {e})")
    return report


# The game's own levels' tracks, unpacked: 9 in 10 are under TRACK_LARGE, the largest is TRACK_LARGEST. The track
# shares the game's 8 MB heap with the level's objects: a 329 KB one left them no room and crashed (play-tested).
TRACK_LARGE, TRACK_LARGEST = 121 * 1024, 247 * 1024
NO_COLLISION = 0x80  # a triangle flag: drawn, not solid


def lost_collision(track: dict) -> int:
    """Colliding triangles that come after a non-colliding one in their segment. The game collides only a segment's
    first N triangles, N its count of colliding ones (decomp MSR, "Ledges"): a non-colliding triangle before the end
    pushes colliding ones past N, and an imported Kestrel level lost its floors that way (play-tested 2026-10-25)."""
    lost = 0
    for seg in track.get("segments", []):
        flags = [t[0] for t in seg.get("triangles", [])]
        first = next((i for i, f in enumerate(flags) if f & NO_COLLISION), None)
        if first is not None:
            lost += sum(1 for f in flags[first:] if not f & NO_COLLISION)
    return lost


def import_geometry(project: proj.Project, src: str | Path, name: str | None = None) -> Path:
    """Copies a .glb into the project's geometry folder as <name>.glb (default: the file's own name, cleaned)."""
    src = Path(src)
    if src.suffix.lower() != ".glb":
        raise proj.ProjectError([f"{src.name}: export a .glb (glTF binary) from Blender"])
    name = name or "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in src.stem.lower()) or "track"
    if not proj.NAME_RE.match(name):
        raise proj.ProjectError([f"{name!r}: geometry names use lower case letters, digits, _ and -"])
    dest = project.geometry_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return dest


def markers(path: str | Path) -> list[dict]:
    """Objects placed in Blender (jfg64 markers): glTF nodes whose extras name a `jfg_type`, as project objects. The
    position is the node's place in the file; a type with a facing field gets it from the marker's arrow (its local +Y
    in glTF, which is where jfg64's arrow points)."""
    import numpy as np

    from jfgstudio.gui.viewport import _node_matrix
    formats.load()
    from jfgformats import gltf
    doc, _ = gltf.read_glb(str(path))
    nodes = doc.get("nodes", [])
    schemas = obj.load_schemas()
    out, used = [], set()

    def walk(i, parent):
        m = parent @ _node_matrix(nodes[i])
        extras = nodes[i].get("extras") or {}
        if isinstance(extras.get("jfg_type"), str) and extras["jfg_type"]:
            try:
                fields = json.loads(extras.get("jfg_fields") or "{}")
            except ValueError:
                fields = {}
            o = {"type": extras["jfg_type"], "pos": [round(float(v)) for v in m[:3, 3]],
                 "fields": {k: int(v) for k, v in fields.items() if isinstance(v, int | float)}}
            schema = obj.schema_for(o, schemas)
            yaw_f = schema.role("yaw") if schema else None
            if yaw_f is not None:
                d = m[:3, :3] @ np.array([0.0, 1.0, 0.0])
                if abs(d[0]) + abs(d[2]) > 1e-6:
                    # a start point at yaw 0 faces -z in the game, so the marker's arrow gives yaw + half a turn
                    o["fields"][yaw_f.name] = obj.value_of_angle(yaw_f, math.atan2(-d[0], -d[2]))
            if schema and schema.status == "decoded":
                o["size"] = schema.min_size + (schema.min_size & 1)
                o["fields"] = {**schema.defaults(), **o["fields"]}
            stem = "".join(ch if ch.isalnum() else "_" for ch in nodes[i].get("name", "marker").lower()).strip("_")
            stem = stem.rstrip("0123456789_") or "marker"
            k = 1
            while f"{stem}{k}" in used:
                k += 1
            used.add(f"{stem}{k}")
            o = {"uid": f"{stem}{k}", "name": nodes[i].get("name", stem), **o}
            out.append(o)
        for c in nodes[i].get("children", []):
            walk(c, m)

    scene = doc.get("scenes", [{}])[doc.get("scene", 0)].get("nodes", list(range(len(nodes)))) if nodes else []
    for r in scene:
        walk(r, np.identity(4))
    return out


def first_level(project: proj.Project, track_name: str, mesh=None) -> proj.Level:
    """A level on the imported geometry with one start point: the middle of the geometry, on its floor (`mesh`: the
    Scene's TrackMesh of it, when there is one)."""
    name = "level1"
    while name in project.levels:
        name = f"level{int(name[5:]) + 1}"
    level = proj.new_level(project, name, project.meta["name"][:31], f"pack:track/{track_name}")
    try:
        placed = markers(project.geometry_path(track_name))
    except Exception:  # noqa: BLE001 (a file the importer can't read gets the default start point)
        placed = []
    level.objects["objects"].extend(placed)
    if any(o["type"] == "stock:objtype/0xD" for o in placed):
        return level
    x = y = z = 0.0
    if mesh is not None:
        lo, hi = mesh.bounds
        x, z = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        floor = mesh.floor_height(x, z)
        y = obj.on_ground(floor, None) if floor is not None else hi[1]  # a start point: a little above the floor
    level.objects["objects"].append({"uid": "start0", "name": "Start", "type": "stock:objtype/0xD",
                                     "pos": [round(x), round(y), round(z)], "size": 0x0E,
                                     "fields": {"player": 0, "startPoint": 0, "yaw": 0}})
    return level
