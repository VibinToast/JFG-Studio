import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import agent, formats, mcp_server
from jfgstudio import validate as val
from jfgstudio.gui.viewport import floor_of
from tests.fixtures import sample_project


def _quad(x0, z0, x1, z1, y):
    a, b, c, d = (x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0)
    return [[a, b, c], [a, c, d]]


class QuadMesh:
    """A track without jfgformats, from flat quads per surface kind (TrackMesh's interface)."""

    def __init__(self, solid, water=(), kill=()):
        def tris(quads):
            return np.array(quads, "f8").reshape(-1, 3, 3) if quads else np.zeros((0, 3, 3))
        self.tris = {"solid": tris(solid), "water": tris(water), "kill": tris(kill)}
        self.collision = self.tris["solid"]
        self.bounds = (np.array([-2000, -500, -2000]), np.array([2000, 100, 2000]))
        self.images = {}

    def floor_height(self, x, z, below=None):
        return floor_of(self.collision, x, z, below)

    def kind_triangles(self, kind):
        return self.tris.get(kind, np.zeros((0, 3, 3)))


def _block(x0, z0, x1, z1, y0, y1):
    """A box's top and four walls (no bottom, as a modelled block standing on ground)."""
    def wall(a, b):
        p, q, r, s = (a[0], y0, a[1]), (b[0], y0, b[1]), (b[0], y1, b[1]), (a[0], y1, a[1])
        return [[p, q, r], [p, r, s]]
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    return _quad(x0, z0, x1, z1, y1) + sum((wall(corners[k], corners[(k + 1) % 4]) for k in range(4)), [])


KILL = _quad(-2000, -2000, 2000, 2000, -500)


def fake_mesh():
    """Flat ground at 0 with a riverbed at -30 under water at -5 (z 300-500), a raised deck at 100 (x 600-800), and a
    kill floor at -500 under a pit (x -900 to -700) that has no ground."""
    ground = (_quad(-1000, -1000, -900, 1000, 0) + _quad(-700, -1000, 1000, 300, 0)
              + _quad(-700, 500, 1000, 1000, 0) + _quad(-700, 300, 1000, 500, -30) + _quad(600, -200, 800, 0, 100))
    return QuadMesh(ground, _quad(-700, 300, 1000, 500, -5), KILL)


def reshaped_mesh():
    """The same level after a new export: the ground at 20, no deck, the pit kept (x -900 to -700, from z -500), a
    hill (top 100) over x -100..100, z -700..-500 with no ground under it, and a block (top 60) standing on the ground
    over x 300..500, z -400..-200."""
    ground = (_quad(-1000, -1000, 1000, -700, 20) + _quad(-1000, -500, -900, 1000, 20)
              + _quad(-700, -500, 1000, 1000, 20) + _quad(-1000, -700, -100, -500, 20)
              + _quad(100, -700, 1000, -500, 20) + _quad(-100, -700, 100, -500, 100)
              + _block(300, -400, 500, -200, 20, 60))
    return QuadMesh(ground, (), KILL)


# Made-up catalog types (names and numbers are the tests' own).
TYPES = {0x29: {"name": "Bug", "behaviour": 24, "uses": 3, "examples": [[10, 4]]},
         0x4F: {"name": "Squadron", "behaviour": 70, "uses": 2, "examples": [[10, 2]]},
         0x1AD: {"name": "Gate", "behaviour": 7, "uses": 1, "examples": [[11, 7]]},
         0x61: {"name": "Node", "behaviour": 0, "uses": 4, "examples": [[12, 1]]},
         0x60: {"name": "Link", "behaviour": 0, "uses": 4, "examples": [[12, 2]]}}


class FakeCatalog:
    """Made-up stock levels (the tests' own names)."""

    def levels(self):
        return [{"id": i, "name": f"Place {i}", "world": i // 10, "track": i + 1} for i in range(25)]

    def stock_entry(self, list_id, index):
        entry = bytearray(0x50)
        entry[0x24] = 0x13  # a squadron's kind byte: high bits kept, low 4 bits the mode
        return bytes(entry)


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        sample_project(Path(self.tmp.name))
        self.s = agent.Session()
        self.s._catalog_error = "tests have no catalog"  # never the player's ROM, even when one is found nearby
        self.s.open(Path(self.tmp.name) / "sample.jfgproj")
        self.mesh = fake_mesh()
        self.s._mesh = lambda name: self.mesh

    def tearDown(self):
        if self.old_config is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old_config
        self.tmp.cleanup()

    def with_catalog(self):
        self.s._catalog, self.s._catalog_error, self.s._types = FakeCatalog(), None, TYPES

    def errors(self):
        return [p for p in val.validate(self.s.p) if p.severity == val.ERROR]


class Project(AgentTest):
    def test_summary_and_level_info(self):
        summary = self.s.summary()
        self.assertEqual(summary["id"], "test.sample")
        self.assertEqual([lv["name"] for lv in summary["levels"]], ["gemini", "home"])
        self.assertTrue(summary["catalog"].startswith("not available"))
        info = self.s.level_info("gemini")
        door = next(o for o in info["objects"] if o["uid"] == "door1")
        self.assertEqual(door["schema"], "door")
        with self.assertRaises(agent.AgentError):
            self.s.level_info("nowhere")

    def test_set_level_checks_and_undoes(self):
        self.s.set_level("home", {"header": {"fogR": 40}, "name": "Home Base"})
        level = self.s.p.levels["home"].level
        self.assertEqual((level["header"]["fogR"], level["name"]), (40, "Home Base"))
        self.assertEqual(level["header"]["tune"], 4)  # the other header fields stay
        with self.assertRaises(agent.AgentError):
            self.s.set_level("home", {"name": "x" * 40})  # over 31 characters
        with self.assertRaises(agent.AgentError):
            self.s.set_level("home", {"music": "pack:music/none"})  # no such file
        with self.assertRaises(agent.AgentError):
            self.s.set_level("home", {"colour": 1})
        self.s.undo("home")
        self.assertEqual(self.s.p.levels["home"].level["header"]["fogR"], 0)
        saved = json.loads((self.s.p.path / "levels" / "home" / "level.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["header"]["fogR"], 0)  # saved as it goes


class Placing(AgentTest):
    def test_place_on_floor_with_named_values(self):
        health = self.s.place("home", "health", [100, 100])[0]
        self.assertEqual(health["pos"], [100, 2, 100])  # the floor, lifted a little
        start = self.s.place("home", "startpoint", [650, -100], fields={"startPoint": 1})[0]
        self.assertEqual(start["pos"][1], 108)  # on the deck, walkers lifted more
        under = self.s.place("home", "startpoint", [650, 50, -100], fields={"startPoint": 2})[0]
        self.assertEqual(under["pos"][1], 8)  # under the deck: the floor at or below y 50
        light = self.s.place("home", "light", [0, 0], lift=60)[0]
        self.assertEqual(light["pos"][1], 62)
        self.with_catalog()  # 0x1AD is a door by its behaviour, which the catalog knows
        key =self.s.place("home", "0x1AD", [0, 0], fields={"keyLock": "yellow key"}, example=[11, 7])[0]
        self.assertEqual((key["fields"]["keyLock"], key["schema"]), (2, "door"))
        for bad in ({"keyLock": "pink key"}, {"nope": 1}, {"radius": 300}):
            with self.assertRaises(agent.AgentError):
                self.s.place("home", "exit", [0, 0], fields=bad)
        with self.assertRaises(agent.AgentError):
            self.s.place("home", "health", [-800, 0])  # the pit has no floor
        self.assertEqual(self.s.surface_at("home", [[-800, 0]])[0], {"x": -800.0, "z": 0.0, "floor": None,
                                                                     "kill": -500.0})

    def test_partial_types_need_a_stock_entry(self):
        with self.assertRaises(agent.AgentError):
            self.s.place("home", "0x29", [0, 0])  # no catalog to pick one
        self.with_catalog()
        bug = self.s.place("home", "0x29", [0, 0], fields=None)[0]
        self.assertEqual(bug["stock"], {"list": 10, "entry": 4})
        self.assertEqual(bug["typeName"], "Bug")

    def test_squad_locks_a_new_door(self):
        self.with_catalog()
        made = self.s.place_squad("home", [0, -400], "0x29", members=3, door="new", door_type="0x1AD",
                                  door_pos=[0, 0])
        squadron = next(o for o in made if o.get("schema") == "squadron")
        door = next(o for o in made if o.get("schema") == "door")
        members = [o for o in made if o.get("schema") == "squadmember"]
        self.assertEqual(len(members), 3)
        self.assertEqual(squadron["fields"]["doorId"], door["fields"]["doorId"])
        self.assertEqual(door["fields"]["keyLock"], agent.DOOR_LOCKED)
        self.assertEqual(sum(o.get("schema") == "regionpoint" for o in made), 4)
        self.assertEqual({m["fields"]["squadron"] for m in members}, {squadron["fields"]["squadronId"]})
        for o in made:  # the catalog's names are the player's game data: never in the project
            self.assertNotIn("bug", (o["uid"] + o.get("name", "")).lower())
        self.assertFalse(self.errors())

    def test_squad_locks_an_existing_door(self):
        self.with_catalog()
        made = self.s.place_squad("gemini", [0, -400], "0x29", members=2, door="door1")
        squadron = next(o for o in made if o.get("schema") == "squadron")
        door = next(o for o in made if o["uid"] == "door1")
        self.assertEqual(squadron["fields"]["doorId"], door["fields"]["doorId"])

    def test_connect_and_edit(self):
        made = self.s.connect("home", [200, 0], "gemini", [0, 100], arrive_yaw=180)
        exit_, start = made
        self.assertEqual(exit_["target"], "pack:level/gemini")
        self.assertEqual(exit_["fields"]["startPoint"], start["fields"]["startPoint"])
        self.assertEqual(start["fields"]["startPoint"], 1)  # gemini already has start point 0
        self.assertEqual(start["fields"]["yaw"], 32)  # half a turn in angle10
        self.assertFalse([p for p in self.errors() if p.rule == "exit-target"])
        moved = self.s.update_object("home", exit_["uid"], fields={"radius": 90}, pos=[300, 0, 0], name="Out")
        self.assertEqual((moved["fields"]["radius"], moved["pos"], moved["name"]), (90, [300, 0, 0], "Out"))
        self.s.undo("home")
        back = next(o for o in self.s.level_info("home")["objects"] if o["uid"] == exit_["uid"])
        self.assertEqual(back["fields"]["radius"], 70)
        self.s.delete_objects("home", [exit_["uid"]])
        self.assertFalse([o for o in self.s.level_info("home")["objects"] if o["uid"] == exit_["uid"]])


class Geometry(AgentTest):
    def test_surfaces_and_flat_spots(self):
        river, ground = self.s.surface_at("home", [[0, 400], [0, 0]])
        self.assertEqual((river["floor"], river["water"], river["depth"]), (-30.0, -5.0, 25.0))
        self.assertEqual((ground["floor"], ground["kind"]), (0.0, "solid"))
        spots = self.s.find_flat("home", 0, 400, radius=40, search=300, step=50)
        self.assertTrue(spots)
        for s in spots:  # dry and off the riverbed's edges
            self.assertFalse(260 <= s["z"] <= 540)
        hit = self.s.raycast("home", [0, 50, 0], [0, -1, 0])
        self.assertEqual(hit["point"], [0.0, 0.0, 0.0])


class Reimport(AgentTest):
    def test_reimport_keeps_objects_and_finds_misplaced_ones(self):
        health = self.s.place("home", "health", [100, 100])[0]["uid"]                                  # y 2
        deck = self.s.place("home", "startpoint", [650, -100], fields={"startPoint": 1})[0]["uid"]     # y 108
        hill = self.s.place("home", "startpoint", [0, -600], fields={"startPoint": 2})[0]["uid"]       # y 8
        light = self.s.place("home", "light", [300, 300], lift=60)[0]["uid"]
        pit = self.s.place("home", "ammo", [-800, 0, 0], on_floor=False)[0]["uid"]
        block = self.s.place("home", "ammo", [400, 22, -300], on_floor=False)[0]["uid"]  # on the new ground (20)
        self.assertEqual({f["uid"]: f["problem"] for f in self.s.check_placement("home")}, {pit: "over a pit"})

        new = Path(self.tmp.name) / "field_v2.glb"
        new.write_bytes(b"new export")
        geometry = self.s.p.geometry_path("field")
        self.mesh = reshaped_mesh()  # what the new file holds
        out = self.s.reimport_geometry("home", str(new))
        self.assertEqual(geometry.read_bytes(), b"new export")
        self.assertEqual(out["sharedWith"], ["gemini"])  # both levels are on pack:track/field
        found = {f["uid"]: f for f in out["placement"]["home"]}
        self.assertEqual(found[health]["problem"], "sunk")
        self.assertEqual(found[health]["standOn"], [100, 22, 100])
        self.assertEqual((found[deck]["problem"], found[deck]["standOn"]), ("floating", [650, 28, -100]))
        self.assertEqual((found[hill]["problem"], found[hill]["standOn"]), ("buried", [0, 108, -600]))
        self.assertEqual((found[block]["problem"], found[block]["standOn"]), ("buried", [400, 62, -300]))
        self.assertNotIn(light, found)  # lights may hang anywhere
        self.assertEqual(found[pit]["problem"], "over a pit")
        self.assertIn("placement", {p["rule"] for p in self.s.validate()["problems"]})

        settled = self.s.settle_objects("home")
        self.assertEqual({m["uid"] for m in settled["moved"]}, {health, deck, hill, block, "start0"})  # start0 at 0
        self.assertEqual(settled["left"], [{"uid": pit, "problem": "over a pit"}])
        self.assertEqual([f["uid"] for f in self.s.check_placement("home")], [pit])

        self.s.undo("home")  # the settling
        self.s.undo("home")  # the re-import
        self.assertEqual(geometry.read_bytes(), b"glTF")
        with self.assertRaises(agent.AgentError):
            self.s.reimport_geometry("home", str(Path(self.tmp.name) / "missing.glb"))

    def test_stock_tracks_are_not_reimported(self):
        self.s.set_level("home", {"track": "stock:track/3"})
        with self.assertRaises(agent.AgentError):  # the game's own track: not the pack's file to replace
            self.s.reimport_geometry("home", "x.glb")


class TrialFindings(AgentTest):
    """What the first agent trial (a three-level planet from a brief) asked for."""

    def test_types_levels_and_facing(self):
        short = self.s.object_types()
        self.assertNotIn("types", short)  # the schemas only, short, unless asked for the game's types
        self.assertNotIn("description", short["schemas"][0])
        door = self.s.describe_type("door")  # shared by a behaviour: its fields, not an error
        self.assertIn("keyLock", [f["name"] for f in door["fields"]])
        self.with_catalog()
        self.assertEqual(self.s.describe_type("door")["ids"], ["0x1AD"])
        self.assertEqual(self.s.describe_type("door")["modelSizes"], {})  # the made-up types have no models
        self.assertIsNone(self.s.model_size(0x29))
        yaw = next(f for f in self.s.describe_type("startpoint")["fields"] if f["name"] == "yaw")
        self.assertEqual(yaw["perTurn"], 64)
        start = self.s.place("home", "startpoint", [0, 0], fields={"startPoint": 5}, facing=180)[0]
        self.assertEqual(start["fields"]["yaw"], 32)
        self.assertEqual(self.s.update_object("home", start["uid"], facing=90)["fields"]["yaw"], 16)
        with self.assertRaises(agent.AgentError):
            self.s.place("home", "health", [0, 0], facing=90)  # no yaw field
        levels = self.s.stock_levels()
        self.assertEqual((levels["newGame"], levels["levels"][2]), (agent.NEW_GAME_LEVEL, "2: Place 2 (world 0)"))
        self.assertEqual([int(lv.split(":")[0]) for lv in self.s.stock_levels("place 2")["levels"]],
                         [2] + list(range(20, 25)))
        self.assertEqual(len(self.s.stock_levels(world=1)["levels"]), 10)
        self.assertNotIn("schemas", self.s.object_types(search="bug"))  # a search gives only its matches
        made = self.s.edit_stock_level("portal_level", agent.NEW_GAME_LEVEL)
        self.assertEqual(made["track"], f"stock:track/{agent.NEW_GAME_LEVEL + 1}")  # the catalog's, not a guess
        self.assertEqual(self.s.p.levels["portal_level"].level["name"], "portal_level")  # no game names
        tools = {t["name"]: t for t in mcp_server.tool_list()}
        planet = tools["set_planet"]["inputSchema"]["properties"]["planet"]
        self.assertIn("unlock", planet["properties"])
        self.assertNotIn("format", planet["required"])

    def test_validator_rules(self):
        def rules():
            return [(p.rule, p.uid or p.file) for p in val.validate(self.s.p)
                    if p.rule in ("arrival", "key", "reachable")]
        self.assertEqual(rules(), [])
        self.s.place("home", "exit", [300, 0], fields={"radius": 70}, target="pack:level/gemini")
        near = self.s.place("home", "startpoint", [340, 0], fields={"startPoint": 3})[0]["uid"]
        self.assertEqual(rules(), [("arrival", near)])
        self.s.delete_objects("home", [near])
        self.s.update_object("gemini", "door1", fields={"keyLock": "yellow key"})
        self.assertEqual(rules(), [("key", "door1")])
        self.s.place("home", "0x258", [0, 0])  # the yellow key
        self.assertEqual(rules(), [])
        planet = dict(self.s.p.planets["gemini"], unlock={"entered": "gemini"})
        self.s.set_planet("gemini", {k: v for k, v in planet.items() if k != "format"})
        self.assertEqual(rules(), [])  # home (on no planet) has an exit into it: a way in
        exit_ = next(o["uid"] for o in self.s.level_info("home")["objects"] if o.get("target"))
        self.s.delete_objects("home", [exit_])
        self.assertEqual(rules(), [("reachable", "planets/gemini.json")])

    def test_fog_closer_than_the_game(self):
        def fog():
            return [p.uid or p.file for p in val.validate(self.s.p) if p.rule == "fog"]
        self.assertEqual(fog(), [])
        zone = self.s.place("home", "fogzone", [0, 0], fields={"near": 150, "far": 700})[0]["uid"]
        self.s.set_level("home", {"header": {"fogNear": 700, "fogFar": 1020}})
        self.assertEqual(sorted(fog()), sorted([zone, "levels/home/level.json"]))
        self.s.update_object("home", zone, fields={"near": 975})
        self.s.set_level("home", {"header": {"fogNear": 988}})
        self.assertEqual(fog(), [])

    def test_preview(self):
        self.with_catalog()
        self.s.place_squad("home", [0, -400], "0x29", members=2, door="new", door_type="0x1AD", door_pos=[0, -150])
        out = Path(self.tmp.name) / "home.png"
        shot = self.s.preview("home", 256, str(out))
        self.assertTrue(shot["png"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(out.read_bytes(), shot["png"])
        self.assertEqual(shot["legend"]["up"], "-z")
        content = mcp_server.call(self.s, "level_preview", {"level": "home", "size": 128})["content"]
        self.assertEqual([c["type"] for c in content], ["image", "text"])


class Trial2Findings(AgentTest):
    """What the second agent trial (a planet entered through a portal in a game level) asked for."""

    def base_starts(self, numbers):
        def objects(level):
            return [{"type": "stock:objtype/0xD", "schema": "startpoint", "pos": [0, 0, 0],
                     "fields": {"player": 0, "startPoint": n, "yaw": 0}, "base": True}
                    for n in numbers] if level.base is not None else []
        self.s.base_objects = objects

    def test_an_edit_knows_its_game_level(self):
        self.with_catalog()
        self.s.edit_stock_level("portal", agent.NEW_GAME_LEVEL)
        self.base_starts(range(5))  # the game level's own start points 0-4
        exit_, arrival = self.s.connect("home", [200, 0], "portal", [0, 100])
        self.assertEqual((arrival["fields"]["startPoint"], exit_["fields"]["startPoint"]), (5, 5))
        back = self.s.connect("home", [300, 0], "portal", arrive_start_point=2)
        self.assertEqual([o["fields"]["startPoint"] for o in back], [2])  # only the exit: start point 2 is there
        with self.assertRaises(agent.AgentError):
            self.s.connect("home", [400, 0], "portal", arrive_start_point=9)
        self.assertEqual(len(self.s.level_info("portal")["baseLevel"]["objects"]), 5)
        self.assertTrue(self.s.preview("portal", 128)["png"])
        self.s.update_object("portal", arrival["uid"], fields={"startPoint": 0})
        clash = [p for p in self.s.validate()["problems"] if p["rule"] == "start-clash"]
        self.assertEqual([(p["severity"], p["uid"]) for p in clash], [("error", arrival["uid"])])

    def test_doors_stand_in_their_opening(self):
        self.with_catalog()
        gate = _quad(-1000, -1000, 1000, 1000, 0) + _block(-100, -20, 100, 20, 0, 460)  # a wall, its opening unmodelled
        self.mesh = QuadMesh(gate)
        self.assertEqual(self.s.place("home", "0x1AD", [0, 0])[0]["pos"][1], 2)  # the ground, not the wall's top
        self.mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0) + _block(-100, -20, 100, 20, 0, 100))
        self.assertEqual(self.s.place("home", "0x1AD", [0, 0])[0]["pos"][1], 102)  # no room under: the platform

    def test_lava_needs_ground_under_it(self):
        class LavaMesh(QuadMesh):
            def __init__(self, solid, lava):
                super().__init__(solid)
                self.tris["lava"] = np.array(lava, "f8").reshape(-1, 3, 3)
                self.collision = np.concatenate([self.tris["solid"], self.tris["lava"]])

        def lava_rules():
            return [p for p in self.s.validate()["problems"] if p["rule"] == "lava-floor"]
        shore = _quad(-1000, -1000, 1000, 0, 0)
        self.mesh = LavaMesh(shore, _quad(-1000, 0, 1000, 600, -20))  # a lava lake over nothing
        self.assertEqual(len(lava_rules()), 2)  # home and gemini share the track
        self.assertIsNone(self.s.surface_at("home", [[0, 300]])[0]["underLava"])
        self.mesh = LavaMesh(shore + _quad(-1000, 0, 1000, 600, -50), _quad(-1000, 0, 1000, 600, -20))
        self.assertEqual(lava_rules(), [])
        self.assertEqual(self.s.surface_at("home", [[0, 300]])[0]["underLava"], -50.0)

    def test_squad_members_kept_dry(self):
        self.with_catalog()
        made = self.s.place_squad("home", [0, 250], "0x29", members=4, spread=80)
        members = [o for o in made if o.get("schema") == "squadmember"]
        self.assertEqual(len(members), 4)
        for m in members:  # the river (water) runs from z 300 to 500
            self.assertFalse(300 <= m["pos"][2] <= 500, m["pos"])
        self.assertFalse([o for o in made if "warning" in o])


class Prefabs(AgentTest):
    def test_key_doors_pick_free_colours(self):
        self.with_catalog()
        first = self.s.place_key_door("home", "0x1AD", [0, 0], [200, 0])
        second = self.s.place_key_door("home", "0x1AD", [0, -300], [200, -300])
        self.assertEqual((first[0]["fields"]["keyLock"], first[1]["type"]), (2, "stock:objtype/0x258"))
        self.assertEqual((second[0]["fields"]["keyLock"], second[1]["type"]), (3, "stock:objtype/0x259"))
        named = self.s.place_key_door("home", "0x1AD", [0, -600], [200, -600], colour="blue key")
        self.assertEqual(named[1]["type"], "stock:objtype/0x261")
        with self.assertRaises(agent.AgentError):
            self.s.place_key_door("home", "0x1AD", [0, 0], [0, 0], colour="pink key")
        with self.assertRaises(agent.AgentError):
            self.s.place_key_door("home", "health", [0, 0], [0, 0])  # not a door
        self.assertFalse([p for p in self.s.validate()["problems"] if p["rule"] == "key"])

    def test_switch_doors(self):
        self.with_catalog()
        first = self.s.place_switch_door("home", [0, -200], door="new", door_type="0x1AD", door_pos=[0, 0],
                                         switch_lift=60)
        switch = next(o for o in first if o.get("schema") == "target")
        door = next(o for o in first if o.get("schema") == "door")
        self.assertEqual((switch["fields"]["doorId"], door["fields"]["doorId"]), (1, 1))
        self.assertEqual(switch["fields"]["gameFlag"], 103)
        self.assertEqual(switch["pos"][1], 62)  # lifted onto a wall
        self.assertNotIn("keyLock", door["fields"])  # the switch does the locking
        second = self.s.place_switch_door("home", [300, -200], door="new", door_type="0x1AD", door_pos=[300, 0])
        self.assertEqual([o["fields"]["gameFlag"] for o in second if o.get("schema") == "target"], [104])
        shared = self.s.place_switch_door("gemini", [100, 0], door="door1")  # a door a squadron locks too
        self.assertEqual(next(o for o in shared if o.get("schema") == "target")["fields"]["doorId"], 1)
        unsaved = self.s.place_switch_door("home", [600, -200], door="new", door_type="0x1AD", door_pos=[600, 0],
                                           saved=False)
        self.assertEqual([o["fields"]["gameFlag"] for o in unsaved if o.get("schema") == "target"], [0])
        for bad in (5, 400):  # a global flag; a tribal bit
            with self.assertRaises(agent.AgentError):
                self.s.place_switch_door("home", [0, 0], door="new", door_type="0x1AD", door_pos=[0, 100], flag=bad)
        self.assertFalse([p for p in self.s.validate()["problems"] if p["severity"] == "error"])
        self.s.update_object("home", switch["uid"], fields={"gameFlag": 50, "doorId": 99})
        rules = {(p["rule"], p["severity"]) for p in self.s.validate()["problems"] if p.get("uid") == switch["uid"]}
        self.assertEqual(rules, {("game-flag", "error"), ("switch-door", "error")})

    def test_patrol_for_a_squad(self):
        self.with_catalog()
        squad = self.s.place_squad("home", [0, -300], "0x29", members=2)
        sq = next(o["uid"] for o in squad if o.get("schema") == "squadron")
        made = self.s.place_patrol("home", [[0, -250], [400, -250], [400, -600]], loop=True, squadron=sq,
                                   start_at_load=True, wait=30)
        nodes = [o for o in made if o.get("schema") == "patrolpoint"]
        links = [o for o in made if o.get("schema") == "patrolpath"]
        self.assertEqual(sorted(n["fields"]["node"] for n in nodes), [1, 2, 3])
        self.assertEqual(len(links), 3)  # a ring
        self.assertEqual(sum(n["fields"].get("startAtLoad", 0) for n in nodes), 1)
        self.assertEqual({n["fields"]["wait"] for n in nodes}, {30})
        self.assertEqual(next(o for o in made if o.get("uid") == sq)["fields"]["kind"], 0x16)  # 0x13: high bits kept
        self.assertFalse([p for p in self.s.validate()["problems"] if p["rule"] == "patrol"])
        chain = self.s.place_patrol("home", [[-500, 0], [-500, 300]])
        self.assertEqual(sorted(n["fields"]["node"] for n in chain if n.get("schema") == "patrolpoint"), [4, 5])
        with self.assertRaises(agent.AgentError):
            self.s.place_patrol("home", [[0, 0]])


class Kinds(AgentTest):
    def setUp(self):
        super().setUp()
        try:
            formats.load()
        except formats.FormatsUnavailable as e:
            self.skipTest(str(e))
        self.with_catalog()
        self.s._base_slots = lambda oid: [(16, 16, "none"), (8, 32, "mask")]  # a made-up model's texture slots

    def test_make_paint_and_place_a_kind(self):
        from jfgformats import png
        slots = self.s.kind_slots("0x29")["slots"]
        self.assertEqual([(x["width"], x["height"], x["transparency"]) for x in slots],
                         [(16, 16, "none"), (8, 32, "mask")])
        with self.assertRaises(agent.AgentError):
            self.s.paint_kind_texture("red_bug", [200, 30, 30])  # not made yet
        made = self.s.make_kind("red_bug", "Red Bug", "0x29", scale=1.5)
        self.assertEqual((made["base"], made["scale"], made["unpainted"]), ("stock:objtype/0x29", 1.5, [0, 1]))
        for pattern in agent.PATTERNS:
            self.s.paint_kind_texture("red_bug", [200, 30, 30], [255, 200, 0], pattern)
        for k, size in ((0, (16, 16)), (1, (8, 32))):
            w, h, _ = png.read_png(self.s.p.kind_texture("red_bug", k).read_bytes())
            self.assertEqual((w, h), size)
        self.assertEqual(self.s.make_kind("red_bug", "Red Bug", "0x29")["unpainted"], [])
        wrong = Path(self.tmp.name) / "wrong.png"
        wrong.write_bytes(png.write_png("RGBA", 4, 4, bytes(64)))
        with self.assertRaises(agent.AgentError):
            self.s.make_kind("red_bug", "Red Bug", "0x29", textures={"0": str(wrong)})  # slot 0 is 16 x 16
        right = Path(self.tmp.name) / "right.png"
        right.write_bytes(png.write_png("RGBA", 8, 32, bytes(8 * 32 * 4)))
        self.s.make_kind("red_bug", "Red Bug", "0x29", textures={"1": str(right)})
        self.assertEqual(self.s.p.kind_texture("red_bug", 1).read_bytes(), right.read_bytes())
        squad = self.s.place_squad("home", [0, -300], "pack:kind/red_bug", members=2)
        members = [o for o in squad if o.get("schema") == "squadmember"]
        self.assertEqual({m["type"] for m in members}, {"pack:kind/red_bug"})
        self.assertFalse(any("bug" in (o["uid"] + o.get("name", "")).lower() and "red" not in o["uid"]
                             for o in squad))  # named after the kind, never the catalog
        self.assertFalse([p for p in self.s.validate()["problems"] if p["severity"] == "error"])
        with self.assertRaises(agent.AgentError):
            self.s.paint_kind_texture("red_bug", [1, 2, 3], pattern="plaid")


class Server(AgentTest):
    def test_protocol(self):
        init = mcp_server.handle(self.s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(mcp_server.handle(self.s, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = mcp_server.handle(self.s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertIn("place_squad", {t["name"] for t in tools})
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
        ok = mcp_server.call(self.s, "place", {"level": "home", "type": "health", "pos": [0, 0]})
        self.assertFalse(ok["isError"])
        self.assertEqual(json.loads(ok["content"][0]["text"])[0]["pos"], [0, 2, 0])
        bad = mcp_server.call(self.s, "place", {"level": "home", "type": "nothing", "pos": [0, 0]})
        self.assertTrue(bad["isError"])
        self.assertTrue(mcp_server.call(self.s, "place", {"level": "home"})["isError"])  # missing arguments
        self.assertIn("Workflow", mcp_server.call(self.s, "guide", {})["content"][0]["text"])
        unknown = mcp_server.handle(self.s, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(unknown["error"]["code"], -32601)

    def test_serve_over_stdio(self):
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "open_project", "arguments": {"path": str(self.s.p.path)}}}]
        stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"not json\n")
        stdout = io.BytesIO()
        mcp_server.serve(None, stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, None])
        self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"])["id"], "test.sample")
        self.assertEqual(replies[2]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
