"""Animation paths of a level of the pack's own (level.json "paths"): objects the level's animation block spawns and
moves along nodes, as the game's lifts, platforms and the hoverboard (decomp MSR "Animation sequences and paths",
"Writing an animation block"):

    "paths": [{"object": "stock:objtype/0x47", "points": [[-500, 60, 0], [500, 60, 0]], "speed": 150,
               "loop": true, "start": "level"}]

The build encodes them with jfgformats.animseq as the level's own block (group 0, path n = the n-th entry), appended
as animseq_<level> and named by the header's +0xE8 through a fixup. "start": "level" starts the path at time 0;
"trigger" leaves it waiting for an AnimTrigger (0xC2: flags 0x80, index n, pause on exit), which place_path puts at
the first point. The spawned object is not in the level's object list; it is spawned with a plain 10-byte entry, so
it takes no fields (the stock path objects need none).
"""

from jfgstudio import refs

LIMIT = 16384  # node coordinates, world units (MSR)
MAX_NODES = 75  # the US ROM's longest path
DEFAULT_SPEED = 150


class PathError(ValueError):
    pass


def check(path: dict) -> list[str]:
    """What the build refuses in one path."""
    out = []
    try:
        r = refs.parse(path.get("object", ""), "objtype")
        if not r.is_stock:
            out.append(f"{path['object']}: a path spawns a stock object type (the game's own id) for now")
    except refs.RefError as e:
        out.append(str(e))
    pts = path.get("points", [])
    if not 2 <= len(pts) <= MAX_NODES:
        out.append(f"a path has 2-{MAX_NODES} points, not {len(pts)}")
    for p in pts:
        if len(p) != 3 or any(abs(v) >= LIMIT for v in p):
            out.append(f"point {p}: [x, y, z] within +-{LIMIT}")
            break
    if not 0 < path.get("speed", DEFAULT_SPEED) <= 2000:
        out.append(f"speed {path.get('speed')}: units a second, 0-2000")
    if path.get("start", "level") not in ("level", "trigger"):
        out.append(f"start {path.get('start')!r}: \"level\" or \"trigger\"")
    return out


def block(paths: list[dict]) -> bytes:
    """The level's animation block: path n spawns its object at its first point and moves along the rest."""
    from jfgformats import animseq
    doc = animseq.new_block()
    for n, p in enumerate(paths):
        bad = check(p)
        if bad:
            raise PathError(f"path {n}: {'; '.join(bad)}")
        animseq.add_path(doc, n, refs.parse(p["object"], "objtype").number(),
                         [tuple(float(v) for v in pt) for pt in p["points"]],
                         speed=p.get("speed", DEFAULT_SPEED), loop=p.get("loop", True))
    for n, p in enumerate(paths):
        if p.get("start", "level") == "level":
            animseq.start_path(doc, n)
    raw = animseq.encode_block(doc)
    problems = animseq.validate_block(raw)
    if problems:
        raise PathError("; ".join(problems))
    return raw
