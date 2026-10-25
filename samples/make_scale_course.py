"""Makes samples/scale_course.jfgproj: one level of measured ramps, steps, ledges, gaps, tunnels and passages, played
once to learn the hero's scale (what the agent guide's "Scale" section needs and the code can't give plainly: the
jump, the steepest walkable slope, the highest step, the clearance and width the hero needs).

    python samples/make_scale_course.py

Then `jfgstudio test samples/scale_course.jfgproj` and try each item; SCALE_COURSE.md (written next to the project)
numbers them. Up the slope, onto the block or through the gap: each top holds a health pickup, so getting it means
the hero made it. Everything is drawn here (no game data); it is built with the agent tools (agent.Session).
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import worldkit as wk  # noqa: E402

from jfgstudio import agent  # noqa: E402

OUT = Path(__file__).parent / "scale_course.jfgproj"
GEN = "JFG-Studio samples/make_scale_course.py"
IMAGES = {"stone": ("stone", 32, 31), "metal": ("metal", 32, 32), "sand": ("sand", 16, 33)}

RAMPS = (15, 20, 25, 30, 35, 40, 45, 55)  # degrees, each rising RAMP_RISE
RAMP_RISE = 120
BLOCKS = (8, 16, 24, 32, 48, 64, 80, 100, 120, 140, 160, 200, 250)  # heights to step, jump or climb onto
TUNNELS = (40, 50, 60, 70, 80, 100, 120, 160)  # clearance under a roof
PASSAGES = (20, 30, 40, 50, 60, 80, 100)  # width between two walls
GAPS = (40, 60, 80, 100, 140, 180, 240, 300)  # across a shallow trench
TRENCH = 40  # the trench's depth: falling in is safe, a ramp at its end leads out
LANE = 120  # the width of a ramp or block
LEDGES = (100, 140, 180, 220, 260)  # blocks whose top edge should be grabbable
LEDGE_DROP = 1  # their walls stop this far under the top, so the top shares no edge with them


def course():
    """The mesh, and the items: (group, number, size, top (x, y, z) to put a pickup on, or None)."""
    m = wk.Mesh()
    m.material("ground", "sand")
    m.material("block", "stone")
    m.material("block_side", "stone")
    m.material("roof", "metal")
    items = []

    # the ground: everything but the gap trench (z 1300-1700)
    m.floor("ground", -1800, -600, 1800, 1300, 0, step=400)
    m.floor("ground", -1800, 1700, 1800, 2400, 0, step=400)

    # 1. ramps, rising along +z from a little under the ground (a lip stops the hero) to a platform
    for i, deg in enumerate(RAMPS):
        x0 = -1650 + i * 150
        run = (RAMP_RISE + 4) / math.tan(math.radians(deg))
        m.ramp("block", "block_side", x0, 0, x0 + LANE, run, -4, RAMP_RISE)
        m.box("block", "block_side", x0, run, x0 + LANE, run + 120, 0, RAMP_RISE)
        items.append(("ramp", i + 1, f"{deg} degrees", (x0 + LANE / 2, RAMP_RISE, run + 60)))

    # 2. blocks to step, jump or climb onto
    for i, h in enumerate(BLOCKS):
        x0 = -300 + i * 150
        m.box("block", "block_side", x0, 100, x0 + LANE, 260, 0, h)
        items.append(("block", i + 1, f"{h} high", (x0 + LANE / 2, h, 180)))

    # 3. tunnels: two walls and a roof `clear` above the ground, 200 long
    for i, clear in enumerate(TUNNELS):
        x0 = -1650 + i * 200
        for wx in (x0, x0 + 140):
            m.box("block", "block_side", wx, 700, wx + 20, 900, 0, clear + 20)
        m.box("roof", "roof", x0, 700, x0 + 160, 900, clear, clear + 20, bottom=True)
        items.append(("tunnel", i + 1, f"{clear} clearance, 120 wide", None))

    # 4. passages between walls 150 high, 200 long
    x = 100
    for i, w in enumerate(PASSAGES):
        m.box("block", "block_side", x, 700, x + 100, 900, 0, 150)
        x += 100 + w
        items.append(("passage", i + 1, f"{w} wide", None))
    m.box("block", "block_side", x, 700, x + 100, 900, 0, 150)

    # 5. gaps: platforms along x over a trench, each gap wider than the last; a ramp out of the trench at its west end
    m.floor("ground", -1800, 1300, 1800, 1700, -TRENCH, step=400)
    m.ramp("ground", "block_side", -1800, 1300, -1700, 1700, -TRENCH - 4, 0, axis="x")
    x = -1600
    for i, g in enumerate((*GAPS, None)):
        m.box("block", "block_side", x, 1400, x + 200, 1600, -TRENCH, 0)
        if g is not None:
            items.append(("gap", i + 1, f"{g} across", (x + 200 + g + 100, 0, 1500)))
            x += 200 + g
    # 6. ledge blocks: the walls stop LEDGE_DROP under the top, so the top's edges have no neighbour. The game's
    # track loader makes such edges grabbable ledges; the importer links every edge two triangles share, so a block
    # whose walls meet its top has none (the first row's blocks)
    for i, h in enumerate(LEDGES):
        x0 = -600 + i * 250
        m.floor("block", x0, 2000, x0 + 160, 2160, h)
        for ax, az, bx, bz in ((x0, 2000, x0 + 160, 2000), (x0 + 160, 2000, x0 + 160, 2160),
                               (x0 + 160, 2160, x0, 2160), (x0, 2160, x0, 2000)):
            m.wall("block_side", ax, az, bx, bz, 0, h - LEDGE_DROP)
        items.append(("ledge", i + 1, f"{h} high, its top edge left unlinked", (x0 + 80, h, 2080)))
    return m, items


def main() -> int:
    if OUT.exists():
        import shutil
        shutil.rmtree(OUT)
    s = agent.Session()
    s.new(OUT, "jfgstudio_scale_course", "Scale course (JFG-Studio)", "VibinToast")
    m, items = course()
    glb = OUT / "geometry" / "course.glb"
    wk.write_glb(m, glb, IMAGES, GEN)
    s.add_level("course", "Scale Course", "course")
    s.set_level("course", {"header": {"fogNear": 990, "fogFar": 1022, "fog3": 990, "fogR": 200, "fogG": 210,
                                      "fogB": 225}})
    s.place("course", "startpoint", [0, -300], fields={"player": 0, "startPoint": 0}, facing=180, name="start")
    for group, n, size, top in items:
        if top is not None:
            x, y, z = top
            s.place("course", "health", [x, y + 1, z], name=f"{group} {n}: {size}")
    s.place("course", "exit", [600, -450], fields={"radius": 50, "startPoint": 0}, target="stock:level/21",
            name="out to the game")
    v = s.validate()
    lines = ["# Scale course", "", "You start facing the course. First row: the ramps (number 1 is the longest and "
             "gentlest, 8 the shortest and steepest) and the blocks (1 the lowest, 13 the tallest). Second row: the "
             "tunnels (1 the lowest roof) and the passages between walls (1 the narrowest). Last: the trench, "
             "crossed on platforms from its end with the ramp out of it (gap 1 the narrowest). Beyond it: ledge blocks "
             "(1 the lowest), whose top edge should be grabbable: jump up at a wall and see whether the hero "
             "catches the edge.",
             "A health pickup sits on top of each ramp and block and after each gap: getting it means the hero made "
             "it. The ring by the start is an exit back to the game.", ""]
    for group in ("ramp", "block", "tunnel", "passage", "gap", "ledge"):
        lines += [f"## {group.title()}s", ""] + [f"{n}. {size}" for g, n, size, _ in items if g == group] + [""]
    lines += ["Report: the steepest ramp walked up, the highest block stepped onto without jumping, the highest "
              "reached with a jump, the highest climbed (ledge grab), the lowest tunnel walked / crouched through, "
              "the narrowest passage, and the widest gap jumped (standing and running)."]
    (OUT / "SCALE_COURSE.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: {m.triangles()} triangles, {len(items)} items; validate {v['errors']} errors, "
          f"{v['warnings']} warnings")
    for p in v["problems"]:
        print(" ", p["severity"], p["rule"], p.get("uid", ""), p["message"])
    return 1 if v["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
