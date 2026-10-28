"""Makes a debug room for trying out mod enemies: a flat, walled, roofless room 3000 x 3000 with low walls and four
pillars (walls for rolling enemies to bounce off), reached through a portal just behind where a new game starts.

    python samples/make_debug_room.py <out .jfgproj> --mod <mod folder> --type <name>

The mod is a dependency named by its folder (a hand-made mod, or a Studio project's built folder): its object type
`<mod id>:objtype/<name>` is placed as one squad of three in the far half of the room. Everything else is drawn here
or names stock object types by number; the build copies those from the player's own ROM.
"""

import argparse
import math
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import worldkit as wk  # noqa: E402

from jfgstudio import agent  # noqa: E402

GEN = "JFG-Studio samples/make_debug_room.py"
HALF = 1500  # the room's inner half-width (room for a big rolling enemy: the shellback is 280 long)
WALL_HEIGHT, THICK = 220, 80
PILLARS = [(-700, -550), (700, -550), (-700, 550), (700, 550)]
PILLAR_HALF, PILLAR_HEIGHT = 100, 360
FIRST_LEVEL = 21  # where a new game starts
START, LIGHT, GLOW = 0x0D, 0x10, 0xC8
IMAGES = {"floor": ("metal", 64, 3), "wall": ("stone", 64, 5)}


def geometry(out: Path) -> None:
    m = wk.Mesh(ambient=(0.5, 0.5, 0.55), sun=(0.6, 0.58, 0.52))
    m.material("floor", "floor")
    m.material("wall", "wall")
    m.floor("floor", -HALF - THICK, -HALF - THICK, HALF + THICK, HALF + THICK, 0, scale=200, step=250)
    o = HALF + THICK
    for x0, z0, x1, z1 in ((-o, -o, o, -HALF), (-o, HALF, o, o), (-o, -HALF, -HALF, HALF), (HALF, -HALF, o, HALF)):
        m.box("wall", "wall", x0, z0, x1, z1, 0, WALL_HEIGHT)
    for x, z in PILLARS:
        m.box("wall", "wall", x - PILLAR_HALF, z - PILLAR_HALF, x + PILLAR_HALF, z + PILLAR_HALF, 0, PILLAR_HEIGHT)
    wk.write_glb(m, out, IMAGES, GEN)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--mod", required=True, help="the enemy's mod folder (mod.toml, jfg_assets.json)")
    ap.add_argument("--type", required=True, help="its object type, as Studio names it (objtype_ left off)")
    ap.add_argument("--members", type=int, default=3)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    s = agent.Session()
    s.new(out, "jfg_debug_room", "Debug Room", "VibinToast")
    s.p.meta["description"] = ("A walled room for trying out mod enemies, through a portal just behind where a "
                               "new game starts (turn round).")
    s.p.save()
    dep = s.add_dependency(None, None, mod_folder=str(Path(a.mod).resolve()))
    mod_id = dep["dependencies"][-1]["id"]
    enemy = f"{mod_id}:objtype/{a.type}"
    print("types:", dep["types"])

    glb = out / "room_src.glb"
    geometry(glb)
    s.import_geometry(str(glb), "room")
    glb.unlink()
    s.add_level("room", "Debug Room", "room")
    s.place("room", "startpoint", [0, HALF - 250], fields={"startPoint": 0, "player": 0}, facing=0)
    for x, z in ((0, 0), (0, -800), (0, 800)):
        s.place("room", LIGHT, [x, z], fields={"red": 255, "green": 245, "blue": 225, "radius": 900}, lift=400)
    region = [[-HALF + 40, -HALF + 40], [HALF - 40, -HALF + 40], [HALF - 40, HALF - 40], [-HALF + 40, HALF - 40]]
    s.place_squad("room", [0, -900], enemy, members=a.members, region=region, spread=350, name="test squad",
                  face=[0, HALF - 250])

    # The portal, just behind a new game's start point 0 (the example world's stands ahead): turn round to see it
    s.edit_stock_level("portal", FIRST_LEVEL)
    cat = s.catalog()
    start0 = next(e for e in cat.objlist(cat.levels()[FIRST_LEVEL]["objectList1"]) if e[0] == START
                  and e[1][0x0B] == 0)
    sx, sy, sz = struct.unpack_from(">hhh", start0[1], 4)
    facing = (start0[1][0x0C] << 10) / 65536 * 2 * math.pi  # the hero faces -z at yaw 0
    px, pz = round(sx + math.sin(facing) * 220), round(sz + math.cos(facing) * 220)
    s.connect("portal", [px, pz], "room", arrive_start_point=0, name="portal to the debug room")
    s.place("portal", GLOW, [px, pz], lift=50, name="portal glow",
            fields={"red": 255, "green": 140, "blue": 60, "alpha": 70, "distance": 900, "size": 80})
    s.place("portal", LIGHT, [px, pz], lift=60, name="portal light",
            fields={"red": 255, "green": 140, "blue": 60, "radius": 300, "flags": 0x40})
    s.place("room", "exit", [0, HALF - 80], target=f"stock:level/{FIRST_LEVEL}", name="way back",
            fields={"radius": 70, "startPoint": 0})
    clipped = s.check_placement("portal")
    if clipped:
        raise SystemExit(f"the portal's spot is no good: {clipped}")
    s.set_level("room", {"sky": cat.levels()[FIRST_LEVEL].get("sky", -1)})
    print("portal at", px, pz, "start", sx, sy, sz)


if __name__ == "__main__":
    main()
