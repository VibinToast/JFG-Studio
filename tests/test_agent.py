import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import agent, mcp_server
from jfgstudio import validate as val
from jfgstudio.gui.viewport import floor_of
from tests.fixtures import sample_project


def _quad(x0, z0, x1, z1, y):
    a, b, c, d = (x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0)
    return [[a, b, c], [a, c, d]]


class FakeMesh:
    """A track without jfgformats: flat ground at 0 with a riverbed at -30 under water at -5 (z 300-500), a raised
    deck at 100 (x 600-800), and a kill floor at -500 under a pit (x -900 to -700) that has no ground."""

    def __init__(self):
        ground = (_quad(-1000, -1000, -900, 1000, 0) + _quad(-700, -1000, 1000, 300, 0)
                  + _quad(-700, 500, 1000, 1000, 0) + _quad(-700, 300, 1000, 500, -30) + _quad(600, -200, 800, 0, 100))
        self.tris = {"solid": np.array(ground, "f8"), "water": np.array(_quad(-700, 300, 1000, 500, -5), "f8"),
                     "kill": np.array(_quad(-2000, -2000, 2000, 2000, -500), "f8")}
        self.collision = self.tris["solid"]
        self.bounds = (np.array([-2000, -500, -2000]), np.array([2000, 100, 2000]))
        self.images = {}

    def floor_height(self, x, z, below=None):
        return floor_of(self.collision, x, z, below)

    def kind_triangles(self, kind):
        return self.tris.get(kind, np.zeros((0, 3, 3)))


# Made-up catalog types (names and numbers are the tests' own).
TYPES = {0x29: {"name": "Bug", "behaviour": 24, "uses": 3, "examples": [[10, 4]]},
         0x4F: {"name": "Squadron", "behaviour": 70, "uses": 2, "examples": [[10, 2]]},
         0x1AD: {"name": "Gate", "behaviour": 7, "uses": 1, "examples": [[11, 7]]}}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        sample_project(Path(self.tmp.name))
        self.s = agent.Session()
        self.s._catalog_error = "tests have no catalog"  # never the player's ROM, even when one is found nearby
        self.s.open(Path(self.tmp.name) / "sample.jfgproj")
        mesh = FakeMesh()
        self.s._mesh = lambda name: mesh

    def tearDown(self):
        if self.old_config is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old_config
        self.tmp.cleanup()

    def with_catalog(self):
        self.s._catalog, self.s._catalog_error, self.s._types = "fake", None, TYPES

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
