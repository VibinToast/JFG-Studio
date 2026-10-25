"""Makes samples/zephyra.jfgproj: JFG-Studio's example world, a three-level planet played from a new game to the end.

    python samples/make_example_world.py           (needs your ROM and the decomp settings: it picks stock entries
                                                     to copy and checks the geometry with the game's importer)
    blender --background --factory-startup --python samples/make_example_music.py   (the music, once)

The galaxy: Zephyra is a planet of the pack's own galaxy, the Zephyra Expanse (L / R on the planet map). Two more
planets wait there, hidden like the game's own until the world before them is completed: Glacia (an ice field, after
Zephyra's Sky Arena victory) and Nimbus (sky islands, after Glacia).

The route: a new game -> Goldwood's first level (Forest First), where a glowing portal now stands just ahead of Juno (or
the planet map: R to the Zephyra Expanse) -> 1. Zephyra Landing (a valley, a river and a bridge, a chest on a plateau,
the yellow key on a ledge, an ant squad locking the north gate) -> 2. Ember Caverns (a lava lake crossed on a causeway,
two squads, a chasm with a kill floor, a door that needs the yellow key) -> 3. Sky Arena (a floating arena over a deadly
drop, two squads that both lock the final gate) -> the victory exit back to Forest First. Every level has return exits,
its own music, lights and pickups.

Everything here is drawn or written by this script; the project only names stock object types and stock entries
by number (the build copies those from the player's own ROM).
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import worldkit as wk  # noqa: E402

from jfgstudio import objects as obj  # noqa: E402
from jfgstudio import prefabs  # noqa: E402
from jfgstudio import project as proj  # noqa: E402
from jfgstudio.catalog import Catalog  # noqa: E402
from jfgstudio.gui.viewport import TrackMesh  # noqa: E402

OUT = Path(__file__).parent / "zephyra.jfgproj"
GEN = "JFG-Studio samples/make_example_world.py"

# object ids (stock types) and the stock entries copied for the types Studio only knows in part
START, EXIT, CHEST, HEALTH, AMMO, KEY_YELLOW = 0x0D, 0x0B, 0x1AA, 0xA9, 0xE8, 0x258
LIGHT, GLOW, AUDIO, TEXSCROLL, CRATE, STATIC_CRATE, DOOR = 0x10, 0xC8, 0x20, 0x0F, 0x135, 0xA8, 0x1AD
DOOR_EXAMPLE = (424, 274)  # Forest First's Ftechdoor (the one its ants unlock)
SQUADS = {  # baddy type -> (stock squadron entry, stock member entry); squadrons whose members are list entries too
    "BlueAnt": (0x78, (424, 260), (424, 261)),
    "YellowAnt": (0x5F, (442, 139), (706, 30)),
    "ZombieAnt": (0x121, (518, 57), (518, 58)),
    "BigShieldAnt": (0x9C, (409, 60), (413, 22)),
    "Cyborg": (0x143, (431, 1), (431, 146)),
}
FOREST_FIRST, FOREST_FIRST_TRACK = 21, 18
SKY = {"landing": 180, "caverns": 194, "arena": 205, "glacia": 180, "nimbus": 205}
YAW_NORTH, YAW_SOUTH = 32, 0  # start point facing (angle10: 64 = a full turn): the hero faces -z at yaw 0


# ---------------------------------------------------------------- geometry

def enclosure(m, mat, x0, z0, x1, z1, height):
    """Cliff walls round a rectangle, facing in."""
    m.wall(mat, x1, z0, x0, z0, -200, height, 150)
    m.wall(mat, x1, z1, x1, z0, -200, height, 150)
    m.wall(mat, x0, z1, x1, z1, -200, height, 150)
    m.wall(mat, x0, z0, x0, z1, -200, height, 150)


def gate_wall(m, top, side, x0, x1, z, height, gap=45, thick=12):
    """A wall across x at z with a door gap of 2 * gap in the middle."""
    m.box(top, side, x0, z - thick, -gap, z + thick, -20, height)
    m.box(top, side, gap, z - thick, x1, z + thick, -20, height)
    m.box(top, side, -gap - 10, z - thick, gap + 10, z + thick, 150, height)  # the lintel over the door


def landing_mesh() -> wk.Mesh:
    m = wk.Mesh()
    for name, img, kind in (("grass", "grass", "opaque"), ("riverbed", "sand", "opaque"), ("rock", "rock", "opaque"),
                            ("stone", "stone", "opaque"), ("stone_wall", "stone", "opaque"),
                            ("river_water", "water", "water"), ("crystal", "crystal", "opaque")):
        m.material(name, img, kind)

    def height(x, z):
        if 260 < z < 740:  # the river valley: a shallow wade (only Vela swims; deep water drowns the others)
            return -40 * math.sin(math.pi * (z - 260) / 480)
        if z > 1300 or (-1600 < z < -1150 and -300 < x < 300):  # flat: the gate corridor, the landing pad
            return 0.0
        return 18 * math.sin(x / 380.0) * math.cos(z / 470.0)

    def tint(x, z):
        return (0.95, 1.0, 0.9) if (x // 200 + z // 200) % 2 else (1, 1, 1)

    m.heightfield("grass", -1800, -1800, 1800, 1800, 100, height, 140, tint)
    m.floor("river_water", -1800, 300, 1800, 700, -28, 120, alpha=170, step=600)
    m.box("stone", "stone_wall", -90, 220, 90, 780, -45, 12)                    # the bridge
    m.disc("stone", "stone_wall", 0, -1400, 230, -20, 8, n=20)                  # the landing pad
    m.box("grass", "rock", 800, -900, 1500, -200, -30, 140, 140)                # the plateau
    m.ramp("stone", "rock", 500, -700, 800, -500, 0, 140, axis="x")             # its ramp
    m.box("stone", "rock", -1520, -320, -1200, 20, -20, 220)                    # the key ledge
    m.ramp("stone", "rock", -1500, -1100, -1380, -320, 0, 220, axis="z")        # its ramp
    for x, z, s, h in ((-600, -700, 60, 90), (450, 100, 50, 70), (-1100, 900, 70, 120), (1200, 1000, 60, 100),
                       (300, -900, 40, 60), (-300, 1100, 45, 80)):
        m.box("crystal", "crystal", x - s, z - s, x + s, z + s, -20, h)          # crystal outcrops (cover)
    gate_wall(m, "stone", "stone_wall", -1800, 1800, 1350, 260)
    enclosure(m, "rock", -1800, -1800, 1800, 1800, 420)
    return m


def caverns_mesh() -> wk.Mesh:
    m = wk.Mesh(ambient=(0.38, 0.28, 0.26), sun=(0.65, 0.45, 0.35))
    for name, img, kind in (("rock", "rock", "opaque"), ("rock_wall", "rock", "opaque"), ("stone", "stone", "opaque"),
                            ("stone_wall", "stone", "opaque"), ("molten_lava", "lava", "lava"),
                            ("chasm_kill", "rock", "kill"), ("metal", "metal", "opaque")):
        m.material(name, img, kind)
    m.floor("rock", -1400, -1800, 1400, -900, 0, 140, step=400)                # the south shore
    m.floor("molten_lava", -1400, -900, 1400, 400, -30, 90, tint=(1, 0.9, 0.8), step=400)
    m.wall("rock_wall", -1400, -900, 1400, -900, -30, 0, 60)                   # the lava's banks
    m.wall("rock_wall", 1400, 400, -1400, 400, -30, 0, 60)
    m.box("stone", "stone_wall", -110, -900, 110, 400, -60, 0)                  # the causeway
    m.disc("stone", "stone_wall", 0, -250, 360, -60, 0, n=20)                   # the plaza
    m.floor("rock", -1400, 400, 1400, 700, 0, 140, step=400)                   # the middle shore
    m.floor("chasm_kill", -1400, 700, 1400, 950, -500, 200, step=400)          # the chasm's deadly floor
    m.wall("rock_wall", 1400, 700, -1400, 700, -500, 0, 120)
    m.wall("rock_wall", -1400, 950, 1400, 950, -500, 0, 120)
    m.box("stone", "stone_wall", -50, 690, 50, 960, -30, 0)                     # the narrow bridge
    m.floor("rock", -1400, 950, 1400, 1800, 0, 140, step=400)                  # the north shore
    for x, z in ((-700, 550), (650, 520), (-900, 1150), (900, 1180)):
        m.box("rock", "rock_wall", x - 80, z - 80, x + 80, z + 80, -10, 100)     # boulders (cover)
    gate_wall(m, "metal", "metal", -1400, 1400, 1300, 280)
    enclosure(m, "rock_wall", -1400, -1800, 1400, 1800, 520)
    return m


def arena_mesh() -> wk.Mesh:
    m = wk.Mesh(ambient=(0.4, 0.4, 0.55), sun=(0.6, 0.6, 0.75))
    for name, img, kind in (("stone", "stone", "opaque"), ("stone_wall", "stone", "opaque"),
                            ("metal", "metal", "opaque"), ("crystal", "crystal", "opaque"),
                            ("void_kill", "rock", "kill")):
        m.material(name, img, kind)
    m.disc("stone", "stone_wall", 0, 0, 900, -120, 0, n=32)                     # the arena
    m.box("stone", "stone_wall", -150, -1300, 150, -860, -120, 0)               # the entry platform
    m.box("stone", "stone_wall", -320, 860, 320, 1500, -120, 0)                 # the finale platform
    # Play-tested 2026-10-25: in the open, both squads shot Juno down within seconds of arriving. A screen wall hides
    # the entry, and low walls give cover on the way to the pillars.
    m.box("stone", "stone_wall", -230, -800, 230, -740, 0, 230)                 # the entry screen
    for x0, x1 in ((-330, -130), (130, 330)):
        m.box("stone", "stone_wall", x0, -280, x1, -240, 0, 150)                # low cover walls
    for sx, sz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        x, z = 450 * sx, 450 * sz
        m.box("metal", "metal", x - 70, z - 70, x + 70, z + 70, -10, 240)        # pillars (cover)
        m.box("crystal", "crystal", x - 40, z - 40, x + 40, z + 40, 240, 300)
    for k in range(12):  # a broken rim: low walls with gaps
        if k % 3 == 2:
            continue
        a0, a1 = 2 * math.pi * k / 12, 2 * math.pi * (k + 0.7) / 12
        ax, az, bx, bz = 860 * math.sin(a0), 860 * math.cos(a0), 860 * math.sin(a1), 860 * math.cos(a1)
        if az < -700 and abs(ax) < 400 or az > 700 and abs(ax) < 400:
            continue  # keep the ways to the platforms open
        m.wall("stone_wall", bx, bz, ax, az, 0, 40, 60)
        m.wall("stone_wall", ax, az, bx, bz, 0, 40, 60)
    gate_wall(m, "metal", "metal", -320, 320, 950, 260)
    m.floor("void_kill", -3000, -3000, 3000, 3000, -900, 400, step=1000)        # falling off is the end
    return m


IMAGES = {"grass": ("grass", 16, 11), "sand": ("sand", 16, 12), "rock": ("rock", 32, 13), "stone": ("stone", 32, 14),
          "water": ("water", 16, 15), "crystal": ("crystal", 16, 16), "lava": ("lava", 16, 17),
          "metal": ("metal", 32, 18), "snow": ("snow", 16, 19), "ice": ("ice", 32, 20), "cloud": ("cloud", 16, 21)}


def glacia_mesh() -> wk.Mesh:
    """Glacia: a snow field around a frozen lake, ice pillars for cover, the finish pad on a rise in the north."""
    m = wk.Mesh(ambient=(0.55, 0.6, 0.7), sun=(0.7, 0.75, 0.85))
    for name, img, kind in (("snow", "snow", "opaque"), ("ice", "ice", "opaque"), ("ice_wall", "ice", "opaque"),
                            ("stone", "stone", "opaque"), ("stone_wall", "stone", "opaque"),
                            ("crystal", "crystal", "opaque")):
        m.material(name, img, kind)
    m.floor("snow", -1300, -1500, 1300, -300, 0, 140, step=400)                  # the south field
    m.floor("ice", -700, -300, 700, 500, -10, 160, step=350)                     # the frozen lake
    m.floor("snow", -1300, -300, -700, 500, 0, 140, step=400)
    m.floor("snow", 700, -300, 1300, 500, 0, 140, step=400)
    m.wall("ice_wall", -700, -300, 700, -300, -10, 0, 60)
    m.wall("ice_wall", 700, 500, -700, 500, -10, 0, 60)
    m.floor("snow", -1300, 500, 1300, 1100, 0, 140, step=400)                   # the north field
    m.ramp("stone", "stone_wall", -200, 1100, 200, 1300, 0, 80, axis="z")      # up to the finish pad
    m.box("stone", "stone_wall", -300, 1300, 300, 1550, 0, 80)
    for x, z in ((-450, -700), (450, -650), (-900, 150), (900, 100), (-350, 850), (400, 800)):
        m.box("ice", "ice_wall", x - 50, z - 50, x + 50, z + 50, -10, 180)      # ice pillars (cover)
        m.box("crystal", "crystal", x - 30, z - 30, x + 30, z + 30, 180, 230)
    enclosure(m, "ice_wall", -1300, -1500, 1300, 1550, 380)
    return m


def nimbus_mesh() -> wk.Mesh:
    """Nimbus: three cloud-stone islands over a deadly drop, joined by narrow bridges; the finish on the last one."""
    m = wk.Mesh(ambient=(0.55, 0.5, 0.65), sun=(0.75, 0.7, 0.8))
    for name, img, kind in (("cloud", "cloud", "opaque"), ("stone", "stone", "opaque"),
                            ("stone_wall", "stone", "opaque"), ("metal", "metal", "opaque"),
                            ("void_kill", "rock", "kill")):
        m.material(name, img, kind)
    m.disc("cloud", "stone_wall", 0, -1100, 380, -120, 0, n=20)                # the landing island
    m.box("stone", "stone_wall", -60, -730, 60, -420, -60, 0)                  # bridge 1
    m.disc("cloud", "stone_wall", 0, -50, 520, -120, 0, n=24)                  # the middle island
    for sx in (-1, 1):
        m.box("metal", "metal", sx * 260 - 60, -110, sx * 260 + 60, 10, 0, 160)    # cover blocks
    m.box("stone", "stone_wall", -60, 460, 60, 820, -60, 0)                    # bridge 2
    m.disc("cloud", "stone_wall", 0, 1150, 360, -120, 0, n=20)                 # the finish island
    m.floor("void_kill", -3000, -3000, 3000, 3000, -900, 400, step=1000)       # falling off is the end
    return m


# ---------------------------------------------------------------- objects

class World:
    def __init__(self, project, catalog):
        self.p, self.cat = project, catalog
        self.schemas = obj.load_schemas()
        types = catalog.objtypes()
        self.behaviours = {k: v["behaviour"] for k, v in types.items()}
        self.types = types

    def placer(self, level):
        return prefabs.Placer(level, self.schemas, self.behaviours)

    def drop(self, mesh: TrackMesh, x, z, below=None, lift=0, walks=False):
        """On the floor (rounded up; `walks`: enemies and start points a little above it, as Studio drops them)."""
        y = mesh.floor_height(x, z, below)
        y = math.ceil(y if y is not None else 0) + (obj.GROUND_LIFT["squadmember"] if walks else 0)
        return [round(x), y + round(lift), round(z)]

    def example(self, oid):
        return tuple(self.types[oid]["examples"][0])

    def start(self, pl, mesh, x, z, number, yaw, below=None):
        return pl.single(START, self.drop(mesh, x, z, below, walks=True), name=f"start{number}",
                         fields={"player": 0, "startPoint": number, "yaw": yaw})

    def exit(self, pl, mesh, x, z, target, start_point, name, radius=70):
        o = pl.single(EXIT, self.drop(mesh, x, z), name=name, fields={"radius": radius, "startPoint": start_point})
        o["target"] = target
        return o

    def squad(self, pl, mesh, kind, centre, members, corners, door_id=0, spread=150, reskin=None):
        """A squadron of stock members (or, with `reskin` "pack:kind/<name>", of the pack's kind made from them)."""
        baddy, squad_ex, member_ex = SQUADS[kind]
        n = pl.free("squadron", "squadronId")
        region = pl.polygon([self.drop(mesh, x, z) for x, z in corners])
        cx, cz = centre
        label = reskin.rsplit("/", 1)[1].replace("_", " ").title() if reskin else kind
        squadron = pl.single(0x4F, self.drop(mesh, cx, cz, walks=True), squad_ex, name=f"{label} squad",
                             fields={"squadronId": n, "doorId": door_id, "region": region, "count": members,
                                     "baddyType": baddy})
        if reskin:
            squadron["kindFields"] = {"baddyType": reskin}
        for k in range(members):
            a = 2 * math.pi * k / members
            at = self.drop(mesh, cx + spread * math.sin(a), cz + spread * math.cos(a), walks=True)
            m = pl.single(baddy, at, member_ex, name=label, fields={"squadron": n})
            m["schema"] = "squadmember"
            if reskin:
                m["type"] = reskin
        return n

    def door(self, pl, mesh, x, z, name, fields):
        d = pl.single(DOOR, self.drop(mesh, x, z, below=60), DOOR_EXAMPLE, name=name, fields=fields)  # under the lintel
        d["schema"] = "door"
        return d

    def light(self, pl, mesh, x, z, rgb, radius=350, lift=60, track=True):
        return pl.single(LIGHT, self.drop(mesh, x, z, lift=lift), name="light",
                         fields={"red": rgb[0], "green": rgb[1], "blue": rgb[2], "radius": radius,
                                 "flags": 0x40 if track else 0})

    def glow(self, pl, mesh, x, z, rgb, lift=80, below=None):
        return pl.single(GLOW, self.drop(mesh, x, z, below, lift=lift), name="glow",
                         fields={"red": rgb[0], "green": rgb[1], "blue": rgb[2], "alpha": 140, "distance": 900,
                                 "size": 70})

    def pickup(self, pl, mesh, oid, x, z, below=None, **fields):
        name = {HEALTH: "health", AMMO: "ammo", KEY_YELLOW: "yellow key"}.get(oid, "pickup")
        return pl.single(oid, self.drop(mesh, x, z, below), name=name, fields=fields or None)


def fire_ant(p, cat) -> None:
    """kinds/fire_ant: the BlueAnt reskinned with fiery textures drawn here, one per texture slot of its model, at the
    slot's own size (the textures are this script's; the model, animations and behaviour stay the player's own)."""
    from jfgformats import models, png
    base = cat.objtypes()[0x78]
    model = models.ModelData(cat.assets).model(base["assets"][0])
    textures = {}
    for slot, (w, h, _) in enumerate(model.textures):
        px = bytearray()
        rng = __import__("random").Random(100 + slot)
        for y in range(h):
            for x in range(w):
                heat = (math.sin(x * 0.9 + slot) + math.sin(y * 0.7 - slot * 2) + rng.random() * 1.5) / 3.5
                r = 200 + int(55 * heat)
                g = 40 + int(150 * max(0.0, heat))
                b = 10 + int(30 * max(0.0, heat - 0.5))
                px += bytes((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), 255))
        path = p.kind_texture("fire_ant", slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png.write_png("RGBA", w, h, bytes(px)))
        textures[str(slot)] = path.name
    p.kinds["fire_ant"] = {"format": 1, "name": "Fire Ant", "base": "stock:objtype/0x78", "scale": 1.0,
                           "textures": textures}


def world_scripts(p, arena_door: int) -> None:
    """The world's triggers (made in the editor's Triggers window; compiled to Lua by the build) and one script written
    by hand, for the port's Lua runtime."""
    from jfgstudio import scripts

    def say(text, seconds=5):
        return {"do": "message", "text": text, "seconds": seconds}

    scripts.save_triggers(p, [
        {"name": "the portal", "level": f"stock:level/{FOREST_FIRST}", "when": {"kind": "level start"},
         "then": [say("A strange blue portal has opened just ahead...")]},
        {"name": "landing intro", "level": "pack:level/landing", "when": {"kind": "level start"},
         "then": [say("ZEPHYRA - Ants hold the north gate. The yellow key waits on the west ledge.", 6)]},
        {"name": "scout's note", "level": "pack:level/landing", "once": False,
         "when": {"kind": "talk", "at": [1150, -550], "radius": 160},
         "then": [{"do": "lines", "seconds": 5, "lines": [
             "A scout's note on the chest: 'The ants came down from the north at dawn.'",
             "'They locked the gate behind them. Thin air here: your gun cycles faster.'",
             "'If you find this, take the gun. I went west for the key.'"]}]},
        {"name": "caverns intro", "level": "pack:level/caverns", "when": {"kind": "level start"},
         "then": [say("EMBER CAVERNS - Mind the lava. The north door needs the yellow key.", 6)]},
        {"name": "fire ants", "level": "pack:level/caverns", "when": {"kind": "enter", "box": [-380, -650, 380, -100]},
         "then": [say("Fire Ants!", 2)]},
        {"name": "arena intro", "level": "pack:level/arena", "when": {"kind": "level start"},
         "then": [say("SKY ARENA - Defeat both squads to open the final gate.", 6)]},
        {"name": "both squads down", "level": "pack:level/arena", "when": {"kind": "door opens", "door": arena_door},
         "then": [say("The final gate is open! Head north.", 6), {"do": "music", "track": "zephyra_landing"},
                  {"do": "spawn", "type": f"stock:objtype/{HEALTH:#x}", "pos": [0, 30, -80], "near_player": True,
                   "fields": {"amount": 2}}]},
        {"name": "victory", "level": "pack:level/arena", "when": {"kind": "enter", "box": [-300, 1000, 300, 1500]},
         "then": [say("Victory! Zephyra is free. A new planet, Glacia, has appeared on the Zephyra Expanse map.", 7),
                  {"do": "complete planet", "planet": "zephyra"},
                  {"do": "fog", "rgb": [200, 190, 255], "near": 1000, "far": 1023, "ticks": 120}]},
        {"name": "glacia intro", "level": "pack:level/glacia", "when": {"kind": "level start"},
         "then": [say("GLACIA - Cross the frozen lake and reach the pad in the north.", 6)]},
        {"name": "glacia complete", "level": "pack:level/glacia",
         "when": {"kind": "enter", "box": [-300, 1300, 300, 1550]},
         "then": [say("GLACIA COMPLETE - Nimbus has appeared on the Zephyra Expanse map.", 7),
                  {"do": "complete planet", "planet": "glacia"}]},
        {"name": "nimbus intro", "level": "pack:level/nimbus", "when": {"kind": "level start"},
         "then": [say("NIMBUS - Mind the edges. The last island is the way out.", 6)]},
        {"name": "nimbus complete", "level": "pack:level/nimbus",
         "when": {"kind": "enter", "box": [-360, 900, 360, 1500]},
         "then": [say("NIMBUS COMPLETE - The Zephyra Expanse is free!", 8),
                  {"do": "complete planet", "planet": "nimbus"}]},
    ])
    (p.path / "scripts").mkdir(exist_ok=True)
    # Zephyra's air is thin: the starting gun fires faster and its bolts fly faster (the Weapons window).
    scripts.save_weapons(p, {"0": {"delay": 8, "speed": 140.0}})
    (p.path / "scripts" / "zephyra.lua").write_text("""\
-- Zephyra's own script (written by hand; the triggers are in triggers.json).
-- Counts the time spent in the Sky Arena and says it at the finale platform.
local arena_start = nil
local said = false

jfg.on("level", function(level)
  jfg.log("Zephyra: level " .. level)
  arena_start = (level == jfg.level_id("level_arena")) and 0 or nil
  said = false
end)

jfg.on("tick", function(level, seconds)
  if arena_start == nil or said then return end
  local x, y, z = jfg.player()
  if z and z > 1000 and x > -300 and x < 300 then
    said = true
    jfg.log(string.format("Zephyra: the arena took %.0f seconds", seconds))
  end
end)
""", encoding="utf-8", newline="\n")


def level_header(level, fog, clear, extra=None):
    hd = level.level["header"]
    hd.update({"fogNear": fog[3], "fogFar": fog[4], "fog3": fog[3], "fogR": fog[0], "fogG": fog[1], "fogB": fog[2],
               "clearR": clear[0], "clearG": clear[1], "clearB": clear[2]})
    hd.update(extra or {})


def texture_index(catalog, glb: Path, image_name: str) -> int:
    """The index of an image in the track's own texture list (a texture scroll names it), from the game's importer."""
    from jfgformats import models, png, tracks  # importable once the catalog has loaded jfgformats
    result = tracks.import_track(models.ModelData(catalog.assets), str(glb), track_id=catalog.track_count(),
                                 log=lambda *a: None)
    kind, size, seed = IMAGES[image_name]
    want = png.read_png(wk.image(png, kind, size, seed))[2]
    for key, data in result.get("newTextures", {}).items():
        if png.read_png(data)[2] == want:
            return int(key)
    raise SystemExit(f"{glb.name}: no texture made from {image_name}")


def main() -> int:
    cat = Catalog.open()
    if OUT.exists():
        import shutil
        keep = {f.name: f.read_bytes() for f in (OUT / "music").glob("*.ogg")} if (OUT / "music").exists() else {}
        shutil.rmtree(OUT)
    else:
        keep = {}
    p = proj.create(OUT, "jfgstudio_example_zephyra", "Zephyra (JFG-Studio example world)", "VibinToast")
    p.meta["description"] = ("JFG-Studio's example world: three levels reached through a portal in Goldwood's first "
                             "level, with squads, locked doors, a key, lava, water and its own music.")
    for name, data in keep.items():
        (OUT / "music" / name).write_bytes(data)
    w = World(p, cat)
    fire_ant(p, cat)

    meshes = {"landing": landing_mesh(), "caverns": caverns_mesh(), "arena": arena_mesh(), "glacia": glacia_mesh(),
              "nimbus": nimbus_mesh()}
    for name, m in meshes.items():
        wk.write_glb(m, p.geometry_path(name), IMAGES, GEN)
        print(f"geometry/{name}.glb: {m.triangles()} triangles")
    views = {name: TrackMesh(str(p.geometry_path(name))) for name in meshes}

    # 1. Zephyra Landing
    a = proj.new_level(p, "landing", "Zephyra Landing", "pack:track/landing")
    level_header(a, (170, 205, 230, 990, 1022), (120, 170, 210))
    a.level["sky"], a.level["music"] = SKY["landing"], "pack:music/zephyra_landing"
    mv, pl = views["landing"], w.placer(a)
    w.start(pl, mv, 0, -1400, 0, YAW_NORTH)
    w.start(pl, mv, 0, 1500, 1, YAW_SOUTH)
    w.exit(pl, mv, 0, 1660, "pack:level/caverns", 0, "to Ember Caverns")
    w.exit(pl, mv, 380, -1640, f"stock:level/{FOREST_FIRST}", 0, "portal home to Goldwood", radius=60)
    w.glow(pl, mv, 380, -1640, (120, 220, 255), lift=50)
    door_a = pl.free("door", "doorId")
    w.squad(pl, mv, "BlueAnt", (0, 1050), 3, [(-700, 850), (700, 850), (700, 1300), (-700, 1300)], door_id=door_a)
    w.door(pl, mv, 0, 1350, "north gate (the ants keep it shut)", {"doorId": door_a, "yaw": 0})
    w.pickup(pl, mv, CHEST, 1150, -550, contents=2)
    w.pickup(pl, mv, KEY_YELLOW, -1360, -150, below=300)
    for x, z in ((220, -1250), (0, 500), (1350, -350), (-900, 1150)):
        w.pickup(pl, mv, HEALTH, x, z, below=300)
    for x, z in ((-220, -1250), (900, 600)):
        w.pickup(pl, mv, AMMO, x, z)
    for x, z in ((-180, -1580), (180, -1580)):
        w.light(pl, mv, x, z, (255, 230, 170), radius=300)
        w.glow(pl, mv, x, z, (255, 220, 150), lift=40)
    w.light(pl, mv, 0, 1250, (255, 120, 90), radius=420)
    pl.single(AUDIO, w.drop(mv, -400, 500), w.example(AUDIO), name="river sound")
    a.objects["objects"] = pl.made

    # 2. Ember Caverns
    b = proj.new_level(p, "caverns", "Ember Caverns", "pack:track/caverns")
    level_header(b, (110, 30, 20, 985, 1020), (40, 10, 8), {"lights": 32})
    b.level["sky"], b.level["music"] = SKY["caverns"], "pack:music/zephyra_caverns"
    mv, pl = views["caverns"], w.placer(b)
    w.start(pl, mv, 0, -1600, 0, YAW_NORTH)
    w.start(pl, mv, 0, 1450, 1, YAW_SOUTH)
    w.exit(pl, mv, 0, -1760, "pack:level/landing", 1, "back to Zephyra Landing", radius=50)
    w.exit(pl, mv, 0, 1660, "pack:level/arena", 0, "to Sky Arena")
    plaza = [(-240, -490), (240, -490), (240, -10), (-240, -10)]  # 4-corner regions: the tested kind
    w.squad(pl, mv, "BlueAnt", (0, -250), 3, plaza, reskin="pack:kind/fire_ant")
    w.squad(pl, mv, "ZombieAnt", (0, 1120), 3, [(-1200, 980), (1200, 980), (1200, 1270), (-1200, 1270)], spread=220)
    w.door(pl, mv, 0, 1300, "yellow-key door", {"doorId": 0, "keyLock": 2, "yaw": 0})
    for x, z in ((300, -1500), (-500, 560), (500, 1100)):
        w.pickup(pl, mv, HEALTH, x, z)
    for x, z in ((-300, -1500), (0, 600)):
        w.pickup(pl, mv, AMMO, x, z)
    for x, z in ((-300, 480), (-220, 620), (260, 470)):
        pl.single(CRATE, w.drop(mv, x, z), w.example(CRATE), name="crate", fields={"size": 64})
    for x, z in ((-500, -1300), (500, -1300), (-600, 80), (600, -600)):
        w.light(pl, mv, x, z, (255, 110, 30), radius=500, lift=40)
    for x, z in ((-250, -250), (250, -250)):
        w.glow(pl, mv, x, z, (255, 140, 40), lift=60)
    pl.single(TEXSCROLL, w.drop(mv, 0, -1700), name="lava flow",
              fields={"texture": texture_index(cat, p.geometry_path("caverns"), "lava"), "uSpeed": 6, "vSpeed": 10})
    b.objects["objects"] = pl.made

    # 3. Sky Arena
    c = proj.new_level(p, "arena", "Sky Arena", "pack:track/arena")
    level_header(c, (60, 40, 110, 990, 1022), (20, 10, 40), {"lights": 32})
    c.level["sky"], c.level["music"] = SKY["arena"], "pack:music/zephyra_arena"
    mv, pl = views["arena"], w.placer(c)
    w.start(pl, mv, 0, -1150, 0, YAW_NORTH)
    w.start(pl, mv, 0, 1250, 1, YAW_SOUTH)
    w.exit(pl, mv, 0, -1270, "pack:level/caverns", 1, "back to Ember Caverns", radius=50)
    w.exit(pl, mv, 0, 1380, f"stock:level/{FOREST_FIRST}", 0, "victory: home to Goldwood")
    door_c = pl.free("door", "doorId")
    p.meta.setdefault("x-arena-door", door_c)  # the finale trigger watches this door (removed before saving)
    ring = [(-620, 0), (620, 0), (620, 620), (-620, 620)]  # the arena's north half: they hold the gate
    w.squad(pl, mv, "BigShieldAnt", (-350, 350), 2, ring, door_id=door_c)
    w.squad(pl, mv, "Cyborg", (350, 350), 2, ring, door_id=door_c)
    w.door(pl, mv, 0, 950, "final gate (opens when both squads are down)", {"doorId": door_c, "yaw": 0})
    for x, z in ((600, 0), (-600, 0), (0, -600), (0, 700)):
        w.pickup(pl, mv, HEALTH, x, z, below=50)
    for x, z in ((300, -500), (-300, -500)):
        w.pickup(pl, mv, AMMO, x, z, below=50)
    w.pickup(pl, mv, HEALTH, 0, -1000, amount=2)  # full health on the entry platform
    for sx, sz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        w.light(pl, mv, 450 * sx, 450 * sz, (120, 140, 255), radius=450, lift=320, track=True)
        w.glow(pl, mv, 450 * sx, 450 * sz, (160, 120, 255), lift=40, below=400)
    w.glow(pl, mv, 0, 1380, (255, 240, 160), lift=60)
    c.objects["objects"] = pl.made

    # 4. Glacia (the Expanse's second planet, opened by completing Zephyra)
    d = proj.new_level(p, "glacia", "Glacia", "pack:track/glacia")
    level_header(d, (200, 215, 235, 985, 1022), (170, 190, 220))
    d.level["sky"], d.level["music"] = SKY["glacia"], "pack:music/zephyra_landing"
    mv, pl = views["glacia"], w.placer(d)
    w.start(pl, mv, 0, -1350, 0, YAW_NORTH)
    w.exit(pl, mv, 0, 1480, f"stock:level/{FOREST_FIRST}", 0, "the way home to Goldwood")
    w.squad(pl, mv, "BlueAnt", (0, 100), 3, [(-600, -250), (600, -250), (600, 450), (-600, 450)])
    w.squad(pl, mv, "Cyborg", (0, 800), 2, [(-1100, 600), (1100, 600), (1100, 1050), (-1100, 1050)], spread=250)
    for x, z in ((300, -1200), (-1000, 300), (1000, 300), (0, 1100)):
        w.pickup(pl, mv, HEALTH, x, z)
    for x, z in ((-300, -1200), (600, 900)):
        w.pickup(pl, mv, AMMO, x, z)
    for x, z in ((-450, -700), (450, -650), (-900, 150), (900, 100)):
        w.light(pl, mv, x, z, (170, 220, 255), radius=420, lift=200)
    w.glow(pl, mv, 0, 1420, (200, 240, 255), lift=60)
    d.objects["objects"] = pl.made

    # 5. Nimbus (the Expanse's third planet, opened by completing Glacia)
    e = proj.new_level(p, "nimbus", "Nimbus", "pack:track/nimbus")
    level_header(e, (150, 120, 190, 990, 1022), (90, 70, 140), {"lights": 32})
    e.level["sky"], e.level["music"] = SKY["nimbus"], "pack:music/zephyra_arena"
    mv, pl = views["nimbus"], w.placer(e)
    w.start(pl, mv, 0, -1250, 0, YAW_NORTH)
    w.exit(pl, mv, 0, 1330, f"stock:level/{FOREST_FIRST}", 0, "the way home to Goldwood")
    w.squad(pl, mv, "ZombieAnt", (0, 150), 3, [(-380, -300), (380, -300), (380, 300), (-380, 300)], spread=200)
    w.squad(pl, mv, "BigShieldAnt", (0, 1150), 2, [(-250, 950), (250, 950), (250, 1300), (-250, 1300)])
    for x, z in ((200, -1050), (-350, -50), (350, -50)):
        w.pickup(pl, mv, HEALTH, x, z)
    w.pickup(pl, mv, HEALTH, -150, -1050, amount=2)
    w.pickup(pl, mv, AMMO, 0, -350)
    for x, z in ((0, -1100), (0, -50), (0, 1150)):
        w.light(pl, mv, x, z, (220, 190, 255), radius=500, lift=200)
    w.glow(pl, mv, 0, 1300, (255, 230, 180), lift=60)
    e.objects["objects"] = pl.made

    # Goldwood's first level: the portal, just ahead of where a new game puts Juno
    f = proj.edit_stock_level(p, "forest_portal", FOREST_FIRST, "Forest First", FOREST_FIRST_TRACK)
    pl = w.placer(f)
    import struct
    start0 = next(e for e in cat.objlist(cat.levels()[FOREST_FIRST]["objectList1"]) if e[0] == START
                  and e[1][0x0B] == 0)
    sx, sy, sz = struct.unpack_from(">hhh", start0[1], 4)
    facing = (start0[1][0x0C] << 10) / 65536 * 2 * math.pi  # the hero faces -z at yaw 0
    px, pz = round(sx - math.sin(facing) * 300), round(sz - math.cos(facing) * 300)
    portal = pl.single(EXIT, (px, sy, pz), name="portal to Zephyra", fields={"radius": 70, "startPoint": 0})
    portal["target"] = "pack:level/landing"
    pl.single(GLOW, (px, sy + 50, pz), name="portal glow",
              fields={"red": 120, "green": 220, "blue": 255, "alpha": 70, "distance": 900, "size": 80})
    pl.single(LIGHT, (px, sy + 60, pz), name="portal light",
              fields={"red": 120, "green": 220, "blue": 255, "radius": 300, "flags": 0x40})
    f.objects["objects"] = pl.made

    # The Zephyra Expanse: the pack's own galaxy (L / R on the planet map). Zephyra is its home, open from the start;
    # Glacia and Nimbus stay hidden until the world before them is completed (the triggers' "complete planet").
    p.galaxies["expanse"] = {"format": 1, "name": "Zephyra Expanse", "home": "zephyra"}

    def planet(name, title, level, disc, unlock, route=None):
        p.planets[name] = {"format": 1, "displayName": {"en": title}, "galaxy": "expanse", "disc": disc,
                           "unlock": unlock, "routes": [route] if route else [],
                           "entryLevel": dict.fromkeys(("vela", "juno", "lupus"), f"pack:level/{level}")}

    planet("zephyra", "Zephyra", "landing", {"index": 2, "x": 110, "y": 420, "radius": 16, "colour": [150, 210, 255]},
           "start")
    planet("glacia", "Glacia", "glacia", {"index": 4, "x": 290, "y": 420, "radius": 13, "colour": [220, 240, 255]},
           {"completed": "zephyra"}, {"from": "pack:world/zephyra", "dir": "right", "back": "left"})
    planet("nimbus", "Nimbus", "nimbus", {"index": 3, "x": 290, "y": 240, "radius": 15, "colour": [230, 200, 255]},
           {"completed": "glacia"}, {"from": "pack:world/glacia", "dir": "up", "back": "down"})
    arena_door = p.meta.pop("x-arena-door")
    p.save()
    world_scripts(p, arena_door)
    print(f"wrote {OUT}: {len(p.levels)} levels, "
          f"{sum(len(lv.objects['objects']) for lv in p.levels.values())} objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
